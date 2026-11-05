package dev.pixel.voice;

/** F08: Rose's own mute gives way only to a reachable OS Rose chin (RoseChin). Host JVM only. */
public final class RoseChinTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        // Reachability is declared only on REGISTER, by arg2 == AVAILABLE.
        expect(RoseChin.declaresReachable(RoseChin.REGISTER, RoseChin.AVAILABLE), "a REGISTER declaring the chin reachable");
        expect(!RoseChin.declaresReachable(RoseChin.REGISTER, 0), "an older OS (arg2 0) declares nothing");
        expect(!RoseChin.declaresReachable(RoseChin.REGISTER, 2), "an unknown value declares nothing");
        // The mic slot's visible-now marker is arg1 on its own REGISTER, with arg2 0: never reachability.
        expect(!RoseChin.declaresReachable(RoseChin.REGISTER, 0), "visible-now (arg1 = 1, arg2 = 0) is not reachability");
        for (int what : new int[]{2, 3, 4, 5, 6, 100}) expect(!RoseChin.declaresReachable(what, RoseChin.AVAILABLE), "only REGISTER declares, not " + what);
        // The in-app mute shows unless the chin is reachable.
        expect(RoseChin.showInAppMute(false), "no reachable chin: Rose keeps its own mute");
        expect(!RoseChin.showInAppMute(true), "a reachable chin: the duplicate gives way");
        System.out.println("RoseChinTest: " + checks + " checks passed");
    }
}
