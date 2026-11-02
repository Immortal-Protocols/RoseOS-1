package dev.pixel.voice;

import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.provider.MediaStore;
import android.text.format.DateFormat;
import android.view.Gravity;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import android.widget.TextClock;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;

/**
 * Home, the first page (#410; the committed Oxblood Rose HiFi, main f6503a0, oxblood.css:353-360; model.js
 * HOME_PAGES_OXBLOOD): today's date at the upper left, and above the dots one unlabelled grid of system-app marks,
 * four across, in the app pages' cells (HomeAppTile) and band (HomeAppPage). Its apps are real: the owner's default
 * phone, messages, camera and browser apps, then Rose's Mail, Calendar and Notes where installed, then the System
 * folder (the reference's last cell), at most eight. A role with no single default, or an app that is not installed, is
 * left out rather than shown as a stand-in. Every other installed app is a swipe away. The Oxblood field is one
 * drawable behind the whole pager (MainActivity), so it stays continuous while the pages slide; this page draws none of
 * its own. Rose lives in the fixed edge (HomeTray); this page owns no voice state.
 */
final class RoseHomeView extends FrameLayout {
 static final int HOME_SIZE = 8, DATE_LEFT_DP = HomeAppLayout.DATE_LEFT_DP, DATE_TOP_DP = HomeAppLayout.DATE_TOP_DP,
   DATE_SP = HomeAppLayout.DATE_SP, DATE_INK = HomeAppLayout.DATE_INK;
 final TextClock date;

 /** entries: the Home apps in order, with null standing for the System folder cell. */
 RoseHomeView(MainActivity activity, List<ResolveInfo> entries, int columns) {
  super(activity);
  setContentDescription("Home");
  HomeAppPage grid = new HomeAppPage(activity, columns, HomeAppLayout.HOME_CELL_DP, HomeAppLayout.HOME_COL_GAP_DP, null);
  for (ResolveInfo app : entries) grid.addView(app == null ? activity.folderTile(false) : activity.appTile(app, false));
  int side = activity.dp(HomeAppLayout.MARGIN_DP), top = activity.dp(HomeAppLayout.TOP_DP);
  grid.setPadding(side, top, side, 0);
  grid.setOnApplyWindowInsetsListener((v, i) -> {
   v.setPadding(side + i.getSystemWindowInsetLeft(), top + i.getSystemWindowInsetTop(),
     side + i.getSystemWindowInsetRight(), i.getSystemWindowInsetBottom());
   return i;
  });
  addView(grid, new LayoutParams(-1, -1));

  // The date (oxblood.css:353): 22sp regular, warm ivory, no shadow, the owner's locale; TextClock keeps it current.
  date = new TextClock(activity);
  String pattern = DateFormat.getBestDateTimePattern(Locale.getDefault(), "EEEMMMd");
  date.setFormat12Hour(pattern);
  date.setFormat24Hour(pattern);
  date.setTextSize(DATE_SP);
  date.setTextColor(DATE_INK);
  date.setIncludeFontPadding(false);
  LayoutParams dateParams = new LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
  dateParams.leftMargin = activity.dp(DATE_LEFT_DP);
  dateParams.topMargin = activity.dp(DATE_TOP_DP);
  addView(date, dateParams);
  int dateTop = activity.dp(DATE_TOP_DP), dateLeft = activity.dp(DATE_LEFT_DP);
  date.setOnApplyWindowInsetsListener((v, i) -> {
   LayoutParams p = (LayoutParams) v.getLayoutParams();
   // Never under the status bar or a cutout: the reference's 65dp, or 21dp below the inset where that is lower.
   int wantTop = Math.max(dateTop, i.getSystemWindowInsetTop() + activity.dp(21)), wantLeft = dateLeft + i.getSystemWindowInsetLeft();
   if (p.topMargin != wantTop || p.leftMargin != wantLeft) { p.topMargin = wantTop; p.leftMargin = wantLeft; v.setLayoutParams(p); }
   return i;
  });
 }

 /**
  * Home's apps, in order: the owner's default phone, messages, camera and browser apps (taken from the launchable
  * apps), then the suite's Mail, Calendar and Notes (HomeAppGroups.HOME_SUITE) where installed and not already here,
  * at most HOME_SIZE - 1, followed by null for the System folder when there is one.
  */
 static List<ResolveInfo> homeApps(PackageManager pm, String self, List<ResolveInfo> launchable, boolean folder) {
  ArrayList<ResolveInfo> home = new ArrayList<>();
  HashSet<String> used = new HashSet<>();
  ArrayList<String> wanted = new ArrayList<>(dockRolePackages(pm, self));
  for (String pkg : HomeAppGroups.HOME_SUITE) wanted.add(pkg);
  for (String pkg : wanted) {
   if (home.size() == HOME_SIZE - 1) break;
   if (!used.add(pkg)) continue;
   for (ResolveInfo app : launchable) if (pkg.equals(app.activityInfo.packageName)) { home.add(app); break; }
  }
  if (folder) home.add(null);
  return home;
 }

 /** The packages holding the default phone, messages, camera and browser roles, in order, without enumerating apps:
  * four resolutions, cheap enough to compare on every HOME so a changed default app rebuilds Home. */
 static List<String> dockRolePackages(PackageManager pm, String self) {
  Intent[] roles = {
   new Intent(Intent.ACTION_DIAL, Uri.parse("tel:")),
   new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:")),
   new Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA),
   new Intent(Intent.ACTION_VIEW, Uri.parse("https://"))
  };
  ArrayList<String> packages = new ArrayList<>();
  HashSet<String> used = new HashSet<>();
  for (Intent role : roles) {
   ResolveInfo handler = pm.resolveActivity(role, PackageManager.MATCH_DEFAULT_ONLY);
   String pkg = handler == null || handler.activityInfo == null ? null : handler.activityInfo.packageName;
   if (pkg == null || "android".equals(pkg) || pkg.equals(self) || !used.add(pkg)) continue;
   packages.add(pkg);
  }
  return packages;
 }

 @Override public WindowInsets dispatchApplyWindowInsets(WindowInsets insets) {
  for (int i = 0; i < getChildCount(); i++) getChildAt(i).dispatchApplyWindowInsets(insets);
  return insets;
 }
}
