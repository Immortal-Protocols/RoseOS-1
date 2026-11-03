package dev.pixel.voice;

/** #410: Home's Rose control launch rule (HomeRoseLaunch): one launch at a time, repeated and interrupted taps,
 * leaving and returning to Home, clock oddities, and reduced motion. Host JVM only. */
public final class HomeRoseLaunchTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        HomeRoseLaunch l = new HomeRoseLaunch();
        long t = 1_000_000L;
        expect(l.tap(t), "the first tap opens Rose");
        expect(l.inFlight(t + 1), "and the launch is in flight");
        // Repeated taps while the sheet grows open nothing extra (no second Rose stacked, nothing underneath acted on).
        for (long dt : new long[]{0, 1, 50, 300, 619}) expect(!l.tap(t + dt), "a repeat " + dt + "ms later is ignored");
        expect(l.tap(t + 620), "after the reference's 620ms a tap opens again");
        // Interrupted: Home is left (paused) before the 620ms; returning to Home makes the next tap fresh at once.
        HomeRoseLaunch i = new HomeRoseLaunch();
        expect(i.tap(t), "open");
        i.reset();
        expect(!i.inFlight(t + 10) && i.tap(t + 10), "after Home was left or resumed, the next tap opens immediately");
        // A launch that never resolved (no pause) cannot block Rose for good.
        HomeRoseLaunch s = new HomeRoseLaunch();
        expect(s.tap(t) && s.tap(t + 5_000), "a stale in-flight launch expires");
        // The clock going backwards (uptime never does, but be safe) never blocks Rose.
        HomeRoseLaunch b = new HomeRoseLaunch();
        expect(b.tap(t) && b.tap(t - 10), "a time before the launch is not treated as in flight");
        // Many quick taps: exactly one launch per 620ms window.
        HomeRoseLaunch m = new HomeRoseLaunch();
        int launches = 0;
        for (long dt = 0; dt < 2000; dt += 40) if (m.tap(t + dt)) launches++;
        expect(launches == 4, "taps every 40ms for 2s launch at 0, 640, 1280 and 1920ms: " + launches);
        // Reduced motion: animator scale 0 means a plain, instant launch; any positive scale animates.
        expect(!HomeRoseLaunch.animate(0f), "animations off: no clip-reveal");
        expect(HomeRoseLaunch.animate(1f) && HomeRoseLaunch.animate(.5f) && HomeRoseLaunch.animate(10f), "animations on: clip-reveal");
        expect(!HomeRoseLaunch.animate(-1f), "a nonsense scale never animates");
        System.out.println("HomeRoseLaunchTest: " + checks + " checks passed");
    }
}
