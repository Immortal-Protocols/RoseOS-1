package com.android.quickstep.fallback;
public final class RoseEdgeGesture {
    private RoseEdgeGesture() {}
    public static int stage(int previous, float upDp, boolean raisedAtStart) { return raisedAtStart ? previous : (int) upDp; }
}
