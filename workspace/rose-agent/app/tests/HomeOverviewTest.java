package dev.pixel.voice;

import dev.pixel.voice.HomeOverview.Result;

/** OS-OVERVIEW-156-M001: the Home overview entry against a recording route. One tap is one request; repeats
 * while it is in flight never toggle the overview closed; an unavailable or refused route claims nothing; and
 * leaving or recreating Home starts clear. */
public final class HomeOverviewTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }

    static final class Recording implements HomeOverview.Route {
        Boolean answer;
        int calls;
        Recording(Boolean answer) { this.answer = answer; }
        public Boolean show() { calls++; return answer; }
    }

    public static void main(String[] args) {
        // One tap, one system request.
        HomeOverview overview = new HomeOverview();
        Recording route = new Recording(true);
        expect(overview.request(1_000, route) == Result.REQUESTED && route.calls == 1, "One tap makes one request");
        expect(HomeOverview.notice(Result.REQUESTED) == null, "A request handed to the system shows no notice");

        // Repeated taps while it is in flight make no second (toggling) request.
        expect(overview.request(1_100, route) == Result.IN_FLIGHT, "A second tap is in flight");
        expect(overview.request(1_000 + HomeOverview.SETTLE_MS - 1, route) == Result.IN_FLIGHT, "Still in flight");
        expect(route.calls == 1, "Repeats never toggle the overview closed");

        // The overview covered Home (onPause); returning, the next tap is a new request.
        overview.homeLeft();
        expect(overview.request(1_200, route) == Result.REQUESTED && route.calls == 2, "After return a tap requests again");

        // Accepted but nothing happened: after SETTLE_MS a tap may try again.
        expect(overview.request(1_200 + HomeOverview.SETTLE_MS, route) == Result.REQUESTED && route.calls == 3,
            "A settled request does not block the next tap");

        // Unavailable route (phone controls off): no request reported, a truthful notice, nothing held in flight.
        HomeOverview off = new HomeOverview();
        Recording unavailable = new Recording(null);
        expect(off.request(5_000, unavailable) == Result.UNAVAILABLE && unavailable.calls == 1, "Unavailable is asked once");
        expect(HomeOverview.notice(Result.UNAVAILABLE).contains("Nothing was opened"), "Unavailable never claims success");
        unavailable.answer = true;
        expect(off.request(5_001, unavailable) == Result.REQUESTED, "Enabling the route works on the next tap");

        // Refused by the system: reported as failed, not in flight.
        HomeOverview refused = new HomeOverview();
        Recording denied = new Recording(false);
        expect(refused.request(9_000, denied) == Result.FAILED, "A refused request is a failure");
        expect(HomeOverview.notice(Result.FAILED) != null, "A failure says the overview did not open");
        expect(refused.request(9_001, denied) == Result.FAILED && denied.calls == 2, "A failure holds nothing in flight");

        // A recreated Home is a new instance: nothing carried over from the old activity.
        HomeOverview recreated = new HomeOverview();
        Recording fresh = new Recording(true);
        expect(recreated.request(1_050, fresh) == Result.REQUESTED && fresh.calls == 1, "A recreated Home starts clear");

        // The well's spoken label names the action or the reason.
        expect(HomeOverview.describe(true).equals("Overview"), "Available label");
        expect(HomeOverview.describe(false).contains("unavailable"), "Unavailable label names the reason");
        System.out.println("HomeOverviewTest: " + checks + " checks passed");
    }
}
