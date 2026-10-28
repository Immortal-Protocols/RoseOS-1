package dev.pixel.voice;

public final class RendererRecoveryTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        RendererRecovery r = new RendererRecovery(3, 60000);
        expect(r.allow(0) && r.allow(1000) && r.allow(2000), "A few losses are recovered automatically");
        expect(!r.allow(3000), "A renderer that keeps dying is not reloaded in a loop");
        expect(!r.allow(59000), "Still within the window");
        expect(r.allow(61000), "After the window, recovery is allowed again");
        r.reset();
        expect(r.allow(61001) && r.allow(61002), "An explicit user action starts a fresh budget");
        RendererRecovery one = new RendererRecovery(1, 1000);
        expect(one.allow(5) && !one.allow(6) && one.allow(1006), "Window boundary");
        System.out.println("RendererRecoveryTest: " + checks + " checks passed");
    }
}
