"""Make LLM text speakable: numbers, times, money, symbols, markdown.

The TTS reads digits inconsistently ("6:30" -> "6 30"), so everything is
spelled out the way a person would say it before synthesis.
"""

from __future__ import annotations

import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
        "nine": "ninth", "twelve": "twelfth"}


def number_words(n: int) -> str:
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + number_words(rest))
    for value, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= value:
            rest = n % value
            return (number_words(n // value) + " " + name
                    + ("" if rest == 0 else " " + number_words(rest)))
    return str(n)


def ordinal_words(n: int) -> str:
    words = number_words(n)
    head, _, last = words.rpartition("-") if "-" in words.split(" ")[-1] else words.rpartition(" ")
    sep = "-" if "-" in words.split(" ")[-1] else " "
    if last in _ORD:
        last = _ORD[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return (head + sep if head else "") + last


def year_words(n: int) -> str:
    if 2000 <= n <= 2009:
        return number_words(n)
    hi, lo = divmod(n, 100)
    if lo == 0:
        return number_words(hi) + " hundred"
    return number_words(hi) + " " + (("oh " + _ONES[lo]) if lo < 10 else number_words(lo))


def _time(m: re.Match) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    suffix = (m.group(3) or "").lower().replace(".", "").strip()
    if mi == 0:
        words = number_words(h) + ("" if suffix else " o'clock")
    elif mi < 10:
        words = number_words(h) + " oh " + _ONES[mi]
    else:
        words = number_words(h) + " " + number_words(mi)
    if suffix in ("am", "a"):
        words += " A M"
    elif suffix in ("pm", "p"):
        words += " P M"
    return words


def _money(m: re.Match) -> str:
    dollars = int(m.group(1).replace(",", ""))
    cents = int((m.group(2) or "0").ljust(2, "0")[:2])
    scale = m.group(3)
    parts = []
    if scale:
        return number_words(dollars) + " " + {"k": "thousand", "m": "million", "b": "billion"}[
            scale.lower()] + " dollars"
    if dollars or not cents:
        parts.append(number_words(dollars) + (" dollar" if dollars == 1 else " dollars"))
    if cents:
        parts.append(number_words(cents) + (" cent" if cents == 1 else " cents"))
    return " and ".join(parts)


def _decimal(m: re.Match) -> str:
    whole, frac = m.group(1), m.group(2)
    return number_words(int(whole.replace(",", ""))) + " point " + " ".join(_ONES[int(d)] for d in frac)


def _fraction(m: re.Match) -> str:
    return _fraction_words(int(m.group(1)), int(m.group(2)))


def _fraction_words(a: int, b: int) -> str:
    if b == 2:
        return number_words(a) + (" half" if a == 1 else " halves")
    if b == 4:
        return number_words(a) + (" quarter" if a == 1 else " quarters")
    den = ordinal_words(b)
    return number_words(a) + " " + (den if a == 1 else den + "s")


def _mixed(m: re.Match) -> str:
    a, b = m.group(2).split("/")
    return number_words(int(m.group(1))) + " and " + _fraction_words(int(a), int(b))


def _ordinal(m: re.Match) -> str:
    return ordinal_words(int(m.group(1)))


def _integer(m: re.Match) -> str:
    raw = m.group(0)
    n = int(raw.replace(",", ""))
    if "," not in raw and 1100 <= n <= 2099 and len(raw) == 4:
        return year_words(n)
    return number_words(n)


_MD = re.compile(r"(\*\*|__|`|#+\s|^\s*[-*•]\s+)", re.M)
_SYMBOLS = [("&", " and "), ("%", " percent"), ("+", " plus "), ("=", " equals "),
            ("°F", " degrees"), ("°C", " degrees Celsius"), ("°", " degrees"),
            ("w/", "with "), ("e.g.", "for example"), ("i.e.", "that is"), ("etc.", "et cetera"),
            ("approx.", "about"), ("vs.", "versus"), ("Dr.", "Doctor"), ("St.", "Street")]


def speakable(text: str) -> str:
    t = _MD.sub("", text)
    t = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", t)  # links
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)  # emoji
    t = re.sub(r"\b(\d{1,2}):(\d{2})(?:\s*([AaPp]\.?[Mm])\b)?", _time, t)
    t = re.sub(r"\b(\d{1,2})\s*([AaPp])\.?[Mm]\b",
               lambda m: number_words(int(m.group(1))) + (" A M" if m.group(2).lower() == "a" else " P M"), t)
    t = re.sub(r"\$(\d[\d,]*)(?:\.(\d{1,2}))?(?:\s*([kKmMbB])\b)?", _money, t)
    t = re.sub(r"\b(\d+)\s+(\d+/\d+)\b", _mixed, t)
    t = re.sub(r"\b(\d+)/(\d+)\b", _fraction, t)
    t = re.sub(r"\b(\d[\d,]*)\.(\d+)\b", _decimal, t)
    t = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", _ordinal, t)
    for a, b in _SYMBOLS:
        t = t.replace(a, b)
    t = re.sub(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+\b", _integer, t)
    t = re.sub(r"([.!?,;:])\s*—\s*", r"\1 ", t)
    t = t.replace("—", ", ").replace("–", " to ").replace("…", "...")
    t = re.sub(r"\s+", " ", t).strip()
    return t


if __name__ == "__main__":
    for s in ["Dinner at 6:30 PM with Sam.", "Alarm set for 6:05 am.", "You need 2 5/8 cups.",
              "It's 1.5 cups for 4 people.", "That costs $12.50, or $3k total.", "Meeting at 4pm.",
              "It was founded in 1998 and grew 25% in 2024.", "The 3rd of May.", "**Done!** — texted Sam."]:
        print(f"{s!r} -> {speakable(s)!r}")
