package dev.pixel.voice;

/** 1C: the V3 return control's rules (ReturnModel), every phase and the correlated answer. Host JVM. */
public final class ReturnModelTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        ReturnModel.Target notes = new ReturnModel.Target("org.fossify.notes", "Notes", "task-42");
        ReturnModel.Target maps = new ReturnModel.Target("app.organicmaps", "Maps", "task-52");

        // Nothing to return to: nothing shown, never a guessed app.
        ReturnModel.State s = ReturnModel.initial();
        expect(!s.controlShown() && !s.sheetShown() && s.observed(null).phase == ReturnModel.Phase.HIDDEN, "no source: hidden");

        // A source: named; the accessible name is always the full name.
        s = s.observed(notes);
        expect(s.phase == ReturnModel.Phase.READY && s.controlShown(), "a source: ready");
        expect("Notes".equals(ReturnModel.text(s)) && "Return to Notes".equals(ReturnModel.spoken(s)), "V3 label and accessible name");
        expect(s.observed(notes) == s, "the same task is the same state");

        // A tap requests directly: no confirmation, no delay; pending ignores duplicates and keeps its name spoken.
        ReturnModel.State pending = s.tapped();
        expect(pending.phase == ReturnModel.Phase.RETURNING && pending.request == s.request + 1, "tap: requested at once");
        expect("Returning…".equals(ReturnModel.text(pending)) && "Return to Notes".equals(ReturnModel.spoken(pending)), "Returning… with the full name kept");
        expect(pending.tapped() == pending, "duplicate requests are ignored");
        expect(pending.controlShown() && !pending.sheetShown(), "Rose stays usable while pending");

        // Cancel invalidates the request: a late answer changes nothing; a new request gets a new id.
        ReturnModel.State cancelled = pending.cancelled();
        expect(cancelled.phase == ReturnModel.Phase.READY && cancelled.target == notes, "cancel: back to ready");
        expect(cancelled.answered(pending.request, ReturnModel.Answer.RETURNED) == cancelled, "late confirmation ignored");
        ReturnModel.State again = cancelled.tapped();
        expect(again.request == pending.request + 1, "a new request gets a new id");
        expect(again.answered(pending.request, ReturnModel.Answer.RETURNED) == again, "a mismatched answer is ignored");

        // Only the acknowledged exact-task return leaves Rose.
        expect(pending.answered(pending.request, ReturnModel.Answer.RETURNED).phase == ReturnModel.Phase.HIDDEN, "returned: Rose leaves");
        expect(s.answered(s.request, ReturnModel.Answer.RETURNED) == s, "no answer counts without a request");

        // Missing or replaced source: stay in Rose with the sheet; afterwards it is not offered again.
        ReturnModel.State gone = pending.answered(pending.request, ReturnModel.Answer.UNAVAILABLE);
        expect(gone.sheetShown() && !gone.controlShown(), "unavailable: the sheet, not the control");
        expect(gone.observed(maps) == gone, "the sheet holds while open");
        expect(gone.stayed().phase == ReturnModel.Phase.HIDDEN, "Stay: a gone source is not offered again");

        // A failed transfer: the same sheet; Stay keeps the control so it can be tried again.
        ReturnModel.State failed = pending.answered(pending.request, ReturnModel.Answer.REFUSED);
        expect(failed.phase == ReturnModel.Phase.FAILED && failed.sheetShown(), "failure: the sheet");
        expect(failed.stayed().phase == ReturnModel.Phase.READY && failed.stayed().target == notes, "Stay: try again later");

        // While idle a different task replaces the source; a pending request keeps its own.
        expect(s.observed(maps).target == maps, "a newer source replaces it");
        expect(s.observed(new ReturnModel.Target("org.fossify.notes", "Notes", "task-77")).target.token.equals("task-77"), "a replaced task is a new source");
        expect(pending.observed(maps) == pending && pending.observed(null) == pending, "a pending request keeps its source");
        expect(s.observed(null).phase == ReturnModel.Phase.HIDDEN, "no source while idle: hidden");

        // Names: cleaned; no name is invented ("Previous app" / "Return to previous app").
        expect("Evil".equals(ReturnModel.name("Ev\nil")) && ReturnModel.name("  ") == null && ReturnModel.name(null) == null, "names cleaned");
        ReturnModel.State unnamed = ReturnModel.initial().observed(new ReturnModel.Target("x.y", " ", "t"));
        expect("Previous app".equals(ReturnModel.text(unnamed)) && "Return to previous app".equals(ReturnModel.spoken(unnamed)), "unnamed source");
        String longName = "A very long application name that will not fit";
        expect(longName.equals(ReturnModel.name(longName)), "the full name is kept; the view truncates");

        // V3 motion constants.
        expect(ReturnModel.DEPART_MS == 200 && ReturnModel.DEPART_DP == 8, "200 ms, 8dp");

        System.out.println("ReturnModelTest: " + checks + " checks passed");
    }
}
