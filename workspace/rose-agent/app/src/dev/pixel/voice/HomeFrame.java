package dev.pixel.voice;

import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.FrameLayout;

/** The pager with the fixed HomeTray floating over its bottom edge (OS-HIFI-HOME-001) and the one row of page
 * dots just above it (OS-PAGING-158-M001); neither moves while the pages slide. The pages still run full
 * height, so the shared wallpaper continues behind the tray, but they receive the tray's and dots' height as
 * extra bottom inset and keep their content above both. The tray itself sits above the system navigation
 * inset; production status and navigation bars are unchanged. */
final class HomeFrame extends FrameLayout {
 final LauncherPager pager;
 final HomeTray tray;
 final View dots;
 final int side, gap, dotsHeight, reserved;

 HomeFrame(MainActivity activity, LauncherPager pager, HomeTray tray, View dots) {
  super(activity);
  this.pager = pager;
  this.tray = tray;
  this.dots = dots;
  side = activity.dp(HomeTray.SIDE_DP);
  gap = activity.dp(HomeTray.GAP_DP);
  dotsHeight = activity.dp(DOTS_DP);
  reserved = activity.dp(HomeTray.HEIGHT_DP) + 2 * gap + dotsHeight;
  addView(pager, new LayoutParams(-1, -1));
  LayoutParams trayParams = new LayoutParams(-1, activity.dp(HomeTray.HEIGHT_DP), Gravity.BOTTOM);
  trayParams.setMargins(side, 0, side, gap);
  addView(tray, trayParams);
  LayoutParams dotsParams = new LayoutParams(-1, dotsHeight, Gravity.BOTTOM);
  dotsParams.setMargins(side, 0, side, dotsBottom(0));
  addView(dots, dotsParams);
 }

 static final int DOTS_DP = 44;

 /** The dots sit one gap above the tray. */
 int dotsBottom(int navigationInset) {
  return navigationInset + gap + tray.getLayoutParams().height + gap / 2;
 }

 @Override public WindowInsets dispatchApplyWindowInsets(WindowInsets insets) {
  int left = insets.getSystemWindowInsetLeft(), right = insets.getSystemWindowInsetRight();
  int bottom = insets.getSystemWindowInsetBottom();
  pager.dispatchApplyWindowInsets(insets.replaceSystemWindowInsets(left, insets.getSystemWindowInsetTop(), right,
    bottom + reserved));
  LayoutParams trayParams = (LayoutParams) tray.getLayoutParams();
  if (trayParams.leftMargin != side + left || trayParams.rightMargin != side + right || trayParams.bottomMargin != gap + bottom) {
   trayParams.setMargins(side + left, 0, side + right, gap + bottom);
   tray.setLayoutParams(trayParams);
  }
  LayoutParams dotsParams = (LayoutParams) dots.getLayoutParams();
  if (dotsParams.bottomMargin != dotsBottom(bottom) || dotsParams.leftMargin != side + left) {
   dotsParams.setMargins(side + left, 0, side + right, dotsBottom(bottom));
   dots.setLayoutParams(dotsParams);
  }
  return insets;
 }
}
