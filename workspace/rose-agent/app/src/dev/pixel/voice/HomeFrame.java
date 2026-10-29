package dev.pixel.voice;

import android.view.Gravity;
import android.view.WindowInsets;
import android.widget.FrameLayout;

/** The pager with the fixed HomeTray floating over its bottom edge (OS-HIFI-HOME-001). The pages still
 * run full height, so Home's wallpaper continues behind the tray, but they receive the tray's height as
 * extra bottom inset and keep their content above it. The tray itself sits above the system navigation
 * inset; production status and navigation bars are unchanged. */
final class HomeFrame extends FrameLayout {
 final LauncherPager pager;
 final HomeTray tray;
 final int side, gap, reserved;

 HomeFrame(MainActivity activity, LauncherPager pager, HomeTray tray) {
  super(activity);
  this.pager = pager;
  this.tray = tray;
  side = activity.dp(HomeTray.SIDE_DP);
  gap = activity.dp(HomeTray.GAP_DP);
  reserved = activity.dp(HomeTray.HEIGHT_DP) + 2 * gap;
  addView(pager, new LayoutParams(-1, -1));
  LayoutParams trayParams = new LayoutParams(-1, activity.dp(HomeTray.HEIGHT_DP), Gravity.BOTTOM);
  trayParams.setMargins(side, 0, side, gap);
  addView(tray, trayParams);
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
  return insets;
 }
}
