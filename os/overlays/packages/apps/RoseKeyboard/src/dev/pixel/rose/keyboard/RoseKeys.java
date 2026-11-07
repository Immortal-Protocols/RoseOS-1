/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.rose.keyboard;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * RoseOS (1M, F10): the Rose keyboard's text model, a line-for-line port of the selected design's
 * prototypes/rose-keyboard/src/keyboard.js (818a032), with no Android types. One editable field: text, selection [a, b]
 * (a == b is a caret), shift state, symbol page, suggestions, autocorrect with a one-step revert, word deletion,
 * clipboard and undo/redo. The IME (RoseKeyboardService) mirrors the text around the app's cursor into a State, runs
 * one operation and applies the difference to the app (FieldEdit); RoseKeysTest runs the prototype's model-check.cjs.
 */
public final class RoseKeys {
    private RoseKeys() {}

    // Common words, most frequent first (rank = weight). Small on purpose: the prototype's lexicon.
    static final List<String> WORDS = Arrays.asList(("the I to you and a it is that of in for on be have we are this my "
            + "me what with so at your just can do not was but if will all get up about out how now like know there "
            + "one when go see time good back think they then here more today tomorrow Saturday Sunday "
            + "morning afternoon evening tonight great thanks thank sounds perfect okay yes no maybe sure "
            + "meet meeting garden gate ten eleven noon walk river lunch coffee café later soon still "
            + "would could should want need going come coming leave left right where why who which "
            + "from been has had did does make made take took let lets love lovely nice happy hope "
            + "plan plans weekend week day days call calling talk talked message write wrote read "
            + "Jamie Rose notes list book books bring open opens close closed early late quiet quieter "
            + "there their they're don't can't won't I'm I'll I've you're that's it's "
            + "receive address separate definitely tomorrow's keyboard typing suggestion "
            + "home phone place places after before again also around away really very well work "
            + "hello hi hey way my OK on lunch dinner tea pack call text soon reply ready").trim().split("\\s+"));
    static final Map<String, Integer> RANK = new HashMap<>();
    static final Map<String, String> CANON = new HashMap<>();

    static {
        // JS `new Map(WORDS.map(...))`: a later duplicate overwrites an earlier one.
        for (int i = 0; i < WORDS.size(); i++) {
            String w = WORDS.get(i);
            RANK.put(lower(w), i);
            CANON.put(lower(w), w);
        }
    }

    /** Autocorrect only for unambiguous typos; anything else is left exactly as typed. */
    static final Map<String, String> CORRECT = map("teh", "the", "adn", "and", "dont", "don't", "im", "I'm",
            "ive", "I've", "youre", "you're", "thats", "that's", "recieve", "receive", "adress", "address",
            "tommorow", "tomorrow", "tomorow", "tomorrow", "seperate", "separate", "definately", "definitely",
            "i", "I", "jamie", "Jamie", "saturday", "Saturday", "sunday", "Sunday", "rose", "Rose");
    /** Valid words often meant as contractions: offered as the best suggestion, never applied automatically. */
    static final Map<String, String> SUGGEST = map("ill", "I'll", "cant", "can't", "wont", "won't", "well", "we'll",
            "were", "we're", "hell", "he'll", "shell", "she'll");
    /** Next-word predictions after a completed word (empty current word). */
    static final Map<String, List<String>> NEXT = new HashMap<>();

    static {
        NEXT.put("", Arrays.asList("I", "The", "Thanks"));
        NEXT.put("see", Arrays.asList("you", "the", "it"));
        NEXT.put("at", Arrays.asList("ten", "the", "noon"));
        NEXT.put("the", Arrays.asList("garden", "river", "gate"));
        NEXT.put("sounds", Arrays.asList("good", "great", "perfect"));
        NEXT.put("thank", Arrays.asList("you", "you!", "you so"));
        NEXT.put("i", Arrays.asList("think", "I'll", "can"));
        NEXT.put("i'll", Arrays.asList("be", "bring", "call"));
        NEXT.put("meet", Arrays.asList("at", "you", "there"));
        NEXT.put("on", Arrays.asList("Saturday", "Sunday", "the"));
        NEXT.put("by", Arrays.asList("the", "ten", "noon"));
        NEXT.put("good", Arrays.asList("morning", "idea", "plan"));
    }

