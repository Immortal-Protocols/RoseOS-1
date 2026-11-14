package dev.pixel.rose.keyboard;

import java.util.List;

/**
 * 1M: the Rose keyboard's model (RoseKeys, ported from prototypes/rose-keyboard/src/keyboard.js at 818a032) against
 * the selected design's own executable specification, prototypes/rose-keyboard/model-check.cjs: the same 37 checks
 * in the same order with the same inputs and times. Then FieldEdit, which applies a model step to the app's field.
 */
public final class RoseKeysTest {
    static int pass, fail;

    static void check(String name, boolean ok, Object detail) {
        if (ok) pass++;
        else fail++;
        System.out.println((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  " + detail));
    }

    static void check(String name, boolean ok) {
        check(name, ok, "");
    }

    /** model-check.cjs typeStr: each character a second apart; ' ' is the space key. */
    static long typeStr(RoseKeys.State s, String str, long t0) {
        long t = t0;
        for (int i = 0; i < str.length(); ) {
            int cp = str.codePointAt(i);
            String ch = new String(Character.toChars(cp));
            i += Character.charCount(cp);
            t += 1000;
            if (" ".equals(ch)) RoseKeys.space(s, t);
            else RoseKeys.type(s, ch);
        }
        return t;
    }

    static long typeStr(RoseKeys.State s, String str) {
        return typeStr(s, str, 0);
    }

    static String sel(RoseKeys.State s) {
        return s.text.substring(Math.min(s.a, s.b), Math.max(s.a, s.b));
    }

    static boolean anyKind(List<RoseKeys.Suggestion> sg, String kind) {
        for (RoseKeys.Suggestion x : sg) if (x.kind.equals(kind)) return true;
        return false;
    }

    static boolean anyText(List<RoseKeys.Suggestion> sg, String text) {
        for (RoseKeys.Suggestion x : sg) if (x.text.equals(text)) return true;
        return false;
    }

    public static void main(String[] args) {
        RoseKeys.State s = RoseKeys.initial();
        check("starts with auto-capitalised shift", s.shift.equals("once"));
        typeStr(s, "hello");
        check("first letter capitalised, shift releases", s.text.equals("Hello") && s.shift.equals("off"), s.text);
        RoseKeys.space(s, 10);
        RoseKeys.space(s, 200);
        check("double space ends a sentence", s.text.equals("Hello. ") && s.shift.equals("once"), s.text);
        typeStr(s, "teh ", 1_000_000);
        check("autocorrect on space (case kept)", s.text.equals("Hello. The "), s.text);
        RoseKeys.backspace(s);
        check("backspace right after autocorrect restores what was typed", s.text.equals("Hello. Teh"), s.text);
        check("reverted word is not re-suggested as a correction", !anyKind(RoseKeys.suggestions(s), "correct"),
                RoseKeys.suggestions(s));
        RoseKeys.deleteWord(s);
        check("delete word", s.text.equals("Hello. "), s.text);

        s = RoseKeys.initial();
        RoseKeys.shiftTap(s, 0);
        check("tapping auto-shift turns it off", s.shift.equals("off"));
        RoseKeys.shiftTap(s, 1000);
        RoseKeys.shiftTap(s, 1200);
        check("double-tap shift locks caps", s.shift.equals("lock"));
        typeStr(s, "ok");
        check("caps lock stays on", s.text.equals("OK") && s.shift.equals("lock"), s.text);
        RoseKeys.shiftTap(s, 5000);
        check("tap shift releases caps lock", s.shift.equals("off"));

        s = RoseKeys.initial();
        typeStr(s, "see you at th");
        List<RoseKeys.Suggestion> sg = RoseKeys.suggestions(s);
        check("three suggestions, typed word first, best in the middle",
                sg.size() == 3 && sg.get(0).text.equals("th") && sg.get(1).text.equals("the"), sg);
        RoseKeys.pick(s, sg.get(1).text);
        check("picking a suggestion replaces the word and adds a space", s.text.equals("See you at the "), s.text);
        sg = RoseKeys.suggestions(s);
        check("next-word predictions between words",
                sg.size() == 3 && sg.get(0).kind.equals("predict") && anyText(sg, "garden"), sg);
        s = RoseKeys.initial();
        typeStr(s, "dont");
        sg = RoseKeys.suggestions(s);
        check("correction offered in the middle slot",
                sg.size() > 1 && sg.get(1).kind.equals("correct") && sg.get(1).text.equals("Don't"), sg);

        s = RoseKeys.initial("Garden at ten", false);
        RoseKeys.selectWord(s, 8);
        check("select word", sel(s).equals("at"));
        RoseKeys.cut(s);
        check("cut", s.text.equals("Garden  ten") && s.clip.equals("at"), s.text);
        RoseKeys.move(s, 1, false);
        RoseKeys.paste(s);
        check("paste at caret", s.text.equals("Garden  atten"), s.text);
        RoseKeys.undo(s);
        check("undo paste", s.text.equals("Garden  ten"), s.text);
        RoseKeys.undo(s);
        check("undo cut", s.text.equals("Garden at ten"), s.text);
        RoseKeys.redo(s);
        check("redo", s.text.equals("Garden  ten"), s.text);
        RoseKeys.selectAll(s);
        RoseKeys.type(s, "x");
        check("typing replaces selection", s.text.equalsIgnoreCase("x"), s.text);

        s = RoseKeys.initial();
        typeStr(s, "abc");
        RoseKeys.undo(s);
        check("typing a word undoes as one step", s.text.isEmpty(), s.text);
        s = RoseKeys.initial();
        typeStr(s, "hi there");
        String sent = RoseKeys.enter(s);
        check("single-line Return sends and clears", "Hi there".equals(sent) && s.text.isEmpty(), s.text);
        s = RoseKeys.initial("", true);
        typeStr(s, "list");
        RoseKeys.enter(s);
        typeStr(s, "milk");
        check("multi-line Return inserts a newline and capitalises the next line", s.text.equals("List\nMilk"), s.text);
        s = RoseKeys.initial();
        RoseKeys.setPage(s, "sym1");
        RoseKeys.type(s, "1");
        RoseKeys.setPage(s, "sym2");
        RoseKeys.type(s, "€");
        RoseKeys.setPage(s, "letters");
        check("symbol pages type and return to letters", s.text.equals("1€") && s.page.equals("letters"), s.text);
        boolean layouts = true;
        for (String p : new String[] {"letters", "sym1", "sym2"}) {
            layouts &= RoseKeys.LAYOUTS.get(p).size() == 4 && RoseKeys.LAYOUTS.get(p).get(0).size() == 10;
        }
        check("layouts: 4 rows each, 10 keys on top", layouts);
        s = RoseKeys.initial("ab", false);
        RoseKeys.setSel(s, 1);
        RoseKeys.move(s, 1, true);
        check("extend selection", s.a == 1 && s.b == 2);
        s = RoseKeys.initial("hi 👋", false);
        RoseKeys.backspace(s);
        check("backspace removes a whole emoji", s.text.equals("hi "), s.text);
        s = RoseKeys.initial();
        typeStr(s, "i dont know");
        RoseKeys.type(s, ".");
        check("corrections also apply before punctuation", s.text.equals("I don't know."), s.text);
        s = RoseKeys.initial("", true);
        typeStr(s, "call jamie");
        RoseKeys.enter(s);
        check("corrections also apply on Return", s.text.equals("Call Jamie\n"), s.text);
        String[][] alts = {{"ill", "I'll"}, {"cant", "can't"}, {"wont", "won't"}};
        for (String[] pair : alts) {
            String w = pair[0], alt = pair[1];
            s = RoseKeys.initial();
            typeStr(s, "so " + w + " ");
            check("\"" + w + "\" is a valid word: never auto-corrected", s.text.equals("So " + w + " "), s.text);
            s = RoseKeys.initial();
            typeStr(s, "so " + w);
            List<RoseKeys.Suggestion> sg2 = RoseKeys.suggestions(s);
            check("\"" + w + "\" offers \"" + alt + "\" as the best suggestion (not a correction)",
                    sg2.size() > 1 && sg2.get(1).text.equals(alt) && sg2.get(1).kind.equals("suggest"), sg2);
        }
        int modelChecks = pass + fail;
        check("the same 37 checks as model-check.cjs", modelChecks == 37, modelChecks);

        // FieldEdit: relative to the cursor, the caret landing where the model put it.
        FieldEdit e = FieldEdit.diff("see you at teh", 14, 14, "see you at the ", 15);
        check("edit: autocorrect replaces the word before the caret", e.before == 2 && e.after == 0
                && e.text.equals("he "), e.before + "/" + e.after + " '" + e.text + "'");
        e = FieldEdit.diff("hi \uD83D\uDC4B", 5, 5, "hi ", 3);
        check("edit: an emoji is deleted whole", e.before == 2 && e.after == 0 && e.text.isEmpty());
        e = FieldEdit.diff("\uD83D\uDC4B\uD83D\uDC4D", 4, 4, "\uD83D\uDC4B\uD83D\uDE00", 4);
        check("edit: never splits a surrogate pair", e.before == 2 && e.text.equals("\uD83D\uDE00"), e.before);
        e = FieldEdit.diff("abc", 1, 2, "abc", 2);
        check("edit: nothing to change", e.none() || (e.before == 0 && e.after == 0), e.before + "/" + e.after);
        e = FieldEdit.diff("aaa", 2, 2, "aa", 1);
        check("edit: backspace among repeats deletes before the caret", e.before == 1 && e.after == 0
                && e.text.isEmpty(), e.before + "/" + e.after);
        e = FieldEdit.diff("aa", 1, 1, "aaa", 2);
        check("edit: typing among repeats inserts at the caret", e.before == 0 && e.after == 0
                && e.text.equals("a"), e.before + "/" + e.after + " '" + e.text + "'");
        e = FieldEdit.diff("Garden at ten", 7, 9, "Garden x ten", 8);
        check("edit: typing replaces the selection", e.before == 0 && e.after == 0 && e.text.equals("x"),
                e.before + "/" + e.after + " '" + e.text + "'");

        System.out.println(pass + " checks passed" + (fail > 0 ? ", " + fail + " FAILED" : ""));
        if (fail > 0) System.exit(1);
    }
}
