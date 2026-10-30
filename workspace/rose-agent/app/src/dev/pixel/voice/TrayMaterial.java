package dev.pixel.voice;

import android.graphics.Bitmap;
import android.graphics.BitmapShader;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * The one tray material, shared by the Home tray and the conversation tray so both read as one system. #157: matched
 * to Nathan's selected Home image (HiFi Prototype Plan, Screenshot_2026-10-29_at_10.57.17_AM.png, 790x1580, sha256
 * 109faa230ca63744dc393a65d03ae525ca1e0d77bf98e0db5a423c670bccd1b4), measured from its pixels: a mid-silver brushed
 * bar with small corners and two clear oval glass wells. Earlier values came from prototypes/rose-hifi at ce126f56.
 * Decorative drawables only: the views that carry them keep their clicks, names and state.
 */
final class TrayMaterial {
 private TrayMaterial() {}

 /** The pocket drawn around each well's glass (box-shadow spreads of 1, 4 and 5px plus a 1px lower lip). A well
  * view is its glass plus this margin on every side, so nothing is drawn outside the view. */
 static final int POCKET_DP = 5;

 /**
  * .tray: brushed light metal, 22dp corners. A vertical #f6f7f9 → #e3e7eb (38%) → #cfd5db (62%) → #e8ebee
  * gradient, a fine horizontal grain (the reference's fractal-noise --brush at overlay; here a fixed seeded set of
  * hairlines, an approximation), one 100-degree specular band (white .55 at 44%, clear at 28% and 60%), a white top
  * rim and a dark lower rim. The drop shadow is the view's elevation, cast from this outline.
  */
 static final class Tray extends Drawable {
  /** #157: the selected image's metal, averaged down the bar between the wells (top #c1c3c9, middle #c3c5ca,
   * bottom #c6c8cd): a mid silver, much darker than the earlier near-white prototype output. Its corners are small
   * (about 13% of the bar height), it has a bright top edge and a darker lower edge, and only a faint broad sheen. */
  static final int[] METAL = {0xffc4c6cc, 0xffc1c3c9, 0xffc3c5ca, 0xffc6c8cd};
  static final float[] METAL_STOPS = {0f, .38f, .62f, 1f};
  static final int[] SPECULAR = {0x00ffffff, 0x33ffffff, 0x00ffffff};
  static final float[] SPECULAR_STOPS = {.28f, .44f, .60f};
  static final int TOP_RIM = 0xffffffff, LOWER_RIM = 0x663c4b5f, RADIUS_DP = 12;

  /** Made once per display density: Home rebuilds its tray on every HOME press. */
  static Bitmap grainTile;
  static float grainDensity;
  final float density, radius;
  final Paint metal = new Paint(Paint.ANTI_ALIAS_FLAG), grain = new Paint(Paint.ANTI_ALIAS_FLAG),
    specular = new Paint(Paint.ANTI_ALIAS_FLAG), rim = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF box = new RectF();

  Tray(float density) {
   this.density = density;
   radius = RADIUS_DP * density;
   rim.setStyle(Paint.Style.STROKE);
   rim.setStrokeWidth(Math.max(1f, density));
   if (grainTile == null || grainDensity != density) { grainTile = grain(density); grainDensity = density; }
   grain.setShader(new BitmapShader(grainTile, Shader.TileMode.REPEAT, Shader.TileMode.REPEAT));
  }

  /** Grain alpha per shade step (shades -8..8). The selected image shows a visible horizontal brush on the bar. */
  static final int GRAIN = 2;
  /** A 180 x 7dp tile of horizontal hairlines, light and dark by a fixed seed (CSS background-size 180px 7px). */
  static Bitmap grain(float density) {
   int w = Math.max(1, Math.round(180 * density)), h = Math.max(1, Math.round(7 * density));
   Bitmap tile = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
   Canvas canvas = new Canvas(tile);
   Paint line = new Paint();
   long seed = 7;
   for (int y = 0; y < h; y++) for (int x = 0; x < w; ) {
    seed = seed * 6364136223846793005L + 1442695040888963407L;
    int run = 6 + (int) ((seed >>> 33) % 40), shade = (int) ((seed >>> 20) % 17) - 8;
    line.setColor(shade >= 0 ? (shade * GRAIN) << 24 | 0xffffff : (-shade * GRAIN) << 24 | 0x3c4b5f);
    canvas.drawRect(x, y, Math.min(w, x + run), y + 1, line);
    x += run;
   }
   return tile;
  }

