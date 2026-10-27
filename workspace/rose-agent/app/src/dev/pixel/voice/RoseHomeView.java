package dev.pixel.voice;

import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Shader;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

/**
 * Launcher Home page: wallpaper, a compact Rose entry and a dock of real installed apps.
 * It owns no voice state; the Rose entry only navigates to the existing conversation page.
 */
final class RoseHomeView extends FrameLayout {
 static final int DOCK_SIZE = 4;
 final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
 final Path shape = new Path();

 RoseHomeView(MainActivity activity, List<ResolveInfo> dock, Runnable openRose, Runnable openApps, View pageDots) {
  super(activity);
  setWillNotDraw(false);
  setContentDescription("Home");
  LinearLayout column = new LinearLayout(activity);
  column.setOrientation(LinearLayout.VERTICAL);
  column.setGravity(Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
  int side = activity.dp(18), top = activity.dp(20), bottom = activity.dp(12);
  column.setPadding(side, top, side, bottom);
  column.setOnApplyWindowInsetsListener((v, i) -> {
   v.setPadding(side + i.getSystemWindowInsetLeft(), top + i.getSystemWindowInsetTop(),
     side + i.getSystemWindowInsetRight(), bottom + i.getSystemWindowInsetBottom());
   return i;
  });
  addView(column, new LayoutParams(-1, -1));

  LinearLayout.LayoutParams roseParams = new LinearLayout.LayoutParams(-2, activity.dp(56));
  roseParams.bottomMargin = activity.dp(22);
  column.addView(roseEntry(activity, openRose), roseParams);

  LinearLayout tray = new LinearLayout(activity);
  tray.setGravity(Gravity.CENTER);
  tray.setPadding(activity.dp(6), activity.dp(4), activity.dp(6), activity.dp(2));
  GradientDrawable trayBack = new GradientDrawable();
  trayBack.setColor(PixelAppearance.color(0xd9fffaf2));
  trayBack.setCornerRadius(activity.dp(30));
  tray.setBackground(trayBack);
  for (ResolveInfo app : dock) tray.addView(activity.appTile(app, false), new LinearLayout.LayoutParams(0, -2, 1));
  tray.addView(appsTile(activity, openApps), new LinearLayout.LayoutParams(0, -2, 1));
  column.addView(tray, new LinearLayout.LayoutParams(-1, -2));
  column.addView(pageDots, new LinearLayout.LayoutParams(-1, activity.dp(30)));
 }

 /** Opens the conversation surface; never starts, mutes or ends the voice session. */
 static View roseEntry(MainActivity activity, Runnable openRose) {
  LinearLayout entry = new LinearLayout(activity);
  entry.setGravity(Gravity.CENTER_VERTICAL);
  entry.setPadding(activity.dp(18), 0, activity.dp(24), 0);
  GradientDrawable back = new GradientDrawable();
  back.setColor(PixelAppearance.color(0xf2fffaf2));
  back.setCornerRadius(activity.dp(28));
  entry.setBackground(back);
  View mark = new View(activity);
  GradientDrawable dot = new GradientDrawable(GradientDrawable.Orientation.TL_BR,
    new int[]{PixelAppearance.color(0xffffc7b8), PixelAppearance.color(0xff9fc3e6)});
  dot.setShape(GradientDrawable.OVAL);
  mark.setBackground(dot);
  mark.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  LinearLayout.LayoutParams markParams = new LinearLayout.LayoutParams(activity.dp(26), activity.dp(26));
  markParams.rightMargin = activity.dp(12);
  entry.addView(mark, markParams);
  TextView name = activity.text("Rose", 20, activity.ink);
  name.setTypeface(activity.chatFont(900));
  name.setIncludeFontPadding(false);
  name.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  entry.addView(name);
  entry.setClickable(true);
  entry.setFocusable(true);
  entry.setContentDescription("Open Rose conversation");
  entry.setOnClickListener(v -> openRose.run());
  return entry;
 }

 /** Same shape as an app tile, opening the existing installed-apps pages. */
 static View appsTile(MainActivity activity, Runnable openApps) {
  LinearLayout tile = new LinearLayout(activity);
  tile.setOrientation(LinearLayout.VERTICAL);
  tile.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
  tile.setPadding(activity.dp(2), activity.dp(12), activity.dp(2), activity.dp(4));
  tile.setClickable(true);
  tile.setFocusable(true);
  tile.setContentDescription("All apps");
  tile.setOnClickListener(v -> openApps.run());
  FrameLayout frame = new FrameLayout(activity);
  frame.setBackground(activity.raised(PixelAppearance.color(0xfffff7e5), PixelAppearance.color(0xff789ab5), 18));
  TextView glyph = activity.text("▦", 28, activity.secondary);
  glyph.setGravity(Gravity.CENTER);
  glyph.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  frame.addView(glyph, new FrameLayout.LayoutParams(-1, -1));
  tile.addView(frame, new LinearLayout.LayoutParams(activity.dp(68), activity.dp(70)));
  TextView label = activity.text("All apps", 12, activity.ink);
  label.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
  label.setTypeface(activity.chatFont(800));
  label.setIncludeFontPadding(false);
  label.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(-1, activity.dp(36));
  labelParams.topMargin = activity.dp(8);
  tile.addView(label, labelParams);
  return tile;
 }

 /**
  * The owner's default phone, messages, camera and browser apps, in that order, taken from the
  * launchable apps. A role with no single default (a chooser) or no launchable activity is left out.
  */
 static List<ResolveInfo> dockApps(PackageManager pm, String self, List<ResolveInfo> launchable) {
  Intent[] roles = {
   new Intent(Intent.ACTION_DIAL, Uri.parse("tel:")),
   new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:")),
   new Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA),
   new Intent(Intent.ACTION_VIEW, Uri.parse("https://"))
  };
  ArrayList<ResolveInfo> dock = new ArrayList<>();
  HashSet<String> used = new HashSet<>();
  for (Intent role : roles) {
   if (dock.size() == DOCK_SIZE) break;
   ResolveInfo handler = pm.resolveActivity(role, PackageManager.MATCH_DEFAULT_ONLY);
   String pkg = handler == null || handler.activityInfo == null ? null : handler.activityInfo.packageName;
   if (pkg == null || "android".equals(pkg) || pkg.equals(self) || !used.add(pkg)) continue;
   for (ResolveInfo app : launchable) if (pkg.equals(app.activityInfo.packageName)) { dock.add(app); break; }
  }
  return dock;
 }

