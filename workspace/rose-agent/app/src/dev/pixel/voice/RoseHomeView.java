package dev.pixel.voice;

import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.drawable.Drawable;
import android.net.Uri;
import android.provider.MediaStore;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

/**
 * Launcher Home page of the selected HiFi (prototypes/rose-hifi at ce126f56, the sparse first page): the owner's
 * default phone, messages, camera and browser apps as a plain grid on the wallpaper, in the same cells as the app
 * pages, above the fixed page dots and HomeTray. There is no second shortcut bar: the tray is the one persistent
 * control bar (#157), and every other installed app is a swipe or a page dot away. The wallpaper is one Wallpaper
 * drawable behind the whole pager (OS-PAGING-158-M001), so it stays continuous while Home and the app pages slide
 * over it; this page draws none of its own. Rose lives in the fixed HomeTray, drawn by App's RosePresenceView;
 * this page owns no voice state.
 */
final class RoseHomeView extends FrameLayout {
 static final int DOCK_SIZE = 4;
 /** Decoded once per process: MainActivity rebuilds its pages on every HOME press. */
 static Bitmap cachedWallpaper;

 RoseHomeView(MainActivity activity, List<ResolveInfo> dock, int columns) {
  super(activity);
  setContentDescription("Home");
  // The app pages' own grid and cells (MainActivity.appTile): same tile, label and launch path, bottom-aligned
  // above the dots; Home only differs in its column count. No measurement report: Home never re-plans the pages.
  HomeAppPage grid = new HomeAppPage(activity, columns, null);
  for (ResolveInfo app : dock) grid.addView(activity.appTile(app));
  int side = activity.dp(HomeAppLayout.MARGIN_DP), top = activity.dp(HomeAppLayout.TOP_DP);
  grid.setPadding(side, top, side, 0);
  grid.setOnApplyWindowInsetsListener((v, i) -> {
   v.setPadding(side + i.getSystemWindowInsetLeft(), top + i.getSystemWindowInsetTop(),
     side + i.getSystemWindowInsetRight(), i.getSystemWindowInsetBottom());
   return i;
  });
  addView(grid, new LayoutParams(-1, -1));
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
