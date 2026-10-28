package dev.pixel.voice;

/** Snapshots below follow server/launcher-task.mjs exactly: start() sets title 'Working on your request',
 * state running and empty progress/activity/result; activity()/progress()/result() fill those fields while
 * accepting; checking() sets 'checking'; stopping() clears activity; finish() sets the terminal state,
 * clears activity and may set result; a restart finishes 'interrupted' with a fixed explanation. */
public final class TaskPresentationTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static TaskPresentation p(String state, String title, String activity, String progress, String result) {
        return TaskPresentation.of(true, "t1", state, title, activity, progress, result, true);
    }

    public static void main(String[] args) {
        // Empty: {version:1,current:null} presents nothing at all.
        TaskPresentation empty = TaskPresentation.of(false, null, null, null, null, null, null, true);
        expect(!empty.visible && empty.title.isEmpty() && empty.body.isEmpty(), "No task: nothing shown");
        expect(!TaskPresentation.of(true, "", "running", "x", "", "", "", true).visible, "A task without id is not shown");

        // Running, right after start(): a quiet title, no invented progress text.
        TaskPresentation started = p("running", "Working on your request", "", "", "");
        expect(started.visible && started.eyebrow.equals("Rose") && started.body.isEmpty() && started.active(),
                "Just started: no filler body");
        // Calculator request after setTitle() and the worker's activity()/progress() updates.
        TaskPresentation working = p("running", "Calculate eighteen percent tip", "Using Calculator", "", "");
        expect(working.title.equals("Calculate eighteen percent tip") && working.body.equals("Using Calculator"),
                "Activity is the live progress line");
        expect(p("running", "Calculate eighteen percent tip", "", "Computing 240 × 0.18", "").body.equals("Computing 240 × 0.18"),
                "Progress is used when there is no activity");
        expect(p("checking", "Calculate eighteen percent tip", "", "", "43.2").body.equals("Checking the result before finishing."),
                "Checking states what is happening, not the unchecked result");

        // Completed: the actual result, unchanged, tied to the same task id.
        TaskPresentation done = p("completed", "Calculate eighteen percent tip", "", "", "18% of 240 is **43.2**.");
        expect(done.eyebrow.equals("For you") && done.body.equals("18% of 240 is **43.2**.") && done.id.equals("t1") && !done.active(),
                "Completion shows the real result for the same task");
        expect(p("completed", "Calculate eighteen percent tip", "", "", "").body.isEmpty(), "Empty result stays quiet");

        // Failure and stop are honest, never a success card.
        TaskPresentation failed = p("failed", "Calculate eighteen percent tip", "", "", "Calculator was not available.");
        expect(failed.eyebrow.equals("Failed") && failed.body.equals("Calculator was not available."), "Failure shows its reason");
        TaskPresentation stopping = p("stopping", "Calculate eighteen percent tip", "", "", "");
        expect(stopping.eyebrow.equals("Stopping…") && stopping.body.isEmpty() && stopping.active(), "Stopping is shown as such");
        TaskPresentation restarted = p("interrupted", "Working on your request", "", "", "The backend restarted. The previous outcome is unknown.");
        expect(restarted.eyebrow.equals("Stopped") && restarted.body.startsWith("The backend restarted"),
                "Interrupted keeps the server's explanation");
        expect(p("weird", "x", "a", "b", "c").eyebrow.equals("Status unavailable") && p("weird", "x", "a", "b", "c").body.isEmpty(),
                "Unknown state is not presented as progress or success");

        // Return to the surface / stale stream: the last task stays, marked, never replaced by a guess.
        TaskPresentation stale = TaskPresentation.of(true, "t1", "running", "Calculate eighteen percent tip", "Using Calculator", "", "", false);
        expect(stale.visible && stale.body.equals("Using Calculator") && !stale.note.isEmpty(), "Stale task is kept and labelled");
        expect(done.note.isEmpty(), "Live task has no connection note");
        expect(p("running", "", "", "", "").title.equals("Working on your request"), "Missing title uses the server default");

        System.out.println("TaskPresentationTest: " + checks + " checks passed");
    }
}
