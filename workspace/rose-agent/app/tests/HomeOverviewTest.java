package dev.pixel.voice;

import dev.pixel.voice.HomeOverview.Result;

/** OS-OVERVIEW-156-M001: the Home overview entry against a recording route. One tap is one request; repeats
 * while it is in flight never toggle the overview closed; an unavailable or refused route claims nothing; and
 * leaving or recreating Home starts clear. #156: an accepted request that leaves Home showing is reported
 * unconfirmed once, after SETTLE_MS; pause, destroy or a newer request make its check say nothing. */
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

        // #156: accepted, but Home is still showing at SETTLE_MS. One truthful notice, once, and nothing retried.
        HomeOverview silent = new HomeOverview();
        Recording quiet = new Recording(true);
        expect(silent.request(20_000, quiet) == Result.REQUESTED, "Accepted");
        long t1 = silent.token();
        expect(silent.unconfirmed(t1, 20_000 + HomeOverview.SETTLE_MS - 1) == null, "Nothing before the deadline");
        expect(silent.request(20_000 + HomeOverview.SETTLE_MS - 1, quiet) == Result.IN_FLIGHT && quiet.calls == 1,
            "A duplicate tap before the deadline makes no second (toggling) request");
        expect(silent.token() == t1, "A duplicate tap is not a new request");
        String said = silent.unconfirmed(t1, 20_000 + HomeOverview.SETTLE_MS);
        expect(HomeOverview.UNCONFIRMED.equals(said), "Home never left: the overview is reported unconfirmed");
        expect(said.contains("didn’t open here") && !said.toLowerCase().contains("opened the") && !said.contains("try"),
            "The notice claims only what was observed and suggests no retry");
        expect(silent.unconfirmed(t1, 20_000 + 2 * HomeOverview.SETTLE_MS) == null, "Said once per request");
        expect(quiet.calls == 1, "Nothing was retried, toggled or opened instead");
        expect(silent.request(20_000 + 2 * HomeOverview.SETTLE_MS, quiet) == Result.REQUESTED && quiet.calls == 2,
            "After the notice, the next tap is a new request");

        // The overview covered Home before the deadline (onPause): the pending check says nothing.
        HomeOverview paused = new HomeOverview();
        Recording works = new Recording(true);
        paused.request(30_000, works);
        long t2 = paused.token();
        paused.homeLeft();
        expect(paused.unconfirmed(t2, 30_000 + HomeOverview.SETTLE_MS) == null, "Home left: no unconfirmed notice");
        // Back on Home, a new tap is a new request with its own check; the old one stays silent.
        expect(paused.request(31_000, works) == Result.REQUESTED && works.calls == 2, "A successful new request after return");
        long t3 = paused.token();
        expect(t3 != t2, "The new request has a new token");
        expect(paused.unconfirmed(t2, 31_000 + HomeOverview.SETTLE_MS) == null, "A stale check from the earlier request says nothing");
        expect(HomeOverview.UNCONFIRMED.equals(paused.unconfirmed(t3, 31_000 + HomeOverview.SETTLE_MS)), "...while the current one still can");

        // A replacement request after the first settled makes the first request's late check stale.
        HomeOverview replaced = new HomeOverview();
        Recording again = new Recording(true);
        replaced.request(40_000, again);
        long first = replaced.token();
        expect(replaced.request(40_000 + HomeOverview.SETTLE_MS, again) == Result.REQUESTED, "A later tap replaces it");
        expect(replaced.unconfirmed(first, 40_000 + 2 * HomeOverview.SETTLE_MS) == null, "The replaced request's check is stale");

        // The activity is destroyed with a request pending: nothing may speak afterwards.
        HomeOverview destroyed = new HomeOverview();
        destroyed.request(50_000, new Recording(true));
        long t4 = destroyed.token();
        destroyed.cancel();
        expect(destroyed.unconfirmed(t4, 50_000 + HomeOverview.SETTLE_MS) == null, "Destroy cancels the pending notice");

        // Unavailable and refused requests keep their own notices and start no check.
        HomeOverview none = new HomeOverview();
        long before = none.token();
        expect(none.request(60_000, new Recording(null)) == Result.UNAVAILABLE && none.token() == before, "Unavailable starts no check");
        expect(none.request(60_001, new Recording(false)) == Result.FAILED && none.token() == before, "A refusal starts no check");
        expect(none.unconfirmed(before, 60_000 + HomeOverview.SETTLE_MS) == null, "...so nothing is reported unconfirmed");
        expect(HomeOverview.notice(Result.UNAVAILABLE) != null && HomeOverview.notice(Result.FAILED) != null
            && HomeOverview.notice(Result.IN_FLIGHT) == null, "The existing notices are unchanged");

        // The well's spoken label names the action or the reason.
        expect(HomeOverview.describe(true).equals("Overview"), "Available label");
        expect(HomeOverview.describe(false).contains("unavailable"), "Unavailable label names the reason");
        System.out.println("HomeOverviewTest: " + checks + " checks passed");
    }
}
