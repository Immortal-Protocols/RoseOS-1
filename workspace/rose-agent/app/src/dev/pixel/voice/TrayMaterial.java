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
 * The one tray material of the selected HiFi (prototypes/rose-hifi at ce126f56, effective style.css .tray and .well
 * rules, review overrides included), shared by the Home tray and the conversation tray so both read as one system.
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
  static final int[] METAL = {0xfff6f7f9, 0xffe3e7eb, 0xffcfd5db, 0xffe8ebee};
  static final float[] METAL_STOPS = {0f, .38f, .62f, 1f};
  static final int[] SPECULAR = {0x00ffffff, 0x8cffffff, 0x00ffffff};
  static final float[] SPECULAR_STOPS = {.28f, .44f, .60f};
  static final int TOP_RIM = 0xffffffff, LOWER_RIM = 0x473c4b5f, RADIUS_DP = 22;

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
    line.setColor(shade >= 0 ? (shade * 3) << 24 | 0xffffff : (-shade * 3) << 24 | 0x3c4b5f);
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
  * .well: a glass pill lens recessed in the metal. Inside: radial-gradient(110% 80% at 32% 18%) from white .98
  * through white .55 (34%) and rgba(214,226,240,.35) (68%) to rgba(190,206,224,.5); a white .65 inner edge, a
  * lower inner shade rgba(70,92,125,.22) and the ::after sheen (16% in from each side, 5dp down, 34% tall, white
  * .85 fading out). Around it, the pocket: a 1px rgba(95,115,142,.42) edge, a 4px rgba(198,206,215,.9) collar, a
  * rgba(120,135,155,.35) outer line and a white .75 lower lip. Pressed (:active) drops the sheen and shades the
  * glass from the top (inset 0 3px 8px rgba(60,80,110,.3)), as the reference does.
  */
 static final class Well extends Drawable {
  static final int[] GLASS = {0xfaffffff, 0x8cffffff, 0x59d6e2f0, 0x80becee0};
  static final float[] GLASS_STOPS = {0f, .34f, .68f, 1f};
  static final int INNER_EDGE = 0xa6ffffff, LOWER_SHADE = 0x38465c7d, SHEEN = 0xd9ffffff, PRESSED_SHADE = 0x4d3c506e,
    POCKET_EDGE = 0x6b5f738e, COLLAR = 0xe6c6ced7, COLLAR_LINE = 0x5978879b, LIP = 0xbfffffff;

  final float density, pocket;
  final Paint glass = new Paint(Paint.ANTI_ALIAS_FLAG), shade = new Paint(Paint.ANTI_ALIAS_FLAG),
    sheen = new Paint(Paint.ANTI_ALIAS_FLAG), pressedShade = new Paint(Paint.ANTI_ALIAS_FLAG),
    line = new Paint(Paint.ANTI_ALIAS_FLAG), fill = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF lens = new RectF(), shine = new RectF(), ring = new RectF();
  boolean pressed;

  Well(float density) {
   this.density = density;
   pocket = POCKET_DP * density;
   line.setStyle(Paint.Style.STROKE);
  }

  @Override protected void onBoundsChange(Rect bounds) {
   lens.set(bounds);
   lens.inset(pocket, pocket);
   float w = lens.width(), h = lens.height();
   RadialGradient light = new RadialGradient(0, 0, 1, GLASS, GLASS_STOPS, Shader.TileMode.CLAMP);
   Matrix ellipse = new Matrix();
   ellipse.setScale(1.1f * w, .8f * h);
   ellipse.postTranslate(lens.left + .32f * w, lens.top + .18f * h);
   light.setLocalMatrix(ellipse);
   glass.setShader(light);
   shade.setShader(new LinearGradient(0, lens.bottom - 8 * density, 0, lens.bottom, 0x00465c7d, LOWER_SHADE, Shader.TileMode.CLAMP));
   shine.set(lens.left + .16f * w, lens.top + 5 * density, lens.right - .16f * w, lens.top + 5 * density + .34f * h);
   sheen.setShader(new LinearGradient(0, shine.top, 0, shine.bottom, SHEEN, 0x00ffffff, Shader.TileMode.CLAMP));
   pressedShade.setShader(new LinearGradient(0, lens.top, 0, lens.top + 8 * density, PRESSED_SHADE, 0x003c506e, Shader.TileMode.CLAMP));
  }

  @Override public void draw(Canvas canvas) {
   if (lens.isEmpty()) return;
   float r = lens.height() / 2, one = Math.max(1f, density);
   // The pocket, outermost first: the lower lip, the outer line, the collar, then the lens edge.
   fill.setColor(LIP);
   ring.set(lens.left - pocket, lens.top - pocket + one, lens.right + pocket, lens.bottom + pocket);
   canvas.drawRoundRect(ring, r + pocket, r + pocket, fill);
   fill.setColor(COLLAR_LINE);
   ring.set(lens.left - pocket, lens.top - pocket, lens.right + pocket, lens.bottom + pocket - one);
   canvas.drawRoundRect(ring, r + pocket, r + pocket, fill);
   fill.setColor(COLLAR);
   ring.set(lens);
   ring.inset(-4 * density, -4 * density);
   canvas.drawRoundRect(ring, r + 4 * density, r + 4 * density, fill);
   fill.setColor(POCKET_EDGE);
   ring.set(lens);
   ring.inset(-one, -one);
   canvas.drawRoundRect(ring, r + one, r + one, fill);
   // The glass.
   canvas.drawRoundRect(lens, r, r, glass);
   canvas.drawRoundRect(lens, r, r, shade);
   if (pressed) canvas.drawRoundRect(lens, r, r, pressedShade);
   else canvas.drawRoundRect(shine, shine.height() / 2, shine.height() / 2, sheen);
   line.setStrokeWidth(one);
   line.setColor(INNER_EDGE);
   ring.set(lens);
   ring.inset(one / 2, one / 2);
   canvas.drawRoundRect(ring, r - one / 2, r - one / 2, line);
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
}
