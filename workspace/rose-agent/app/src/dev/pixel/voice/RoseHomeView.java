package dev.pixel.voice;

import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

/**
 * Launcher Home page from the Rose-Fold-v3 prototype (narrow Home): a light dock of real installed apps over the
 * prototype wallpaper. The wallpaper is one Wallpaper drawable behind the whole pager (OS-PAGING-158-M001), so it
 * stays continuous while Home and the app pages slide over it; this page draws none of its own. Rose lives in the
 * fixed HomeTray, drawn by App's RosePresenceView; this page owns no voice state.
 */
final class RoseHomeView extends FrameLayout {
 static final int DOCK_SIZE = 4;
 /** Decoded once per process: MainActivity rebuilds its pages on every HOME press. */
 static Bitmap cachedWallpaper;

 RoseHomeView(MainActivity activity, List<ResolveInfo> dock, Runnable openApps, View pageDots) {
  super(activity);
  setContentDescription("Home");
  LinearLayout column = new LinearLayout(activity);
  column.setOrientation(LinearLayout.VERTICAL);
  column.setGravity(Gravity.CENTER_HORIZONTAL);
  int side = activity.dp(18), top = activity.dp(20), bottom = activity.dp(4);
  column.setPadding(side, top, side, bottom);
  column.setOnApplyWindowInsetsListener((v, i) -> {
   v.setPadding(side + i.getSystemWindowInsetLeft(), top + i.getSystemWindowInsetTop(),
     side + i.getSystemWindowInsetRight(), bottom + i.getSystemWindowInsetBottom());
   return i;
  });
  addView(column, new LayoutParams(-1, -1));

  // Rose now sits in the fixed HomeTray (OS-HIFI-HOME-001); the wallpaper fills the space above the dock.
  column.addView(new View(activity), new LinearLayout.LayoutParams(-1, 0, 1));

  LinearLayout tray = new LinearLayout(activity);
  tray.setGravity(Gravity.CENTER_VERTICAL);
  tray.setPadding(activity.dp(8), activity.dp(12), activity.dp(8), activity.dp(12));
  GradientDrawable trayBack = new GradientDrawable();
  trayBack.setColor(0xd9fafcf4);
  trayBack.setCornerRadius(activity.dp(24));
  tray.setBackground(trayBack);
  for (ResolveInfo app : dock) {
   String label = AppLabels.label(activity.getPackageManager(), app.activityInfo.packageName,
     app.loadLabel(activity.getPackageManager()).toString());
   int iconResource = AppLabels.icon(activity.getPackageManager(), app.activityInfo.packageName);
   ImageView icon = new ImageView(activity);
   icon.setImageDrawable(iconResource == 0 ? app.loadIcon(activity.getPackageManager()) : activity.getDrawable(iconResource));
   tray.addView(dockTile(activity, icon, label, () -> WorkspaceLauncher.open(activity, app, label)), new LinearLayout.LayoutParams(0, -1, 1));
  }
  TextView glyph = activity.text("▦", 30, 0xff3b5547);
  glyph.setGravity(Gravity.CENTER);
  tray.addView(dockTile(activity, glyph, "All apps", openApps), new LinearLayout.LayoutParams(0, -1, 1));
  LinearLayout.LayoutParams trayParams = new LinearLayout.LayoutParams(-1, -2);
  trayParams.bottomMargin = activity.dp(8);
  column.addView(tray, trayParams);
  column.addView(pageDots, new LinearLayout.LayoutParams(-1, activity.dp(30)));
 }

 /** A plain icon over its label, as in the prototype dock; no tile background. */
 static View dockTile(MainActivity activity, View icon, String label, Runnable open) {
  LinearLayout tile = new LinearLayout(activity);
  tile.setOrientation(LinearLayout.VERTICAL);
  // Top-aligned at the tray's tallest tile, so icons stay level when one label takes two lines.
  tile.setGravity(Gravity.CENTER_HORIZONTAL | Gravity.TOP);
  tile.setPadding(activity.dp(4), activity.dp(8), activity.dp(4), activity.dp(6));
  tile.setClickable(true);
  tile.setFocusable(true);
  tile.setContentDescription(label);
  tile.setOnClickListener(v -> open.run());
  icon.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  if (icon instanceof ImageView) ((ImageView) icon).setScaleType(ImageView.ScaleType.FIT_CENTER);
  tile.addView(icon, new LinearLayout.LayoutParams(activity.dp(46), activity.dp(46)));
  DockLabelView name = new DockLabelView(activity, label);
  name.setTextSize(12);
  name.setTextColor(0xff22382e);
  name.setGravity(Gravity.CENTER_HORIZONTAL);
  name.setTypeface(activity.chatFont(800));
  name.setIncludeFontPadding(false);
  name.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  LinearLayout.LayoutParams nameParams = new LinearLayout.LayoutParams(-1, -2);
  nameParams.topMargin = activity.dp(10);
  tile.addView(name, nameParams);
  return tile;
 }

