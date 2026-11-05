package com.android.quickstep.fallback;
public final class RoseEdgeSwipe {
    public static final String DIAG_PROPERTY = "persist.wm.debug.rose_edge_diag";
    private int mStage;
    public int onMove(float upPxAfterSlop) { mStage = RoseEdgeGesture.stage(mStage, upPxAfterSlop, false); return mStage; }
    public String diag() { return System.getProperty(DIAG_PROPERTY); }
}
