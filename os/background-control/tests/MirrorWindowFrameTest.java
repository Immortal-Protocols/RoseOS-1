package dev.pixel.backgroundcontrol;

import java.util.Arrays;

public final class MirrorWindowFrameTest {
    private static void check(int w, int h, int oldW, int oldH, int expectedW, int expectedH) {
        int[] frame = MirrorWindowFrame.bounds(w, h, oldW, oldH);
        if (!Arrays.equals(frame, new int[]{0, 0, expectedW, expectedH}))
            throw new AssertionError(Arrays.toString(frame));
    }
    public static void main(String[] args) {
        check(2076, 2152, 2076, 2152, 2076, 2152);
        check(1026, 2152, 1026, 2152, 1026, 2152);
        check(1026, 2152, 2076, 2152, 2076, 2152);
        check(2076, 2152, 1026, 2152, 2076, 2152);
        check(1026, 1000, 1026, 2152, 1026, 2152);
        check(1026, 2152, 0, 0, 1026, 2152);
        System.out.println("Mirror geometry: 6 resize/local-coordinate checks passed");
    }
}