  @Override protected void onBoundsChange(Rect bounds) {
   box.set(bounds);
   metal.setShader(new LinearGradient(0, box.top, 0, box.bottom, METAL, METAL_STOPS, Shader.TileMode.CLAMP));
   // CSS 100deg: from the left, leaning slightly downwards across the bar.
   double angle = Math.toRadians(100);
   float dx = (float) Math.sin(angle) * box.width() / 2, dy = (float) -Math.cos(angle) * box.width() / 2;
   specular.setShader(new LinearGradient(box.centerX() - dx, box.centerY() - dy, box.centerX() + dx, box.centerY() + dy,
     SPECULAR, SPECULAR_STOPS, Shader.TileMode.CLAMP));
  }

  @Override public void draw(Canvas canvas) {
   if (box.isEmpty()) return;
   canvas.drawRoundRect(box, radius, radius, metal);
   canvas.drawRoundRect(box, radius, radius, grain);
   canvas.drawRoundRect(box, radius, radius, specular);
   float half = rim.getStrokeWidth() / 2;
   rim.setColor(TOP_RIM);
   canvas.drawLine(box.left + radius, box.top + half, box.right - radius, box.top + half, rim);
   rim.setColor(LOWER_RIM);
   canvas.drawLine(box.left + radius, box.bottom - half, box.right - radius, box.bottom - half, rim);
  }

  @Override public void getOutline(Outline outline) { outline.setRoundRect(getBounds(), radius); }
  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

 /**
  * #157: a clear oval glass lens set in the metal, as in the selected image. The glass is a light blue-grey tint over
  * the metal (the image's well interior averages #abb3bd against #c3c5ca metal), lit from the upper left, with a
  * soft sheen across its top, a thin bright rim and a soft shadow just outside its lower edge. Pressed drops the
  * sheen and shades the glass from the top. A well view is its glass plus POCKET_DP on every side for that shadow,
  * so nothing is drawn outside the view.
  */
 static final class Well extends Drawable {
  static final int TINT = 0x2e2a3c5a, LIGHT = 0x59ffffff, RIM = 0xb3ffffff, RIM_SHADOW = 0x4d28374f,
    LOWER_SHADE = 0x38465c7d, SHEEN = 0x4dffffff, PRESSED_SHADE = 0x4d3c506e, OUTER_SHADOW = 0x4028374f;

  final float density, pocket;
  final Paint tint = new Paint(Paint.ANTI_ALIAS_FLAG), light = new Paint(Paint.ANTI_ALIAS_FLAG),
    shade = new Paint(Paint.ANTI_ALIAS_FLAG), sheen = new Paint(Paint.ANTI_ALIAS_FLAG),
    pressedShade = new Paint(Paint.ANTI_ALIAS_FLAG), line = new Paint(Paint.ANTI_ALIAS_FLAG), fill = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF lens = new RectF(), shine = new RectF(), ring = new RectF();
  final android.graphics.Path lensPath = new android.graphics.Path();
  boolean pressed;

  Well(float density) {
   this.density = density;
   pocket = POCKET_DP * density;
   line.setStyle(Paint.Style.STROKE);
   tint.setColor(TINT);
  }

  @Override protected void onBoundsChange(Rect bounds) {
   lens.set(bounds);
   lens.inset(pocket, pocket);
   float w = lens.width(), h = lens.height();
   lensPath.reset();
   lensPath.addOval(lens, android.graphics.Path.Direction.CW);
   // Light from the upper left: white fading out across the lens.
   RadialGradient glow = new RadialGradient(0, 0, 1, new int[]{LIGHT, 0x00ffffff}, null, Shader.TileMode.CLAMP);
   Matrix ellipse = new Matrix();
   ellipse.setScale(.8f * w, .8f * h);
   ellipse.postTranslate(lens.left + .32f * w, lens.top + .22f * h);
   glow.setLocalMatrix(ellipse);
   light.setShader(glow);
   shade.setShader(new LinearGradient(0, lens.bottom - .3f * h, 0, lens.bottom, 0x00465c7d, LOWER_SHADE, Shader.TileMode.CLAMP));
   shine.set(lens.left + .22f * w, lens.top + .07f * h, lens.right - .22f * w, lens.top + .36f * h);
   sheen.setShader(new LinearGradient(0, shine.top, 0, shine.bottom, SHEEN, 0x00ffffff, Shader.TileMode.CLAMP));
   pressedShade.setShader(new LinearGradient(0, lens.top, 0, lens.top + .3f * h, PRESSED_SHADE, 0x003c506e, Shader.TileMode.CLAMP));
  }

