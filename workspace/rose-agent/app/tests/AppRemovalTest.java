package dev.pixel.voice;

/** F03: what a long press offers for removing an app (AppRemoval), for every kind of app and both routes. Host JVM. */
public final class AppRemovalTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        // Rose Agent itself: no removal menu.
        for (boolean may : new boolean[]{false, true}) expect(AppRemoval.offer(true, false, false, may) == null, "never Rose itself");

        // An ordinary installed app: Android's confirmation only while the permission is actually held.
        AppRemoval.Offer direct = AppRemoval.offer(false, false, false, true);
        expect(AppRemoval.UNINSTALL.equals(direct.remove) && direct.route == AppRemoval.Route.CONFIRM && direct.note == null, "held: Uninstall opens the confirmation");
        AppRemoval.Offer info = AppRemoval.offer(false, false, false, false);
        expect(AppRemoval.IN_APP_INFO.equals(info.remove) && info.route == AppRemoval.Route.APP_INFO, "not held: named for where it goes, App info");

        // An updated built-in app: its updates can be removed; the factory version stays (Android's own wording).
        AppRemoval.Offer updates = AppRemoval.offer(false, true, true, true);
        expect(AppRemoval.UNINSTALL_UPDATES.equals(updates.remove) && updates.route == AppRemoval.Route.CONFIRM, "updated built-in: Uninstall updates");
        expect(AppRemoval.offer(false, true, true, false).route == AppRemoval.Route.APP_INFO, "and App info without the permission");

        // A built-in app cannot be uninstalled: no removal action, an honest note, App info only.
        for (boolean may : new boolean[]{false, true}) {
            AppRemoval.Offer builtIn = AppRemoval.offer(false, true, false, may);
            expect(builtIn.remove == null && AppRemoval.BUILT_IN.equals(builtIn.note) && builtIn.route == AppRemoval.Route.APP_INFO,
                "built-in: no fake delete, named, App info (" + may + ")");
        }
        // 1J: the confirmation's answer, always checked against the package manager.
        AppRemoval.Outcome gone = AppRemoval.outcome(true, false, false, false);
        expect(gone == AppRemoval.Outcome.REMOVED && AppRemoval.refreshes(gone) && "Notes was uninstalled".equals(AppRemoval.message(gone, "Notes")), "removed: Home refreshes");
        expect(AppRemoval.outcome(false, true, false, false) == AppRemoval.Outcome.REMOVED, "gone is gone, whatever the result code");
        expect(AppRemoval.outcome(false, false, false, true) == AppRemoval.Outcome.REMOVED, "an updated built-in app that is fully gone");
        AppRemoval.Outcome kept = AppRemoval.outcome(false, true, true, false);
        expect(kept == AppRemoval.Outcome.KEPT && !AppRemoval.refreshes(kept) && AppRemoval.message(kept, "Notes") == null, "cancel: stays installed, Home unchanged, nothing said");
        expect(AppRemoval.outcome(false, true, true, true) == AppRemoval.Outcome.KEPT, "cancelled Uninstall updates");
        AppRemoval.Outcome factory = AppRemoval.outcome(true, false, true, true);
        expect(factory == AppRemoval.Outcome.UPDATES_REMOVED && AppRemoval.refreshes(factory) && "Maps updates were uninstalled".equals(AppRemoval.message(factory, "Maps")), "updates removed, factory version stays");
        for (boolean confirmed : new boolean[]{false, true}) {
            AppRemoval.Outcome refused = AppRemoval.outcome(confirmed, false, true, false);
            expect(refused == AppRemoval.Outcome.NOT_REMOVED && !AppRemoval.refreshes(refused)
                && "Work app is still installed. Android didn't uninstall it.".equals(AppRemoval.message(refused, "Work app")), "Android kept it: said so (" + confirmed + ")");
        }
        expect(AppRemoval.outcome(false, false, true, true) == AppRemoval.Outcome.NOT_REMOVED, "updates Android refused to remove");
        // 1I: only the genuine retired Rose Models app (exact package and its release signer) is named retired.
        java.util.List<String> models = java.util.Arrays.asList(AppRemoval.MODELS_SIGNER);
        expect(AppRemoval.retired("dev.pixel.models", models), "the genuine app");
        expect(AppRemoval.retired("dev.pixel.models", java.util.Arrays.asList("00", AppRemoval.MODELS_SIGNER.toUpperCase())), "any listed signer, either case");
        expect(!AppRemoval.retired("dev.pixel.models", java.util.Arrays.asList("650d47f9")), "same package, another signer: an ordinary app");
        expect(!AppRemoval.retired("dev.pixel.models", java.util.Collections.<String>emptyList()) && !AppRemoval.retired("dev.pixel.models", null), "no signer read");
        expect(!AppRemoval.retired("dev.pixel.models.client", models) && !AppRemoval.retired("dev.pixel.voice", models), "exact package only");
        expect(AppRemoval.MODELS_SIGNER.matches("[0-9a-f]{64}"), "a full SHA-256");
        System.out.println("AppRemovalTest: " + checks + " checks passed");
    }
}
