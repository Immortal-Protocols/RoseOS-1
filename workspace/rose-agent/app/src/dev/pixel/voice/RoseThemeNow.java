package dev.pixel.voice;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

/** Plan 1E: the Rose theme this process draws now, and its artwork, for Rose Agent's own surfaces (Home, Rose).
 *
 * <p>The choice lives in CustomizationStore (theme), which updates {@link #id} on every read, so a surface built after a
 * change draws it. Surfaces resolve their colours through {@link #c} (artwork colour, RoseTheme.map) and {@link #w}
 * (warning, destructive and muted colours, RoseTheme.warm) when they are built or drawn; Home and Rose rebuild on a
 * theme change. {@link #prepare} also decodes the theme's raster wallpaper (its RoseTheme.wallpaperDrawable) once; when that fails, the
 * field falls back to its own geometry in the theme's colours, never to another theme's wallpaper. */
final class RoseThemeNow {
 private RoseThemeNow() {}

 static volatile String id = RoseTheme.DEFAULT;
 private static volatile Bitmap raster;
 private static volatile String rasterTheme;

 /** Reads the stored theme and makes its raster wallpaper ready; call before building a surface. */
 static void prepare(Context ctx) {
  String t = CustomizationStore.read(ctx).theme;
  id = t;
  if (RoseTheme.rasterWallpaper(t) && !t.equals(rasterTheme)) {
   try {
    BitmapFactory.Options o = new BitmapFactory.Options();
    o.inScaled = false;
    // Each raster theme ships its wallpaper as its own named drawable (themes/rose-themes.json, copied by
    // scripts/rose-themes.py); test_rose_themes checks every one exists in this package.
    int res = ctx.getResources().getIdentifier(RoseTheme.wallpaperDrawable(t), "drawable", ctx.getPackageName());
    Bitmap b = res == 0 ? null : BitmapFactory.decodeResource(ctx.getResources(), res, o);
    if (b != null) { raster = b; rasterTheme = t; }
   } catch (RuntimeException | OutOfMemoryError unavailable) { raster = null; rasterTheme = null; }
  }
 }

 /** The raster wallpaper of theme, or null (a procedural theme, or not decoded). */
 static Bitmap raster(String theme) { return theme != null && theme.equals(rasterTheme) ? raster : null; }

 static int c(int argb) { return RoseTheme.map(id, argb); }
 static int w(int argb) { return RoseTheme.warm(id, argb); }
 static int[] c(int[] colors) { return RoseTheme.map(id, colors); }
}
