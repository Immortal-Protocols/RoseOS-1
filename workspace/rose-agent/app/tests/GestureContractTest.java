package dev.pixel.voice;

/** #410 gesture_nav_contract_v1, Home's fail-closed rules (GestureContract): the entry, caller, version and rect gates. */
public final class GestureContractTest {
    static int checks;
    static void expect(boolean c, String m) { checks++; if (!c) throw new AssertionError(m); }
    public static void main(String[] args) {
        // Exact keys confirmed from pinned Launcher3 47accce (#410 comment 6593674343).
        expect(GestureContract.EXTRA.equals("gesture_nav_contract_v1"), "version key");
        expect(GestureContract.ICON_POSITION.equals("gesture_nav_contract_icon_position"), "reply key");
        expect(GestureContract.COMPONENT.equals("android.intent.extra.COMPONENT_NAME") && GestureContract.USER.equals("android.intent.extra.USER")
            && GestureContract.CALLBACK.equals("android.intent.extra.REMOTE_CALLBACK") && GestureContract.ENABLE.equals("gesture_nav_contract_enable"), "contract keys");
        expect(GestureContract.tag("com.a", "com.a.Main").equals("home-app:com.a/com.a.Main"), "tiles are named by exact package and activity");
        // The recents package from config_recentsComponentName.
        expect("com.android.launcher3".equals(GestureContract.recentsPackage("com.android.launcher3/com.android.quickstep.RecentsActivity")), "flattened component");
        expect("com.x".equals(GestureContract.recentsPackage("com.x")) && GestureContract.recentsPackage("") == null && GestureContract.recentsPackage(null) == null
            && GestureContract.recentsPackage("/cls") == null && GestureContract.recentsPackage("  ") == null, "empty or missing: none");
        // Entry: MainActivity itself, SDK 35+, a HOME intent, Home not already showing.
        expect(GestureContract.entry(true, 35, true, false), "a swipe Home from an app");
        expect(!GestureContract.entry(false, 35, true, false), "a subclass (WorkspaceAppsActivity) never replies");
        expect(!GestureContract.entry(true, 34, true, false), "before ComponentCaller: no reply");
        expect(!GestureContract.entry(true, 35, false, false), "not a HOME intent (the icon, settings): no reply");
        expect(!GestureContract.entry(true, 35, true, true), "Home already showing: no reply");
        // Caller: exactly the platform-reported recents package with a real uid.
        String recents = "com.android.launcher3";
        expect(GestureContract.caller(recents, 10123, recents), "Launcher3, identity shared");
        expect(!GestureContract.caller(null, -1, recents), "identity not shared: no reply");
        expect(!GestureContract.caller(recents, -1, recents), "no uid: no reply");
        expect(!GestureContract.caller("com.evil", 10200, recents), "another app: no reply");
        expect(!GestureContract.caller(recents, 10123, null), "recents unknown: no reply");
        expect(!GestureContract.caller("com.android.launcher3.evil", 10123, recents) && !GestureContract.caller("COM.ANDROID.LAUNCHER3", 10123, recents), "exact match only");
        // Rect: finite, non-empty, inside the window.
        expect(GestureContract.rect(10, 20, 69, 79, 1080, 2400), "a tile");
        expect(!GestureContract.rect(Float.NaN, 20, 69, 79, 1080, 2400) && !GestureContract.rect(10, 20, Float.POSITIVE_INFINITY, 79, 1080, 2400), "not finite");
        expect(!GestureContract.rect(10, 20, 10, 79, 1080, 2400) && !GestureContract.rect(10, 79, 69, 20, 1080, 2400), "empty or inverted");
        expect(!GestureContract.rect(-1, 20, 58, 79, 1080, 2400) && !GestureContract.rect(1030, 20, 1089, 79, 1080, 2400) && !GestureContract.rect(10, 2390, 69, 2449, 1080, 2400), "outside the window");
        expect(!GestureContract.rect(10, 20, 69, 79, 0, 2400), "no window");
        System.out.println("GestureContractTest: " + checks + " checks passed");
    }
}