    private static final Pattern SENT_END = Pattern.compile("[.!?]\\s+$");
    private static final Pattern NEWLINE_END = Pattern.compile("\\n\\s*$");
    private static final Pattern PUNCT = Pattern.compile("[.,!?;:]");
    private static final Pattern PREV_WORD = Pattern.compile("([\\p{L}\\p{N}'’]+)[^\\p{L}\\p{N}'’]*$");

    public static final String OFF = "off", ONCE = "once", LOCK = "lock";
    public static final String LETTERS = "letters", SYM1 = "sym1", SYM2 = "sym2";

    /** The last autocorrection, revertible by one backspace right after it. */
    static final class Auto {
        final String from, to;
        final int at;

        Auto(String from, String to, int at) {
            this.from = from;
            this.to = to;
            this.at = at;
        }
    }

    static final class Snap {
        final String text;
        final int a, b;

        Snap(String text, int a, int b) {
            this.text = text;
            this.a = a;
            this.b = b;
        }
    }

    /** One editable field's state (keyboard.js `initial`). */
    public static final class State {
        public String text;
        public int a, b;
        public boolean multiline;
        public String shift = OFF;
        public String page = LETTERS;
        final Deque<Snap> undo = new ArrayDeque<>();
        final Deque<Snap> redo = new ArrayDeque<>();
        public String clip = "";
        Auto lastAuto;
        String revertedWord;
        long lastShiftAt = -1_000_000_000L, lastSpaceAt = -1_000_000_000L;
        boolean typingRun;
        public final List<String> events = new ArrayList<>();
        /**
         * Device-only switches, both on by default as in the prototype: off for fields where they would corrupt the
         * text (passwords, e-mail addresses, URLs, numbers, fields that ask for no suggestions).
         */
        public boolean autoCapOn = true, correctOn = true;
    }

    /** A suggestion: its text and kind (typed, correct, suggest or predict). */
    public static final class Suggestion {
        public final String text, kind;

        Suggestion(String text, String kind) {
            this.text = text;
            this.kind = kind;
        }

        @Override
        public String toString() {
            return kind + ":" + text;
        }
    }

    public static State initial(String text, boolean multiline) {
        State s = new State();
        s.text = text == null ? "" : text;
        s.a = s.b = s.text.length();
        s.multiline = multiline;
        autoCap(s);
        return s;
    }

    public static State initial() {
        return initial("", false);
    }

    // ---------- helpers ----------

    static boolean wordCh(char c) {
        return Character.isLetterOrDigit(c) || c == '\'' || c == '’';
    }

    static boolean wordCh(String s) {
        return s.length() > 0 && wordCh(s.charAt(0));
    }

    private static String lower(String s) {
        return s.toLowerCase(Locale.ROOT);
    }

    private static Map<String, String> map(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put(kv[i], kv[i + 1]);
        return m;
    }

    private static void pushUndo(State s, boolean coalesce) {
        if (coalesce && s.typingRun) return;
        s.undo.push(new Snap(s.text, s.a, s.b));
        if (s.undo.size() > 100) s.undo.removeLast();
        s.redo.clear();
        s.typingRun = coalesce;
    }

    static int lo(State s) {
        return Math.min(s.a, s.b);
    }

    static int hi(State s) {
        return Math.max(s.a, s.b);
    }

    private static void replace(State s, int from, int to, String str) {
        s.text = s.text.substring(0, from) + str + s.text.substring(to);
        s.a = s.b = from + str.length();
    }

    /** The word being typed: word characters immediately before the caret, as {start, end}. */
    public static int[] currentWordRange(State s) {
        if (s.a != s.b) return new int[] {s.a, s.a};
        int i = s.a;
        while (i > 0 && wordCh(s.text.charAt(i - 1))) i--;
        return new int[] {i, s.a};
    }

    public static String currentWord(State s) {
        int[] r = currentWordRange(s);
        return s.text.substring(r[0], r[1]);
    }

    private static String prevWord(State s, int before) {
        String t = s.text.substring(0, before).replaceAll("\\s+$", "");
        Matcher m = PREV_WORD.matcher(t);
        return m.find() ? lower(m.group(1)) : "";
    }

    static void autoCap(State s) {
        if (LOCK.equals(s.shift)) return;
        if (!s.autoCapOn) {
            s.shift = OFF;
            return;
        }
        String before = s.text.substring(0, lo(s));
        s.shift = before.isEmpty() || SENT_END.matcher(before).find() || NEWLINE_END.matcher(before).find() ? ONCE : OFF;
    }

