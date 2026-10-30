package dev.pixel.backgroundcontrol;

public final class ActionNoticeTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        ActionNotice n = new ActionNotice();
        expect(n.text().isEmpty(), "Nothing is shown before a button is pressed");
        n.consent(false);
        expect("No consent is pending.".equals(n.text()), "No pending consent is said, not silent");
        n.consent(true);
        expect("Android consent opened.".equals(n.text()), "A launched consent is reported as opened, not granted");
        n.failed("Android consent", new IllegalStateException("sender cancelled"));
        expect(n.text().equals("Android consent failed: java.lang.IllegalStateException: sender cancelled"), n.text());
        n.failed("Android consent", new RuntimeException("x".repeat(1000)));
        expect(n.text().length() < 340, "A failure is bounded");
        n.stopped("stopped", "old error");
        expect("Background session stopped.".equals(n.text()), "A clean Stop ignores a stale earlier error");
        n.stopped("cleanup_failed", "java.lang.RuntimeException: close failed");
        expect(n.text().startsWith("Stop did not finish") && n.text().contains("close failed"),
            "A failed close is never reported as stopped");
        n.stopped("cleanup_failed", "");
        expect(n.text().equals("Stop did not finish: the session could not be closed."), n.text());
        n.stopped("starting", "");
        expect(n.text().contains("starting") && !n.text().contains("stopped."), "An unexpected status is shown as it is");
        String saved = n.text();
        ActionNotice recreated = new ActionNotice();
        recreated.restore(saved);
        expect(saved.equals(recreated.text()), "The notice survives recreation through its saved text");
        recreated.restore(null);
        expect(recreated.text().isEmpty(), "Nothing saved restores nothing");
        expect("dev.pixel.backgroundcontrol.notice".equals(ActionNotice.SAVED_KEY), "Stable saved-state key");
        System.out.println("ActionNoticeTest: " + checks + " checks passed");
    }
}
