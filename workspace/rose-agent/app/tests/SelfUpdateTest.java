package dev.pixel.voice;

/** APP-UPDATER-002. Fixtures only: these ids, versions and sizes are made up for the test and are not a
 * catalog record, a published APK or evidence that any update is available or installable. */
public final class SelfUpdateTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final String SELF = "dev.pixel.voice";
    static final String ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90";
    static final String OTHER_ID = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";
    static String ok(long installed) {
        return SelfUpdate.verify(ID, 302, SELF, installed, ID, ID, SELF, 302, 4096, ID, 4096, SELF, 302, true);
    }

    public static void main(String[] args) {
        // Admission for an explicit Install tap.
        expect(SelfUpdate.admit(302, 301, false, false, true) == SelfUpdate.Admission.START, "Newer, idle, capable: start");
        expect(SelfUpdate.admit(301, 301, false, false, true) == SelfUpdate.Admission.NOT_NEWER, "Equal is not installed");
        expect(SelfUpdate.admit(300, 301, false, false, true) == SelfUpdate.Admission.NOT_NEWER, "Downgrade is never installed");
        expect(SelfUpdate.admit(302, -1, false, false, true) == SelfUpdate.Admission.NOT_NEWER, "Unreadable installed version is not assumed older");
        expect(SelfUpdate.admit(302, 301, true, false, true) == SelfUpdate.Admission.VOICE_BUSY, "Active or connecting voice is never interrupted");
        expect(SelfUpdate.admit(302, 301, false, true, true) == SelfUpdate.Admission.IN_PROGRESS, "A repeated tap does not start a second install");
        expect(SelfUpdate.admit(302, 301, false, false, false) == SelfUpdate.Admission.NO_PERMISSION, "Missing install capability is reported, not worked around");
        expect(SelfUpdate.admissionText(SelfUpdate.Admission.NO_PERMISSION).contains("won’t change it"), "The limitation is stated honestly");

        // Identity binding: selected record, manifest, bytes and parsed archive must agree.
        expect(ok(301) == null, "Matching newer self record verifies");
        expect(SelfUpdate.verify(OTHER_ID, 302, SELF, 301, ID, ID, SELF, 302, 4096, ID, 4096, SELF, 302, true) != null, "Different selected id");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, OTHER_ID, OTHER_ID, SELF, 302, 4096, ID, 4096, SELF, 302, true) != null, "Manifest names another APK");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, OTHER_ID, SELF, 302, 4096, ID, 4096, SELF, 302, true) != null, "Manifest id and sha256 disagree");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, "dev.pixel.other", 302, 4096, ID, 4096, SELF, 302, true) != null, "Manifest for another package");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 303, 4096, ID, 4096, SELF, 302, true) != null, "Version changed since the offer");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302.5, 4096, ID, 4096, SELF, 302, true) != null, "Fractional version");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302, null, ID, 4096, SELF, 302, true) != null, "Missing size");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302, 4096, OTHER_ID, 4096, SELF, 302, true) != null, "Corrupt bytes");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302, 4096, ID, 4095, SELF, 302, true) != null, "Truncated bytes");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302, 4096, ID, 4096, null, -1, true) != null, "Not a parseable APK");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302, 4096, ID, 4096, "dev.pixel.other", 302, true) != null, "APK of another package");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302, 4096, ID, 4096, SELF, 301, true) != null, "APK version differs from its record");
        expect(SelfUpdate.verify(ID, 302, SELF, 301, ID, ID, SELF, 302, 4096, ID, 4096, SELF, 302, false) != null, "Different signer");
        expect(ok(302) != null && ok(303) != null, "Installed version caught up or passed it: nothing to install");

        // #407: bytes that match the published update but are not the offered Rose Agent are still rejected, and the
        // reason names what Android actually read, so an unreadable archive, another package and another versionCode
        // are never one indistinguishable message (the reported 'isn't the offered Rose Agent version' covered all three).
        String unreadable = SelfUpdate.verify(ID, 340, SELF, 338, ID, ID, SELF, 340, 4096, ID, 4096, null, -1, true);
        String otherPackage = SelfUpdate.verify(ID, 340, SELF, 338, ID, ID, SELF, 340, 4096, ID, 4096, "dev.pixel.other", 340, true);
        String otherVersion = SelfUpdate.verify(ID, 340, SELF, 338, ID, ID, SELF, 340, 4096, ID, 4096, SELF, 338, true);
        expect(unreadable != null && otherPackage != null && otherVersion != null, "every one is still rejected");
        expect(unreadable.equals("Android couldn’t read the downloaded app, so version 340 wasn’t checked or installed."),
               "an archive Android cannot parse says so: " + unreadable);
        expect(otherPackage.equals("The downloaded app is dev.pixel.other, not Rose Agent."), "another package is named: " + otherPackage);
        expect(otherVersion.equals("The downloaded app is version 338, not the offered version 340."), "the APK's own versionCode is named: " + otherVersion);
        expect(!unreadable.equals(otherPackage) && !otherPackage.equals(otherVersion) && !unreadable.equals(otherVersion), "three distinct reasons");
        // The archive checks still come after the download is bound to the published bytes, and before the signer.
        expect(SelfUpdate.verify(ID, 340, SELF, 338, ID, ID, SELF, 340, 4096, OTHER_ID, 4096, SELF, 338, true).equals("The download didn’t match the published update."),
               "bytes other than the published ones are rejected first");
        expect(SelfUpdate.verify(ID, 340, SELF, 338, ID, ID, SELF, 340, 4096, ID, 4096, SELF, 340, false).equals("The downloaded app isn’t signed like the installed Rose Agent."),
               "the right package and version with another signer is still refused");
        expect(SelfUpdate.verify(ID, 340, SELF, 338, ID, ID, SELF, 340, 4096, ID, 4096, SELF, 340, true) == null, "the matching 340 over 338 is accepted");

        // Immediately before commit.
        expect(SelfUpdate.beforeCommit(302, 301, false) == null, "Still idle and newer: commit");
        expect(SelfUpdate.beforeCommit(302, 301, true).contains("call started"), "A call that began during the download is not interrupted");
        expect(SelfUpdate.beforeCommit(302, 302, false) != null, "Installed elsewhere meanwhile: not committed");

        // Installer outcomes are distinct; only the installed version proves success.
        expect(SelfUpdate.outcome(SelfUpdate.PENDING_USER_ACTION) == SelfUpdate.Outcome.WAITING_FOR_USER, "Pending user action");
        expect(SelfUpdate.outcome(SelfUpdate.SUCCESS) == SelfUpdate.Outcome.INSTALLED, "Success");
        expect(SelfUpdate.outcome(SelfUpdate.ABORTED) == SelfUpdate.Outcome.CANCELLED, "User cancelled");
        for (int failure : new int[] {SelfUpdate.FAILURE, SelfUpdate.BLOCKED, SelfUpdate.INVALID, SelfUpdate.CONFLICT, SelfUpdate.STORAGE, SelfUpdate.INCOMPATIBLE, 99})
            expect(SelfUpdate.outcome(failure) == SelfUpdate.Outcome.FAILED, "Failure status " + failure);
        expect(SelfUpdate.attemptText(302, "committed", 301).startsWith("Waiting for Android"), "A committed session is not an installed update");
        expect(SelfUpdate.attemptText(302, "downloading", 301).startsWith("Downloading"), "Downloading");
        expect(SelfUpdate.attemptText(302, "WAITING_FOR_USER", 301).contains("Confirm"), "Waiting for the owner's confirmation");
        expect(SelfUpdate.attemptText(302, "CANCELLED", 301).contains("cancelled"), "Cancelled");
        expect(SelfUpdate.attemptText(302, "INSTALL_FAILED_UPDATE_INCOMPATIBLE", 301).contains("wasn’t installed"), "Failure message shown");
        expect(!SelfUpdate.attemptText(302, "INSTALLED", 301).contains("is installed"), "A reported success that the installed version contradicts is not shown as installed");
        expect(SelfUpdate.attemptText(302, "committed", 302).equals("Rose Agent 302 is installed."), "Success comes from the installed version");
        expect(SelfUpdate.attemptText(-1, "INSTALLED", 302).isEmpty() && SelfUpdate.attemptText(302, "", 301).isEmpty(), "No record, nothing claimed");

        System.out.println("SelfUpdateTest: " + checks + " checks passed");
    }
}