    private static boolean hasUpperAscii(String s) {
        for (int i = 0; i < s.length(); i++) if (s.charAt(i) >= 'A' && s.charAt(i) <= 'Z') return true;
        return false;
    }

    static String matchCase(String word, String typed) {
        if (typed.length() > 1 && typed.equals(typed.toUpperCase(Locale.ROOT)) && hasUpperAscii(typed)) {
            return word.toUpperCase(Locale.ROOT);
        }
        if (typed.length() > 0) {
            String first = typed.substring(0, 1);
            if (first.equals(first.toUpperCase(Locale.ROOT)) && hasUpperAscii(first)) {
                return word.substring(0, 1).toUpperCase(Locale.ROOT) + word.substring(1);
            }
        }
        return word;
    }

    private static void log(State s, String e) {
        s.events.add(e);
        if (s.events.size() > 40) s.events.remove(0);
    }

    private static boolean isAsciiLower(String ch) {
        return ch.length() == 1 && ch.charAt(0) >= 'a' && ch.charAt(0) <= 'z';
    }

    // ---------- typing ----------

    /** Types one key's text (a letter, digit or symbol). */
    public static void type(State s, String ch) {
        String up = !OFF.equals(s.shift) && isAsciiLower(ch) ? ch.toUpperCase(Locale.ROOT) : ch;
        pushUndo(s, wordCh(up));
        if (PUNCT.matcher(up).matches() && s.a == s.b) correctCurrent(s);
        replace(s, lo(s), hi(s), up);
        s.lastAuto = null;
        if (ONCE.equals(s.shift)) s.shift = OFF;
        if (!wordCh(up)) {
            s.typingRun = false;
            autoCap(s);
        }
        log(s, "type " + up);
    }

    /** Applies an unambiguous correction to the word before the caret (space, Return, punctuation). */
    static String[] correctCurrent(State s) {
        int[] r = currentWordRange(s);
        String word = s.text.substring(r[0], r[1]);
        String fix = word.isEmpty() || !s.correctOn ? null : CORRECT.get(lower(word));
        if (fix == null || fix.equals(word) || word.equals(s.revertedWord)) return null;
        String to = word.length() == 1 ? fix : matchCase(fix, word);
        replace(s, r[0], r[1], to);
        log(s, "autocorrect " + word + "→" + to);
        return new String[] {word, to};
    }

    /** The space key at time now (ms): a double tap after a word ends the sentence with ". ". */
    public static void space(State s, long now) {
        if (s.a == s.b && now - s.lastSpaceAt < 450 && s.text.substring(0, s.a).endsWith(" ") && s.a >= 2
                && wordCh(s.text.charAt(s.a - 2))) {
            pushUndo(s, false);
            replace(s, s.a - 1, s.a, ". ");
            s.lastSpaceAt = -1_000_000_000L;
            s.lastAuto = null;
            autoCap(s);
            log(s, "period");
            return;
        }
        pushUndo(s, false);
        String[] fixed = s.a == s.b ? correctCurrent(s) : null;
        replace(s, lo(s), hi(s), " ");
        s.lastAuto = fixed != null ? new Auto(fixed[0], fixed[1], s.a) : null;
        if (fixed == null) log(s, "space");
        s.lastSpaceAt = now;
        s.typingRun = false;
        autoCap(s);
    }

    public static void backspace(State s) {
        // Right after an autocorrect, backspace restores exactly what was typed.
        if (s.lastAuto != null && s.a == s.b && s.a == s.lastAuto.at) {
            String from = s.lastAuto.from, to = s.lastAuto.to;
            pushUndo(s, false);
            replace(s, s.a - to.length() - 1, s.a, from);
            s.lastAuto = null;
            s.revertedWord = from;
            log(s, "revert " + to + "→" + from);
            autoCap(s);
            return;
        }
        s.lastAuto = null;
        if (s.a != s.b) {
            pushUndo(s, false);
            replace(s, lo(s), hi(s), "");
            log(s, "delete selection");
            autoCap(s);
            return;
        }
        if (s.a == 0) return;
        pushUndo(s, true);
        // A whole surrogate pair (an emoji) goes at once.
        int n = s.a >= 2 && Character.isLowSurrogate(s.text.charAt(s.a - 1))
                && Character.isHighSurrogate(s.text.charAt(s.a - 2)) ? 2 : 1;
        replace(s, s.a - n, s.a, "");
        autoCap(s);
        log(s, "backspace");
    }

