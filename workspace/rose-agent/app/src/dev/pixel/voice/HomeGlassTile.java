package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * The app tile of the selected HiFi (prototypes/rose-hifi at ce126f56, style.css .tile and .tile::after): a 64dp
 * rounded square of frosted glass over the wallpaper. A 160-degree white fill (62% to 18% at 55% to a cool 32%),
 * a fine cool outer ring and a white inner ring, a bright top edge, a soft lower inner shade and a sheen over the
 * top half. The drop shadow is the view's elevation, cast from this outline. The web tile also blurs the wallpaper
 * behind it (backdrop-filter); this drawable does not, so it reads slightly clearer than the reference.
 * Decorative only: the tile view keeps the app's name and its click.
 */
final class HomeGlassTile extends Drawable {
 static final int RADIUS_DP = 18;
 static final int[] FILL = {0x9effffff, 0x2effffff, 0x52e1ecf8};
 static final float[] FILL_STOPS = {0f, .55f, 1f};
 static final int OUTER_RING = 0x527896be, INNER_RING = 0x80ffffff, TOP_EDGE = 0xf2ffffff, LOWER_SHADE = 0x2e3c5a82,
   SHEEN = 0x8cffffff;

 final float density, radius;
 final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), line = new Paint(Paint.ANTI_ALIAS_FLAG),
   sheen = new Paint(Paint.ANTI_ALIAS_FLAG), shade = new Paint(Paint.ANTI_ALIAS_FLAG);
 final RectF box = new RectF(), inner = new RectF(), top = new RectF();

 HomeGlassTile(float density) {
  this.density = density;
  radius = RADIUS_DP * density;
  line.setStyle(Paint.Style.STROKE);
  line.setStrokeWidth(Math.max(1f, density));
 }

 @Override protected void onBoundsChange(Rect bounds) {
  box.set(bounds);
  float w = box.width(), h = box.height();
  // CSS 160deg runs from the top towards the bottom, leaning a little left: start top-left of centre, end below.
  double angle = Math.toRadians(160);
  float dx = (float) Math.sin(angle) * w / 2, dy = (float) -Math.cos(angle) * h / 2;
  fill.setShader(new LinearGradient(box.centerX() - dx, box.centerY() - dy, box.centerX() + dx, box.centerY() + dy,
    FILL, FILL_STOPS, Shader.TileMode.CLAMP));
  shade.setShader(new LinearGradient(0, box.bottom - 8 * density, 0, box.bottom, 0x003c5a82, LOWER_SHADE, Shader.TileMode.CLAMP));
  inner.set(box);
  inner.inset(2 * density, 2 * density);
  top.set(inner.left, inner.top, inner.right, box.centerY());
  sheen.setShader(new LinearGradient(0, top.top, 0, top.bottom, SHEEN, 0x00ffffff, Shader.TileMode.CLAMP));
 }

 @Override public void draw(Canvas canvas) {
  if (box.isEmpty()) return;
  canvas.drawRoundRect(box, radius, radius, fill);
  canvas.drawRoundRect(box, radius, radius, shade);
  canvas.drawRoundRect(top, radius - 2 * density, radius - 2 * density, sheen);
  // Outer ring on the edge, inner ring one stroke inside it (box-shadow 0 0 0 1px and inset 0 0 0 1px).
  float stroke = line.getStrokeWidth(), half = stroke / 2, in = stroke * 1.5f;
  line.setColor(INNER_RING);
  canvas.drawRoundRect(box.left + in, box.top + in, box.right - in, box.bottom - in, radius - in, radius - in, line);
  line.setColor(OUTER_RING);
  canvas.drawRoundRect(box.left + half, box.top + half, box.right - half, box.bottom - half, radius - half, radius - half, line);
  // The bright top edge: the upper rim only, inside the outer ring.
  line.setColor(TOP_EDGE);
  canvas.drawLine(box.left + radius, box.top + stroke, box.right - radius, box.top + stroke, line);
 }

 @Override public void getOutline(Outline outline) {
  outline.setRoundRect(getBounds(), radius);
  outline.setAlpha(.22f);
 }

 @Override public void setAlpha(int alpha) {}
 @Override public void setColorFilter(ColorFilter filter) {}
 @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
