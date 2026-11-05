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
        System.out.println("AppRemovalTest: " + checks + " checks passed");
    }
}
