"""Rose's look_at_screen with the real brain: does she read the screen, and how fast?

    ROSE_SHOTS=/path/to/shots ROSE_GPU_LOCK=/path/to/gpu.lock \\
        .venv/bin/python -m rose.tests.test_talker_look

Loads the brain (~21 GB) while holding ``$ROSE_GPU_LOCK`` (see test_vision).
The conductor runs with test_conductor's fake speaker, listener and tasks and
a fake phone whose ``look()`` returns ``$ROSE_SHOTS/com.anthropic.claude.png``
(a private Galaxy Fold screenshot of the Claude app's side menu; it is only
read, never written or printed). The menu's text comes from the uiautomator
dump next to it (``com.anthropic.claude.xml``).

Turns:
  1. "What do you see on the screen?"  she must look (one screenshot) and name
     at least three real menu items, and nothing made up.
  2. "Is there a Code button?"          yes, answered from the screenshot still
     in her cache (no new screenshot).
  3. "Look again: what's on the screen now?"  a second look (one screenshot),
     for the latency with warm kernels.
Reports the latency from the end of the turn to "Let me look." (first audio
text), to the screenshot being in her cache, and to the first words of the
answer (audio-ready text). Exit code 0 only if every check passed.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.brain import Brain
from rose.bus import Bus
from rose.conductor import Conductor
from rose.runtime import Runtime
from rose.tests.test_conductor import FakeListener, FakeSpeaker, FakeTasks
from rose.tests.test_vision import gpu_lock

MENU = ["Projects", "Code", "Artifacts", "Scheduled", "Dispatch", "Recents", "New session"]
MADE_UP = ["hamburger", "Home", "Profile", "Help", "Settings", "Inbox", "Search bar"]


class ShotPhone:
    """The fake Phone's data (contacts, calendar...) with a real screenshot as its screen."""

    def __init__(self, image):
        from rose.phone import Phone

        self.inner, self.image, self.looks = Phone(), image, 0
        self.data = self.inner.data

    def summary(self):
        return self.inner.summary()

    def look(self, view=None):
        self.looks += 1
        time.sleep(0.25)  # about what the real phone's screenshot takes over the link
        return self.image


def screen_words(xml_path: str) -> set[str]:
    words = set()
    for node in ET.parse(xml_path).iter("node"):
        for key in ("text", "content-desc"):
            words.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z']+", node.get(key, "")))
    return words


def main() -> None:
    from PIL import Image

    shots = os.environ["ROSE_SHOTS"]
    image = Image.open(os.path.join(shots, "com.anthropic.claude.png"))
    image.load()
    vocab = screen_words(os.path.join(shots, "com.anthropic.claude.xml"))

    with gpu_lock():
        bus, log = Bus(), []
        bus.subscribe(lambda m: log.append(dict(m, t_recv=time.monotonic())) if m["kind"] != "status" else None)
        rt = Runtime()
        t = time.monotonic()
        brain = rt.run(lambda: Brain(), timeout=600)
        print(f"brain loaded in {time.monotonic() - t:.0f} s", flush=True)
        phone, tasks = ShotPhone(image), FakeTasks()
        c = Conductor(rt, brain, FakeListener(), FakeSpeaker(), tasks, phone, bus)
        tasks.on_done = c.on_task_done
        c.start()

        def run_turn(text: str) -> dict:
            log.clear()
            looks = phone.looks
            t0 = time.monotonic()
            c.inject_text_turn(text)
            end = t0 + 60
            while time.monotonic() < end:
                r = c.reply
                if r is not None and r.user_text == text and not r.generating and r.utterance is not None:
                    break
                time.sleep(0.02)
            time.sleep(0.3)
            r = c.reply
            said = " ".join(r.speech.split())
            u = r.utterance
            if u is not None:  # heard in full: the next turn must not interrupt it
                u._played = len(u.text)
                u.done.set()
            time.sleep(0.3)
            look = next((m for m in log if m["kind"] == "look"), None)
            answer = next((m for m in log if m["kind"] == "look_answer"), None)
            first = round((r.t_first_speech - t0) * 1000) if r.t_first_speech else None
            out = {"text": text, "said": said, "screenshots": phone.looks - looks, "first_text_ms": first,
                   "look": {k: v for k, v in (look or {}).items() if k not in ("kind", "t", "t_recv")},
                   "answer_ms": answer["ms"] if answer else None,
                   "answer_after_look_ms": answer["after_look_ms"] if answer else None,
                   "warnings": [m["message"] for m in log if m["kind"] in ("warning", "error")],
                   "tools": [m["name"] for m in log if m["kind"] == "tool_call"]}
            print(out, flush=True)
            return out

        bad = []
        one = run_turn("What do you see on the screen?")
        named = [w for w in MENU if re.search(rf"\b{w}\b", one["said"], re.I)]
        made_up = [w for w in MADE_UP if re.search(rf"\b{w}\b", one["said"], re.I)]
        if one["screenshots"] != 1 or "look_at_screen" not in one["tools"]:
            bad.append(f"turn 1 did not look once: {one['screenshots']} screenshots, tools {one['tools']}")
        if len(named) < 3:
            bad.append(f"turn 1 named only {named} of the menu")
        if made_up:
            bad.append(f"turn 1 made up {made_up}")
        # Every capitalised word she says that is not an ordinary word must be on the screen.
        ordinary = {"i", "it", "it's", "its", "the", "there", "there's", "you", "your", "you're", "yes", "no", "a",
                    "an", "and", "at", "on", "in", "with", "this", "that", "let", "me", "look", "claude", "app",
                    "menu", "screen", "they", "it", "below", "above", "under", "plus", "also", "some", "of",
                    "for", "to", "from", "is", "are", "here", "near", "my", "options", "like", "recent", "chats"}
        unknown = sorted({w for w in re.findall(r"\b[A-Z][A-Za-z']+", one["said"])
                          if w.lower() not in vocab and w.lower() not in ordinary})
        print("capitalised words not in the screen's text:", unknown)

        two = run_turn("Is there a Code button?")
        if not re.match(r"\W*(yes|yeah|yep|there is|there's)\b", two["said"], re.I) or "code" not in two["said"].lower():
            bad.append(f"turn 2 did not say yes about Code: {two['said']!r}")
        if two["screenshots"]:
            bad.append(f"turn 2 took {two['screenshots']} new screenshots (the first is still in her cache)")
        made_up = [w for w in MADE_UP if re.search(rf"\b{w}\b", two["said"], re.I)]
        if made_up:
            bad.append(f"turn 2 made up {made_up}")

        # A second look (warm kernels): the latency a look has after the first one.
        three = run_turn("Look again: what's on the screen now?")
        if three["screenshots"] != 1:
            bad.append(f"turn 3 ('look again') took {three['screenshots']} screenshots, not 1")
        made_up = [w for w in MADE_UP if re.search(rf"\b{w}\b", three["said"], re.I)]
        if made_up:
            bad.append(f"turn 3 made up {made_up}")

        print("\ntalker cache tokens:", len(c.talker.tokens), "image tokens:", c.talker.image_tokens,
              "next position:", c.talker.pos)
        print("runtime:", rt.stats())
        c.stop()
        rt.stop()
    if bad:
        print("FAILED:\n  " + "\n  ".join(bad))
        raise SystemExit(1)
    print("talker look checks passed")


if __name__ == "__main__":
    main()
