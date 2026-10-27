package dev.pixel.backgroundcontrol;

public final class PanePolicyTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        PanePolicy p = new PanePolicy(); Object first = new Object(), second = new Object();
        expect(!p.shouldShow(first, true, 412, 915), "Folded stays hidden");
        expect(!p.shouldShow(first, false, 840, 900), "Wait for ready, preserving consent");
        expect(p.shouldShow(first, true, 840, 900), "Unfolded ready session opens");
        expect(!p.shouldShow(first, true, 840, 900), "No repeated launches on refresh");
        expect(!p.shouldShow(first, true, 900, 840), "Rotation keeps same pane");
        expect(!p.shouldShow(first, true, 412, 915), "Fold hides, clears display latch");
        expect(p.shouldShow(first, true, 840, 900), "Unfold reopens same live session");
        expect(p.shouldShow(second, true, 840, 900), "New session reopens pane");
        expect(!p.shouldShow(null, false, 840, 900), "No active session does not open");
        expect(p.shouldShow(second, true, 840, 900), "Session restart opens");
        expect(!PanePolicy.isExpanded(599, 900), "Threshold folded side");
        expect(PanePolicy.isExpanded(600, 900), "Threshold expanded side");
        System.out.println("PanePolicyTest: " + checks + " checks passed");
    }
}