    public static void deleteWord(State s) {
        s.lastAuto = null;
        if (s.a != s.b) {
            backspace(s);
            return;
        }
        int i = s.a;
        while (i > 0 && Character.isWhitespace(s.text.charAt(i - 1))) i--;
        if (i > 0 && wordCh(s.text.charAt(i - 1))) {
            while (i > 0 && wordCh(s.text.charAt(i - 1))) i--;
        } else if (i > 0) {
            i--;
        }
        if (i == s.a) return;
        pushUndo(s, false);
        replace(s, i, s.a, "");
        autoCap(s);
        log(s, "delete word");
    }

    /**
     * Return: a newline in multi-line fields. In single-purpose fields the IME action fires: the current word is
     * corrected and the trimmed text is returned (or null when empty) and cleared, as the prototype models a send.
     */
    public static String enter(State s) {
        if (s.multiline) {
            pushUndo(s, false);
            if (s.a == s.b) correctCurrent(s);
            replace(s, lo(s), hi(s), "\n");
            s.lastAuto = null;
            s.typingRun = false;
            autoCap(s);
            log(s, "newline");
            return null;
        }
        if (s.a == s.b) correctCurrent(s);
        String sent = s.text.trim();
        if (sent.isEmpty()) return null;
        pushUndo(s, false);
        s.text = "";
        s.a = s.b = 0;
        s.lastAuto = null;
        autoCap(s);
        log(s, "send");
        return sent;
    }

    /** The action key in a single-purpose field, on the device: only the correction; the app handles the action. */
    public static void beforeAction(State s) {
        if (s.a == s.b) correctCurrent(s);
        s.lastAuto = null;
    }

    // ---------- shift & pages ----------

    public static void shiftTap(State s, long now) {
        if (!LETTERS.equals(s.page)) return;
        if (LOCK.equals(s.shift)) s.shift = OFF;
        else if (ONCE.equals(s.shift) && now - s.lastShiftAt < 350) s.shift = LOCK;
        else s.shift = OFF.equals(s.shift) ? ONCE : OFF;
        s.lastShiftAt = now;
        log(s, "shift " + s.shift);
    }

    public static void setPage(State s, String page) {
        s.page = page;
        log(s, "page " + page);
        if (LETTERS.equals(page)) autoCap(s);
    }

    // ---------- suggestions ----------

    private static String capFirst(String w) {
        return w.isEmpty() ? w : w.substring(0, 1).toUpperCase(Locale.ROOT) + w.substring(1);
    }

    /**
     * Three slots. While typing: [typed-as-is, best, next]; best is a correction when one applies. Between words:
     * next-word predictions. A just-reverted word is never re-corrected.
     */
    public static List<Suggestion> suggestions(State s) {
        List<Suggestion> out = new ArrayList<>();
        if (s.a != s.b || !s.correctOn) return out;
        String word = currentWord(s);
        if (word.isEmpty()) {
            List<String> p = NEXT.get(prevWord(s, s.a));
            if (p == null) p = s.text.trim().isEmpty() ? NEXT.get("") : Arrays.asList("the", "and", "I");
            boolean cap = !OFF.equals(s.shift);
            for (int i = 0; i < Math.min(3, p.size()); i++) {
                out.add(new Suggestion(cap ? capFirst(p.get(i)) : p.get(i), "predict"));
            }
            return out;
        }
        String low = lower(word);
        String fix = CORRECT.containsKey(low) && !word.equals(s.revertedWord) ? matchCase(CORRECT.get(low), word) : null;
        List<String> pre = new ArrayList<>();
        for (String w : WORDS) {
            if (lower(w).startsWith(low) && !lower(w).equals(low)) pre.add(w);
        }
        pre.sort((x, y) -> RANK.get(lower(x)) - RANK.get(lower(y)));
        for (int i = 0; i < pre.size(); i++) pre.set(i, matchCase(CANON.get(lower(pre.get(i))), word));
        String exact = CANON.containsKey(low) ? matchCase(CANON.get(low), word) : null;
        String alt = fix == null && SUGGEST.containsKey(low) ? ("ill".equals(low) ? "I'll" : matchCase(SUGGEST.get(low), word))
                : null;
        String best = fix != null ? fix : alt != null ? alt : exact != null ? exact : pre.isEmpty() ? null : pre.get(0);
        out.add(new Suggestion(word, "typed"));
        if (best != null && !best.equals(word)) out.add(new Suggestion(best, fix != null ? "correct" : "suggest"));
        for (String w : pre) {
            if (out.size() >= 3) break;
            boolean seen = false;
            for (Suggestion o : out) seen |= o.text.equals(w);
            if (!seen) out.add(new Suggestion(w, "suggest"));
        }
        return out;
    }

