package dev.pixel.backgroundcontrol;

/** Window-local bounds, including the previous layout during a resize traversal. */
final class MirrorWindowFrame {
    static int[] bounds(int width, int height, int laidOutWidth, int laidOutHeight) {
        return new int[]{0, 0, Math.max(width, laidOutWidth), Math.max(height, laidOutHeight)};
    }
}
