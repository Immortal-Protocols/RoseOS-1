package dev.pixel.backgroundcontrol;

/** F06: Rose never takes the owner's Home or recents into her workspace (HomeTarget). Host test, no Android. */
public final class HomeTargetTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        String calc = "com.google.android.calculator", launcher3 = "com.android.launcher3", settings = "com.android.settings";
        expect(HomeTarget.refuses(HomeTarget.ROSE_AGENT, HomeTarget.ROSE_AGENT), "Rose Agent as the default Home");
        expect(HomeTarget.refuses(HomeTarget.ROSE_AGENT, launcher3), "Rose Agent even when it is not the default Home");
        expect(HomeTarget.refuses(HomeTarget.ROSE_AGENT, null), "Rose Agent when Home cannot be resolved");
        expect(HomeTarget.refuses(launcher3, launcher3), "another default Home app");
        expect(!HomeTarget.refuses(calc, HomeTarget.ROSE_AGENT), "an ordinary app is taken over");
        expect(!HomeTarget.refuses(calc, null), "an ordinary app with Home unresolved");
        // Settings has a low-priority FallbackHome filter: only the resolved default Home is refused, never every HOME filter.
        expect(!HomeTarget.refuses(settings, HomeTarget.ROSE_AGENT), "Settings stays operable");
        expect(!HomeTarget.refuses(HomeTarget.CHOOSER, HomeTarget.CHOOSER) && !HomeTarget.refuses(calc, HomeTarget.CHOOSER),
            "the HOME chooser is not a Home app");
        expect(!HomeTarget.refuses(null, launcher3), "no package");
        expect(HomeTarget.platformRefused("java.lang.IllegalStateException: target_not_standard: dev.pixel.voice"), "the platform backstop");
        expect(!HomeTarget.platformRefused("java.lang.IllegalStateException: target_in_foreground: x") && !HomeTarget.platformRefused(null),
            "other errors");
        expect("target_not_standard".equals(HomeTarget.REASON), "the reason shared with #551");
        System.out.println("HomeTargetTest: " + checks + " checks passed");
    }
}