    public static void pick(State s, String word) {
        int[] r = currentWordRange(s);
        pushUndo(s, false);
        replace(s, r[0], r[1], word + " ");
        s.lastAuto = null;
        s.revertedWord = null;
        s.typingRun = false;
        autoCap(s);
        log(s, "pick " + word);
    }

    // ---------- cursor, selection, clipboard, undo ----------

    public static void setSel(State s, int a, int b) {
        int n = s.text.length();
        s.a = Math.max(0, Math.min(n, a));
        s.b = Math.max(0, Math.min(n, b));
        s.lastAuto = null;
        s.typingRun = false;
        s.revertedWord = null;
        autoCap(s);
    }

    public static void setSel(State s, int a) {
        setSel(s, a, a);
    }

    public static void move(State s, int delta, boolean extend) {
        if (extend) setSel(s, s.a, s.b + delta);
        else if (s.a != s.b) setSel(s, delta < 0 ? lo(s) : hi(s));
        else setSel(s, s.a + delta);
    }

    public static void selectWord(State s, int at) {
        int i = Math.max(0, Math.min(s.text.length(), at)), j = i;
        while (i > 0 && wordCh(s.text.charAt(i - 1))) i--;
        while (j < s.text.length() && wordCh(s.text.charAt(j))) j++;
        if (i == j && j < s.text.length()) j++;
        setSel(s, i, j);
        log(s, "select word");
    }

    public static void selectAll(State s) {
        setSel(s, 0, s.text.length());
        log(s, "select all");
    }

    static String selected(State s) {
        return s.text.substring(lo(s), hi(s));
    }

    public static void copy(State s) {
        if (s.a != s.b) {
            s.clip = selected(s);
            log(s, "copy");
        }
    }

    public static void cut(State s) {
        if (s.a != s.b) {
            s.clip = selected(s);
            pushUndo(s, false);
            replace(s, lo(s), hi(s), "");
            autoCap(s);
            log(s, "cut");
        }
    }

    public static void paste(State s) {
        if (!s.clip.isEmpty()) {
            pushUndo(s, false);
            replace(s, lo(s), hi(s), s.clip);
            s.lastAuto = null;
            autoCap(s);
            log(s, "paste");
        }
    }

    private static void restore(State s, Snap u) {
        s.text = u.text;
        s.a = u.a;
        s.b = u.b;
    }

    public static void undo(State s) {
        Snap u = s.undo.poll();
        if (u == null) return;
        s.redo.push(new Snap(s.text, s.a, s.b));
        restore(s, u);
        s.lastAuto = null;
        s.typingRun = false;
        autoCap(s);
        log(s, "undo");
    }

    public static void redo(State s) {
        Snap r = s.redo.poll();
        if (r == null) return;
        s.undo.push(new Snap(s.text, s.a, s.b));
        restore(s, r);
        s.lastAuto = null;
        s.typingRun = false;
        autoCap(s);
        log(s, "redo");
    }

    // ---------- layouts ----------

    /** Key ids: "shift", "bksp", "?123", "=\\<", "ABC", "space", "action"; anything else types itself. */
    public static final Map<String, List<List<String>>> LAYOUTS = new HashMap<>();

    private static List<String> row(String spaced) {
        return Arrays.asList(spaced.split(" "));
    }

    private static List<String> row(String first, String spaced, String last) {
        List<String> r = new ArrayList<>();
        if (first != null) r.add(first);
        r.addAll(row(spaced));
        if (last != null) r.add(last);
        return r;
    }

    static {
        LAYOUTS.put(LETTERS, Arrays.asList(row("q w e r t y u i o p"), row("a s d f g h j k l"),
                row("shift", "z x c v b n m", "bksp"), row("?123 , space . action")));
        LAYOUTS.put(SYM1, Arrays.asList(row("1 2 3 4 5 6 7 8 9 0"), row("@ # $ _ & - + ( ) /"),
                row("=\\<", "* \" ' : ; ! ?", "bksp"), row("ABC , space . action")));
        LAYOUTS.put(SYM2, Arrays.asList(row("~ ` | • √ π ÷ × ¶ ∆"), row("£ ¢ € ¥ ^ ° = { } \\"),
                row("?123", "% © ® ™ ✓ [ ]", "bksp"), row("ABC < space > action")));
    }
}
