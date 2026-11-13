package dev.pixel.builder;

/** Project 1 Level 1/3: the builder client's pure rules (BuildJob, InstallPlan, ShareMessage). Host JVM. */
public final class BuilderRulesTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        // Progress never moves backwards; a finished job stays finished.
        expect(BuildJob.advances(null, BuildJob.Stage.QUEUED), "first update accepted");
        expect(BuildJob.advances(BuildJob.Stage.BUILDING, BuildJob.Stage.TESTING), "forward accepted");
        expect(!BuildJob.advances(BuildJob.Stage.TESTING, BuildJob.Stage.BUILDING), "stale update ignored");
        expect(BuildJob.advances(BuildJob.Stage.QUEUED, BuildJob.Stage.FAILED), "failure from any live stage");
        expect(!BuildJob.advances(BuildJob.Stage.READY, BuildJob.Stage.BUILDING), "ready stays ready");
        expect(!BuildJob.advances(BuildJob.Stage.CANCELLED, BuildJob.Stage.READY), "a cancelled job is not revived");

        BuildJob building = new BuildJob("j1", BuildJob.Stage.BUILDING, null, null);
        expect(building.cancellable() && !building.terminal(), "a live job can be cancelled");
        expect("Building the app".equals(BuildJob.status(building)), "building status");
        BuildJob ready = new BuildJob("j1", BuildJob.Stage.READY, null, null);
        expect(!ready.cancellable() && "Ready to install".equals(BuildJob.status(ready)), "ready status");
        expect("Building the app (attempt 2)".equals(BuildJob.status(new BuildJob("j1", BuildJob.Stage.BUILDING, null, null, 2))), "a retry is visible");
        expect("Ready to install".equals(BuildJob.status(new BuildJob("j1", BuildJob.Stage.READY, null, null, 2))), "a finished retry reads as its result");
        expect(new BuildJob("j1", BuildJob.Stage.QUEUED, null, null, 0).attempt == 1, "attempts count from 1");

        // Cancel means stopping until the job confirms; a job that finished first keeps its real result.
        expect("Stopping the build…".equals(BuildJob.status(building, true)), "cancel requested: stopping, not stopped");
        expect("Build cancelled".equals(BuildJob.status(new BuildJob("j1", BuildJob.Stage.CANCELLED, null, null), true)), "cancel confirmed by the job");
        expect("Ready to install".equals(BuildJob.status(ready, true)), "a job that finished first keeps its result");

        // A builder restart: an interrupted job is an honest, retryable unknown outcome.
        BuildJob.Failure interrupted = new BuildJob.Failure(BuildJob.Category.INTERNAL, "Interrupted, outcome unknown", true);
        BuildJob lost = new BuildJob("j3", BuildJob.Stage.FAILED, null, interrupted);
        expect("The builder hit an internal error".equals(BuildJob.status(lost)) && BuildJob.failureAdvice(interrupted).startsWith("Try again"), "interrupted: named, retry offered");

        // Failures are named, and retry is offered only when the server says it can help.
        BuildJob.Failure compile = new BuildJob.Failure(BuildJob.Category.COMPILE, "Main.java:12", false);
        BuildJob failed = new BuildJob("j2", BuildJob.Stage.FAILED, null, compile);
        expect("The app's code didn't compile".equals(BuildJob.status(failed)), "failure titled by category");
        expect(!BuildJob.failureAdvice(compile).startsWith("Try again"), "no retry advice when not retryable");
        expect(BuildJob.failureAdvice(new BuildJob.Failure(BuildJob.Category.TIMEOUT, "", true)).startsWith("Try again"), "retry advice when retryable");
        expect(BuildJob.failureAdvice(new BuildJob.Failure(BuildJob.Category.UNREACHABLE, "", true)).contains("reconnect"), "unreachable: reconnect");
        expect("The build failed".equals(BuildJob.failureTitle(null)), "unknown failure still honest");

        // Install/update continuity: same package, same signer, higher version.
        String pkg = "dev.rose.app.tipcalc", signer = "AB12", other = "CD34";
        InstallPlan.Apk v2 = new InstallPlan.Apk(pkg, signer, "ff00", 2);
        expect(InstallPlan.offer(v2, null).action == InstallPlan.Action.INSTALL, "not installed: install");
        expect(InstallPlan.offer(v2, new InstallPlan.Installed(pkg, signer, 1)).action == InstallPlan.Action.UPDATE, "older same signer: update");
        expect(InstallPlan.offer(v2, new InstallPlan.Installed(pkg, "ab12", 2)).action == InstallPlan.Action.OPEN, "same version: open (digest case ignored)");
        InstallPlan down = InstallPlan.offer(v2, new InstallPlan.Installed(pkg, signer, 3));
        expect(down.action == InstallPlan.Action.REFUSE && down.reason.contains("newer"), "downgrade refused");
        InstallPlan resign = InstallPlan.offer(v2, new InstallPlan.Installed(pkg, other, 1));
        expect(resign.action == InstallPlan.Action.REFUSE && resign.reason.contains("data is left alone"), "different signer refused, never reinstall");
        expect(down.refusal == InstallPlan.Receipt.REFUSED_VERSION && resign.refusal == InstallPlan.Receipt.REFUSED_SIGNER, "refusals carry their receipt");
        expect("Update".equals(InstallPlan.label(InstallPlan.offer(v2, new InstallPlan.Installed(pkg, signer, 1)))), "update label");

        // A download is used only when it is exactly the recorded version.
        expect(InstallPlan.verify(v2, null, null).action == InstallPlan.Action.REFUSE, "missing file refused");
        expect(InstallPlan.verify(v2, new InstallPlan.Apk(pkg, signer, "0000", 2), null).action == InstallPlan.Action.REFUSE, "hash mismatch refused");
        expect(InstallPlan.verify(v2, new InstallPlan.Apk(pkg, signer, "FF00", 3), null).action == InstallPlan.Action.REFUSE, "wrong version refused");
        expect(InstallPlan.verify(v2, new InstallPlan.Apk("x.y", signer, "ff00", 2), null).action == InstallPlan.Action.REFUSE, "wrong package refused");
        expect(InstallPlan.verify(v2, new InstallPlan.Apk(pkg, other, "ff00", 2), null).action == InstallPlan.Action.REFUSE, "wrong signer refused");
        expect(InstallPlan.verify(v2, new InstallPlan.Apk(pkg, signer, "FF00", 2), new InstallPlan.Installed(pkg, signer, 1)).action == InstallPlan.Action.UPDATE, "matching file: update");

        expect(InstallPlan.verify(v2, new InstallPlan.Apk(pkg, other, "ff00", 2), null).refusal == InstallPlan.Receipt.REFUSED_SIGNER, "wrong signer file: refused_signer");
        expect(InstallPlan.verify(v2, new InstallPlan.Apk(pkg, signer, "0000", 2), null).refusal == InstallPlan.Receipt.FAILED, "mismatched file: failed");

        // Install receipts (builder API v1 §3.5): what Android did, never what was intended.
        expect(InstallPlan.androidOutcome(true, false, false) == InstallPlan.Receipt.INSTALLED, "first install: installed");
        expect(InstallPlan.androidOutcome(true, false, true) == InstallPlan.Receipt.UPDATED, "over the same app: updated");
        expect(InstallPlan.androidOutcome(false, true, true) == InstallPlan.Receipt.CANCELLED_BY_USER, "owner declined: cancelled_by_user");
        expect(InstallPlan.androidOutcome(false, false, true) == InstallPlan.Receipt.FAILED, "Android refused: failed");
        expect("refused_signer".equals(InstallPlan.Receipt.REFUSED_SIGNER.wire()) && "cancelled_by_user".equals(InstallPlan.Receipt.CANCELLED_BY_USER.wire()), "wire names");

        // Share text: the owner sends it; version links name the version, latest links do not.
        String exact = ShareMessage.text("Tip Calc", "1.2", "https://example.invalid/a/tipcalc/1.2", false);
        expect(exact.startsWith("Tip Calc 1.2 for Android: https://example.invalid/a/tipcalc/1.2\n"), "immutable link names its version");
        String latest = ShareMessage.text("Tip Calc", "1.2", "https://example.invalid/a/tipcalc", true);
        expect(latest.startsWith("Tip Calc for Android: "), "latest link omits the version");
        expect(ShareMessage.text("Ev\nil", null, "u", false).startsWith("Ev il for Android"), "names cleaned");
        expect(ShareMessage.text(" ", null, "u", true).startsWith("An app for Android"), "no invented name");

        System.out.println("BuilderRulesTest: " + checks + " checks passed");
    }
}
