package dev.pixel.backgroundcontrol;

import java.util.Arrays;
import java.util.Collections;

public final class AnyAppControlTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final String ROSE = "dev.pixel.backgroundcontrol";
    static final String CERT = "650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb";
    static final String OTHER = "cd".repeat(32);

    public static void main(String[] args) {
        // The pin written by apply-os-changes.py: "<controller package>:<certificate>", certificates in upper case.
        String[] pins = {ROSE + ":" + CERT.toUpperCase()};
        expect(AnyAppControl.pinned(ROSE, Collections.singletonList(CERT), pins), "The exact Rose controller and signer are pinned");
        expect(AnyAppControl.pinned(ROSE, Arrays.asList(OTHER, CERT), pins), "Any one of the controller's signers may match");
        String colons = CERT.toUpperCase().replaceAll("(..)(?!$)", "$1:");
        expect(AnyAppControl.pinned(ROSE, Collections.singletonList(CERT), new String[] {ROSE + ":" + colons}), "Colon-separated certificates match");

        // Negative: a non-Rose controller, a wrong signature, malformed or empty pins, no signers.
        expect(!AnyAppControl.pinned("com.example.agent", Collections.singletonList(CERT), pins), "Another package with the same signer is not pinned");
        expect(!AnyAppControl.pinned(ROSE, Collections.singletonList(OTHER), pins), "The Rose package with another signer is not pinned");
        expect(!AnyAppControl.pinned(ROSE + ".evil", Collections.singletonList(CERT), pins), "A package-name prefix is not the package");
        expect(!AnyAppControl.pinned(ROSE, Collections.singletonList(CERT), new String[] {}), "An empty list (the current Pixel 10 build) pins nothing");
        expect(!AnyAppControl.pinned(ROSE, Collections.singletonList(CERT), null), "No list pins nothing");
        for (String bad : new String[] {ROSE, ROSE + ":", ":" + CERT, ROSE + ":" + CERT.substring(2), ROSE + ":" + CERT + "00", ROSE + ":zz" + CERT.substring(2), null})
            expect(!AnyAppControl.pinned(ROSE, Collections.singletonList(CERT), new String[] {bad}), "Malformed pin matches nothing: " + bad);
        expect(!AnyAppControl.pinned(ROSE, Collections.emptyList(), pins), "No signers match nothing");
        expect(!AnyAppControl.pinned(ROSE, null, pins), "Unknown signers match nothing");
        expect(!AnyAppControl.pinned(null, Collections.singletonList(CERT), pins), "No package matches nothing");

        // Workspace following: target-only unless the build lets this controller drive any app.
        String target = "com.android.vending", installer = "com.google.android.packageinstaller";
        expect(AnyAppControl.actionable(target, target, false) && AnyAppControl.actionable(target, target, true), "The target is always actionable");
        expect(!AnyAppControl.actionable(installer, target, false), "Without any-app control the installer stays out of reach");
        expect(AnyAppControl.actionable(installer, target, true), "With any-app control the installer screen in the workspace is reachable");
        expect(!AnyAppControl.actionable("", target, true) && !AnyAppControl.actionable(null, target, true), "A node without a package is never actionable");

        // The platform's refusal is classified, not reported as a generic failure.
        expect(AnyAppControl.targetRefused("java.lang.IllegalArgumentException: Invalid target package for ComputerControl: com.amazon.mShop.android.shopping"),
            "The observed Pixel 10 refusal is recognised");
        expect(!AnyAppControl.targetRefused("java.lang.IllegalStateException: Target did not produce an allowed background frame"), "Other failures stay failures");
        expect(!AnyAppControl.targetRefused(null) && !AnyAppControl.targetRefused(""), "No error is no refusal");
        System.out.println("AnyAppControlTest: " + checks + " checks passed");
    }
}
