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
 * The subdued app tile of the Sunlit Instruments study's Oxblood Home (#403; Rose-07-Home-No-Status, Rose-06), which
 * replaces the HiFi white frosted tile for the Home app icons: a rounded square of smoked oxblood glass a little
 * lighter at the top than the bottom (sampled from the study, where the tile reads about #5A1822 over a #35070E field),
 * a faint warm rim, a brighter top edge and a soft sheen over the top half. It is nearly opaque so the cream glyph
 * (HomeGlyph) keeps the study's contrast over the current light wallpaper, which this change leaves as it is. The
 * drop shadow is the view's elevation, cast from this outline. Decorative only: the tile view keeps the app's name and
 * its click.
 */
final class HomeGlassTile extends Drawable {
 static final int RADIUS_DP = 16;
 static final int[] FILL = {0xf05e1b26, 0xf0461019, 0xf233090f};
 static final float[] FILL_STOPS = {0f, .55f, 1f};
 static final int OUTER_RING = 0x4d1a0306, INNER_RING = 0x29ffd9cc, TOP_EDGE = 0x2effe6da, LOWER_SHADE = 0x33140205,
   SHEEN = 0x17ffffff;

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
  // Light from above, leaning a little left (a 160-degree gradient): start top-left of centre, end below.
  double angle = Math.toRadians(160);
  float dx = (float) Math.sin(angle) * w / 2, dy = (float) -Math.cos(angle) * h / 2;
  fill.setShader(new LinearGradient(box.centerX() - dx, box.centerY() - dy, box.centerX() + dx, box.centerY() + dy,
    FILL, FILL_STOPS, Shader.TileMode.CLAMP));
  shade.setShader(new LinearGradient(0, box.bottom - 8 * density, 0, box.bottom, 0x00140205, LOWER_SHADE, Shader.TileMode.CLAMP));
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
  // A dark outer ring on the edge, a faint warm inner ring one stroke inside it.
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