 /** A dock label fitted to its tile width by DockLabel: the whole name, never a one-line cut. */
 static final class DockLabelView extends TextView {
  final String label;
  float basePx;

  DockLabelView(MainActivity activity, String label) {
   super(activity);
   this.label = label;
   setText(label);
  }

  /** The dock size the owner's font scale gives; fitting may go down to 10/12 of it. */
  @Override public void setTextSize(float sp) {
   super.setTextSize(sp);
   basePx = getTextSize();
  }

  @Override protected void onMeasure(int widthSpec, int heightSpec) {
   float available = MeasureSpec.getSize(widthSpec) - getPaddingLeft() - getPaddingRight();
   if (available > 0 && basePx > 0) {
    android.text.TextPaint paint = new android.text.TextPaint(getPaint());
    DockLabel fit = DockLabel.fit(label, basePx, basePx * 10 / 12, available, (text, size) -> {
     paint.setTextSize(size);
     float widest = 0;
     for (String line : text.split("\n")) widest = Math.max(widest, paint.measureText(line));
     return widest;
    });
    if (!fit.text.contentEquals(getText())) setText(fit.text);
    if (getTextSize() != fit.sizePx) super.setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fit.sizePx);
    // Each setter runs only on a change, so a measure pass never requests another layout once settled.
    if (getMaxLines() != fit.lines) setMaxLines(fit.lines);
    android.text.TextUtils.TruncateAt end = fit.ellipsize ? android.text.TextUtils.TruncateAt.END : null;
    if (getEllipsize() != end) setEllipsize(end);
   }
   super.onMeasure(widthSpec, heightSpec);
  }
 }


 /**
  * The owner's default phone, messages, camera and browser apps, in that order, taken from the
  * launchable apps. A role with no single default (a chooser) or no launchable activity is left out.
  */
 static List<ResolveInfo> dockApps(PackageManager pm, String self, List<ResolveInfo> launchable) {
  ArrayList<ResolveInfo> dock = new ArrayList<>();
  for (String pkg : dockRolePackages(pm, self)) {
   if (dock.size() == DOCK_SIZE) break;
   for (ResolveInfo app : launchable) if (pkg.equals(app.activityInfo.packageName)) { dock.add(app); break; }
  }
  return dock;
 }

 /** The packages holding those defaults, in order, without enumerating apps: four resolutions, cheap
  * enough to compare on every HOME so a changed default app rebuilds the dock. */
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

 /** The shared Home wallpaper, the same bytes and decode as before, drawn behind the pager at its scroll
  * position. CSS background-size: cover, centered: scale to fill and crop the overflow equally. */
 static final class Wallpaper extends Drawable {
  final Bitmap wallpaper;
  final Rect source = new Rect();
  final Paint picture = new Paint(Paint.FILTER_BITMAP_FLAG);

  Wallpaper(android.content.res.Resources resources) {
   if (cachedWallpaper == null) cachedWallpaper = BitmapFactory.decodeResource(resources, R.drawable.rose_home_wallpaper);
   wallpaper = cachedWallpaper;
  }

  @Override protected void onBoundsChange(Rect bounds) {
   int w = bounds.width(), h = bounds.height();
   source.setEmpty();
   if (wallpaper == null || w <= 0 || h <= 0) return;
   int bw = wallpaper.getWidth(), bh = wallpaper.getHeight();
   if ((long) bw * h > (long) bh * w) {
    int cropW = Math.round(bh * (float) w / h);
    source.set((bw - cropW) / 2, 0, (bw + cropW) / 2, bh);
   } else {
    int cropH = Math.round(bw * (float) h / w);
    source.set(0, (bh - cropH) / 2, bw, (bh + cropH) / 2);
   }
  }

  @Override public void draw(Canvas canvas) {
   if (wallpaper != null && !source.isEmpty()) canvas.drawBitmap(wallpaper, source, getBounds(), picture);
   else canvas.drawColor(0xffdfe8e0);
  }
  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.OPAQUE; }
 }
}
