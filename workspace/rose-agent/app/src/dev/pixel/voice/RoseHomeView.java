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
 * HOME_PAGES_OXBLOOD, with the owner's density correction, #410 comment 6592423187): today's date at the upper left,
 * and below it one unlabelled grid of app marks, four across, in the app pages' cells (HomeAppTile), filling the rows
 * that fit down to the reserve above the dots (HomeAppPage). Its apps are real, in order: the owner's default phone,
 * messages, camera and browser apps, then Rose's Mail, Calendar and Notes where installed (at most CURATED), then the
 * start of the normal app inventory, which the app pages continue (MainActivity; no app is placed twice to fill
 * space), and the System folder as the last cell. A role with no single default, or an app that is not installed, is
 * left out rather than shown as a stand-in. The Oxblood field is one drawable behind the whole pager (MainActivity),
 * so it stays continuous while the pages slide; this page draws none of its own. Rose lives in the fixed edge
 * (HomeTray); this page owns no voice state.
 */
final class RoseHomeView extends FrameLayout {
 static final int CURATED = 7, DATE_LEFT_DP = HomeAppLayout.DATE_LEFT_DP, DATE_TOP_DP = HomeAppLayout.DATE_TOP_DP,
   DATE_SP = HomeAppLayout.DATE_SP, DATE_INK = HomeAppLayout.DATE_INK;
 final TextClock date;

 /** entries: the Home apps in order, with null standing for the System folder cell; fit: told the grid's measured
  * area below the date and its cell height, so MainActivity can re-plan Home's capacity from the real viewport. */
 RoseHomeView(MainActivity activity, List<ResolveInfo> entries, int columns, HomeAppPage.Fit fit) {
  super(activity);
  setContentDescription("Home");
  // The date (oxblood.css:353): 22sp regular, warm ivory, no shadow, the owner's locale; TextClock keeps it current.
  date = new TextClock(activity);
  String pattern = DateFormat.getBestDateTimePattern(Locale.getDefault(), "EEEMMMd");
  date.setFormat12Hour(pattern);
  date.setFormat24Hour(pattern);
  date.setTextSize(DATE_SP);
  date.setTextColor(RoseThemeNow.c(DATE_INK));
  // Nov 7 plan 1O: the date is the one always-visible Home text, so it carries a chosen font; default is unchanged.
  String font = CustomizationStore.read(activity).font;
  if (!RoseFonts.DEFAULT.equals(font)) date.setTypeface(CustomizationVisuals.labelTypeface(activity, font, 400));
  date.setIncludeFontPadding(false);
  int dateTop = activity.dp(DATE_TOP_DP), dateLeft = activity.dp(DATE_LEFT_DP);

  HomeAppPage grid = new HomeAppPage(activity, columns, HomeAppLayout.HOME_CELL_DP, HomeAppLayout.HOME_COL_GAP_DP, fit);
  for (ResolveInfo app : entries) grid.addView(app == null ? activity.folderTile(false) : activity.appTile(app, false));
  int side = activity.dp(HomeAppLayout.MARGIN_DP);
  float density = activity.getResources().getDisplayMetrics().density;
  grid.setPadding(side, Math.round(HomeAppLayout.homeTop(dateTop, date.getLineHeight(), density)), side, 0);
  grid.setOnApplyWindowInsetsListener((v, i) -> {
   // The grid starts below the date's line, wherever the insets put the date (below).
   int wantTop = Math.max(dateTop, i.getSystemWindowInsetTop() + activity.dp(21));
   v.setPadding(side + i.getSystemWindowInsetLeft(), Math.round(HomeAppLayout.homeTop(wantTop, date.getLineHeight(), density)),
     side + i.getSystemWindowInsetRight(), i.getSystemWindowInsetBottom());
   return i;
  });
  addView(grid, new LayoutParams(-1, -1));

  LayoutParams dateParams = new LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
  dateParams.leftMargin = dateLeft;
  dateParams.topMargin = dateTop;
  addView(date, dateParams);
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
  * apps; GrapheneOS Messaging's place goes to Rose's Messages, HomeAppGroups.homeRole), then the suite's Mail, Calendar and Notes (HomeAppGroups.HOME_SUITE) where installed and not already here,
  * at most CURATED, followed by null for the System folder when folder is set (MainActivity asks without it and places
  * the folder after Home's share of the inventory).
  */
 static List<ResolveInfo> homeApps(PackageManager pm, String self, List<ResolveInfo> launchable, boolean folder) {
  ArrayList<ResolveInfo> home = new ArrayList<>();
  HashSet<String> used = new HashSet<>();
  ArrayList<String> wanted = new ArrayList<>();
  for (String pkg : dockRolePackages(pm, self)) wanted.add(HomeAppGroups.homeRole(pkg)); // F04: never GrapheneOS Messaging
  for (String pkg : HomeAppGroups.HOME_SUITE) wanted.add(pkg);
  for (String pkg : wanted) {
   if (home.size() == CURATED) break;
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