 @Override public WindowInsets dispatchApplyWindowInsets(WindowInsets insets) {
  for (int i = 0; i < getChildCount(); i++) getChildAt(i).dispatchApplyWindowInsets(insets);
  return insets;
 }

 /** Calm lake-and-mountain wallpaper drawn to fit any size; an approximation, not the prototype image. */
 @Override protected void onDraw(Canvas canvas) {
  float w = getWidth(), h = getHeight();
  if (w <= 0 || h <= 0) return;
  paint.setShader(new LinearGradient(0, 0, 0, h * .62f,
    PixelAppearance.color(0xffd8e8f3), PixelAppearance.color(0xfff6efe4), Shader.TileMode.CLAMP));
  canvas.drawRect(0, 0, w, h * .62f, paint);
  paint.setShader(null);
  ridge(canvas, w, h * .62f, new float[]{0, .40f, .12f, .30f, .27f, .37f, .45f, .26f, .63f, .35f, .80f, .29f, 1, .40f},
    PixelAppearance.color(0xffbccbd9));
  ridge(canvas, w, h * .62f, new float[]{0, .50f, .18f, .44f, .36f, .52f, .58f, .42f, .78f, .50f, 1, .46f},
    PixelAppearance.color(0xff9fb3c6));
  paint.setShader(new LinearGradient(0, h * .62f, 0, h * .84f,
    PixelAppearance.color(0xffb7cfdf), PixelAppearance.color(0xffd9e6ee), Shader.TileMode.CLAMP));
  canvas.drawRect(0, h * .62f, w, h * .84f, paint);
  paint.setShader(null);
  paint.setColor(PixelAppearance.color(0xffece2d1));
  shape.reset();
  shape.moveTo(0, h * .84f);
  shape.quadTo(w * .5f, h * .80f, w, h * .85f);
  shape.lineTo(w, h);
  shape.lineTo(0, h);
  shape.close();
  canvas.drawPath(shape, paint);
 }

 /** Points are x, y pairs: fractions of the width and of the height down to the horizon. */
 void ridge(Canvas canvas, float w, float horizon, float[] points, int color) {
  paint.setColor(color);
  shape.reset();
  shape.moveTo(0, horizon);
  for (int i = 0; i < points.length; i += 2) shape.lineTo(points[i] * w, points[i + 1] * horizon);
  shape.lineTo(w, horizon);
  shape.close();
  canvas.drawPath(shape, paint);
 }
}
