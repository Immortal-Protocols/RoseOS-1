package dev.pixel.voice;

import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.FrameLayout;

/** The pager with Home's fixed edge (HomeTray) across the bottom and the one row of page dots just above it (#410
 * Oxblood geometry; OS-PAGING-158-M001); neither moves while the pages slide. The pages run full height so the field
 * continues under the edge. They receive as bottom inset the band's bottom: the edge plus BAND_BOTTOM_DP, so their
 * grids sit in the reference band above the dots. The edge reaches the screen bottom, 92dp tall plus the tappable
 * navigation inset (three-button navigation; gesture navigation needs none), and the dots' centre is 30dp above it. */
final class HomeFrame extends FrameLayout {
 final LauncherPager pager;
 final HomeTray tray;
 final View dots;
 final int dotsHeight;

 HomeFrame(MainActivity activity, LauncherPager pager, HomeTray tray, View dots) {
  super(activity);
  this.pager = pager;
  this.tray = tray;
  this.dots = dots;
  dotsHeight = activity.dp(DOTS_DP);
  addView(pager, new LayoutParams(-1, -1));
  addView(tray, new LayoutParams(-1, tray.heightPx(), Gravity.BOTTOM));
  LayoutParams dotsParams = new LayoutParams(-1, dotsHeight, Gravity.BOTTOM);
  dotsParams.bottomMargin = dotsBottom();
  addView(dots, dotsParams);
 }

 static final int DOTS_DP = HomeAppLayout.DOTS_DP, DOTS_ABOVE_EDGE_DP = HomeAppLayout.DOTS_ABOVE_EDGE_DP;

 /** The dots' row is centred DOTS_ABOVE_EDGE_DP above the edge's top. */
 int dotsBottom() {
  return tray.heightPx() + Math.round(DOTS_ABOVE_EDGE_DP * tray.density) - dotsHeight / 2;
 }

 @Override public WindowInsets dispatchApplyWindowInsets(WindowInsets insets) {
  int left = insets.getSystemWindowInsetLeft(), right = insets.getSystemWindowInsetRight();
  int tappable = insets.getTappableElementInsets().bottom;
  // Under three-button navigation the edge grows by the bar's height and pads it out, so the controls stay centred in
  // the flat area above the bar (never partly behind it).
  if (tray.navigationExtra != tappable) { tray.navigationExtra = tappable; tray.setPadding(0, tray.getPaddingTop(), 0, tappable); }
  LayoutParams trayParams = (LayoutParams) tray.getLayoutParams();
  if (trayParams.height != tray.heightPx()) { trayParams.height = tray.heightPx(); tray.setLayoutParams(trayParams); }
  pager.dispatchApplyWindowInsets(insets.replaceSystemWindowInsets(left, insets.getSystemWindowInsetTop(), right,
    tray.heightPx() + Math.round(HomeAppLayout.BAND_BOTTOM_DP * tray.density)));
  LayoutParams dotsParams = (LayoutParams) dots.getLayoutParams();
  if (dotsParams.bottomMargin != dotsBottom() || dotsParams.leftMargin != left || dotsParams.rightMargin != right) {
   dotsParams.setMargins(left, 0, right, dotsBottom());
   dots.setLayoutParams(dotsParams);
  }
  return insets;
 }
}
