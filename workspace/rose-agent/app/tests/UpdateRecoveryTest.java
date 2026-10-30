package dev.pixel.voice;

import dev.pixel.voice.UpdateRecovery.Attempt;

/** Launcher update channel persistence and recovery decisions against the production UpdateRecovery and
 * DirectUpdate.Channel. No network, installer or device: these decide what the app does and says. */
public final class UpdateRecoveryTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final long NOW = 1_800_000_000_000L, MIN = 60_000;

    public static void main(String[] args) {
        // Channel persistence: fresh install is Beta; explicit choices are kept even while unavailable; corrupt is Beta.
        expect(UpdateRecovery.channel(null) == DirectUpdate.Channel.BETA, "Fresh install checks Beta");
        expect(UpdateRecovery.channel("prod") == DirectUpdate.Channel.PROD && UpdateRecovery.channel("alpha") == DirectUpdate.Channel.ALPHA,
            "An explicit unavailable choice is kept, never silently switched");
        expect(UpdateRecovery.channel("Beta") == DirectUpdate.Channel.BETA && UpdateRecovery.repair("Beta"), "A corrupt value reads Beta and is repaired");
        expect(!UpdateRecovery.repair(null) && !UpdateRecovery.repair("prod"), "Nothing else is rewritten");
        expect(UpdateRecovery.store(DirectUpdate.Channel.BETA).equals("beta"), "Stored as DirectUpdate's own token");

        // Attempts: the installed version decides success.
        expect(UpdateRecovery.classify(311, "committed", NOW, NOW, 311, false) == Attempt.INSTALLED, "Installed version proves success");
        expect(UpdateRecovery.classify(311, "INSTALLED", NOW, NOW, 310, false) == Attempt.INTERRUPTED,
            "A reported success without the version is not success");
        // Interrupted: process killed mid-download or after commit, no live session, or a pre-timestamp record.
        expect(UpdateRecovery.classify(311, "downloading", NOW - 2 * MIN, NOW, 310, false) == Attempt.IN_PROGRESS, "Recent: still running");
        expect(UpdateRecovery.classify(311, "downloading", NOW - 20 * MIN, NOW, 310, false) == Attempt.INTERRUPTED, "Stale download: interrupted");
        expect(UpdateRecovery.classify(311, "committed", -1, NOW, 310, false) == Attempt.INTERRUPTED, "Legacy record without time: interrupted");
        expect(UpdateRecovery.classify(311, "committed", NOW + 5 * MIN, NOW, 310, false) == Attempt.INTERRUPTED, "A clock set back cannot hold it open");
        expect(UpdateRecovery.classify(311, "committed", NOW - 60 * MIN, NOW, 310, true) == Attempt.IN_PROGRESS, "A live session is still Android's");
        expect(UpdateRecovery.classify(311, "WAITING_FOR_USER", NOW, NOW, 310, false) == Attempt.INTERRUPTED, "Confirmation gone: interrupted");
        // Rejected/corrupt and cancelled.
        expect(UpdateRecovery.classify(311, "The download didn’t match the published update.", NOW, NOW, 310, false) == Attempt.REJECTED, "Corrupt is rejected");
        expect(UpdateRecovery.classify(311, "CANCELLED", NOW, NOW, 310, false) == Attempt.CANCELLED, "Cancelled");
        expect(UpdateRecovery.classify(0, "committed", NOW, NOW, 310, false) == Attempt.NONE
            && UpdateRecovery.classify(311, "", NOW, NOW, 310, false) == Attempt.NONE, "No attempt");

        // Retry and cleanup never touch a live session or a committed one.
        expect(UpdateRecovery.mayRetry(Attempt.INTERRUPTED) && UpdateRecovery.mayRetry(Attempt.REJECTED) && !UpdateRecovery.mayRetry(Attempt.IN_PROGRESS)
            && !UpdateRecovery.mayRetry(Attempt.WAITING_FOR_USER), "Retry only after an end state");
        expect(UpdateRecovery.abandonSession("dev.pixel.voice", "dev.pixel.voice", false, Attempt.INTERRUPTED), "Our stale uncommitted session is abandoned");
        expect(!UpdateRecovery.abandonSession("dev.pixel.voice", "dev.pixel.voice", true, Attempt.INTERRUPTED), "A committed session is Android's");
        expect(!UpdateRecovery.abandonSession("com.other", "dev.pixel.voice", false, Attempt.INTERRUPTED), "Never another package's session");
        expect(!UpdateRecovery.abandonSession("dev.pixel.voice", "dev.pixel.voice", false, Attempt.IN_PROGRESS), "Never a running attempt");
        expect(UpdateRecovery.deleteDownload(Attempt.INTERRUPTED) && !UpdateRecovery.deleteDownload(Attempt.IN_PROGRESS), "Stale download removed only when idle");

        // Text: the working version is named; nothing claims a rollback or success that didn't happen.
        for (Attempt a : new Attempt[]{Attempt.INTERRUPTED, Attempt.CANCELLED, Attempt.REJECTED}) {
            String t = UpdateRecovery.text(a, 311, 310, "The download didn’t match the published update.");
            expect(t.contains("Rose Agent 310 is still installed and working."), a + " names the working version: " + t);
            expect(!t.toLowerCase().contains("roll") && !t.contains("311 is installed"), a + " claims no rollback or success");
        }
        expect(UpdateRecovery.text(Attempt.INSTALLED, 311, 311, null).equals("Rose Agent 311 is installed."), "Success text from the installed version");

        // Activation evidence and forward-only recovery.
        expect(UpdateRecovery.activation(310, 311) == UpdateRecovery.Activation.FIRST_RUN_OF_UPDATE, "A newer version started: activated");
        expect(UpdateRecovery.activation(311, 311) == UpdateRecovery.Activation.UNCHANGED && UpdateRecovery.activation(-1, 311) == UpdateRecovery.Activation.FIRST_RUN,
            "Unchanged or first run");
        String recovery = UpdateRecovery.recovery(311);
        expect(recovery.contains("newer version") && recovery.contains("Older versions aren’t reinstalled") && !recovery.toLowerCase().contains("rollback"),
            "Recovery is forward, same signer and channel; never a downgrade");
        System.out.println("UpdateRecoveryTest: " + checks + " checks passed");
    }
}