  @Override public void draw(Canvas canvas) {
   if (lens.isEmpty()) return;
   float one = Math.max(1f, density);
   // The soft shadow just outside the lower edge, painted only outside the lens so the clear glass shows the metal.
   int save = canvas.save();
   canvas.clipOutPath(lensPath);
   fill.setColor(OUTER_SHADOW);
   ring.set(lens.left - one, lens.top, lens.right + one, lens.bottom + 2 * one);
   canvas.drawOval(ring, fill);
   canvas.restoreToCount(save);
   // The glass.
   canvas.drawOval(lens, tint);
   canvas.drawOval(lens, light);
   canvas.drawOval(lens, shade);
   if (pressed) canvas.drawOval(lens, pressedShade);
   else canvas.drawOval(shine, sheen);
   // The thin rim: a dark line just inside a bright one, so the edge reads as raised glass.
   line.setStrokeWidth(one);
   line.setColor(RIM_SHADOW);
   ring.set(lens);
   ring.inset(1.5f * one, 1.5f * one);
   canvas.drawOval(ring, line);
   line.setStrokeWidth(one);
   line.setColor(RIM);
   ring.set(lens);
   ring.inset(one / 2, one / 2);
   canvas.drawOval(ring, line);
  }

  @Override public boolean isStateful() { return true; }

  @Override protected boolean onStateChange(int[] state) {
   boolean now = false;
   for (int s : state) if (s == android.R.attr.state_pressed) now = true;
   if (now == pressed) return false;
   pressed = now;
   invalidateSelf();
   return true;
  }

  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }
 /**
  * The overview mark: three 11dp bars, 4dp apart, 5dp radius, centred in the well. #157: in the selected image the
  * outer bars are about 70% of the middle one's height (24dp beside 34dp); ce126f56 had 30 beside 34. White to #e2e8f0 bars ringed rgba(120,140,170,.55) with an inset lower shade
  * rgba(80,100,130,.25); the last bar #6aa2ff to #2f6be6, ringed rgba(30,80,200,.6), with an inset top highlight
  * rgba(255,255,255,.45). Gradients are built per size, so drawing allocates nothing.
  */
 static final class Overview extends Drawable {
  static final int[] BAR = {0xffffffff, 0xffe2e8f0}, ACCENT = {0xff6aa2ff, 0xff2f6be6};
  static final int BAR_RING = 0x8c788caa, ACCENT_RING = 0x991e50c8, BAR_SHADE = 0x40506482, ACCENT_LIGHT = 0x73ffffff;

  final float unit;
  final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), edge = new Paint(Paint.ANTI_ALIAS_FLAG),
    inset = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF[] bars = {new RectF(), new RectF(), new RectF()};
  final Shader[] fills = new Shader[3], insets = new Shader[3];

  Overview(float density) {
   unit = density;
   edge.setStyle(Paint.Style.STROKE);
   edge.setStrokeWidth(Math.max(1f, density));
  }

  @Override protected void onBoundsChange(Rect bounds) {
   float width = 11 * unit, gap = 4 * unit, total = 3 * width + 2 * gap;
   float left = bounds.left + (bounds.width() - total) / 2, middle = bounds.exactCenterY();
   for (int i = 0; i < 3; i++) {
    float half = (i == 1 ? 17 : 12) * unit, x = left + i * (width + gap);
    RectF bar = bars[i];
    bar.set(x, middle - half, x + width, middle + half);
    boolean accent = i == 2;
    fills[i] = new LinearGradient(0, bar.top, 0, bar.bottom, accent ? ACCENT : BAR, null, Shader.TileMode.CLAMP);
    insets[i] = accent
      ? new LinearGradient(0, bar.top, 0, bar.top + 3 * unit, ACCENT_LIGHT, 0x00ffffff, Shader.TileMode.CLAMP)
      : new LinearGradient(0, bar.bottom - 4 * unit, 0, bar.bottom, 0x00506482, BAR_SHADE, Shader.TileMode.CLAMP);
   }
  }

  @Override public void draw(Canvas canvas) {
   float radius = 5 * unit;
   for (int i = 0; i < 3; i++) {
    if (fills[i] == null) return;
    fill.setShader(fills[i]);
    canvas.drawRoundRect(bars[i], radius, radius, fill);
    inset.setShader(insets[i]);
    canvas.drawRoundRect(bars[i], radius, radius, inset);
    edge.setColor(i == 2 ? ACCENT_RING : BAR_RING);
    canvas.drawRoundRect(bars[i], radius, radius, edge);
   }
  }

  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }
}
