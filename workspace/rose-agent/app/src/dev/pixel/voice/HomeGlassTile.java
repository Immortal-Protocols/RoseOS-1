package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/**
 * The Oxblood app tile of the committed Rose HiFi prototype (#410; main f6503a0, oxblood.css:38,215,354-362 over
 * style.css .tile): a 59dp rounded square (radius 14) of faint warm glass on the Oxblood field, rgba(255,232,222,.055),
 * with a 1px inner ring at .07 and a brighter 1px inner top edge at .10. No drop shadow. The reference also blurs the
 * field behind it (backdrop-filter 6-10px); this drawable does not, which over the smooth field is barely visible.
 * Shared by Home, the app pages and the System folder. Decorative only: the cell view keeps the app's name and click.
 */
final class HomeGlassTile extends Drawable {
 static final int RADIUS_DP = 14;
 static final int FILL = 0x0effe8de, RING = 0x12ffe8de, TOP_EDGE = 0x1affe8de;

 final float density, radius;
 final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), line = new Paint(Paint.ANTI_ALIAS_FLAG);
 final RectF box = new RectF();

 HomeGlassTile(float density) {
  this.density = density;
  radius = RADIUS_DP * density;
  fill.setColor(FILL);
  line.setStyle(Paint.Style.STROKE);
  line.setStrokeWidth(Math.max(1f, density));
 }

 @Override protected void onBoundsChange(Rect bounds) { box.set(bounds); }

 @Override public void draw(Canvas canvas) {
  if (box.isEmpty()) return;
  canvas.drawRoundRect(box, radius, radius, fill);
  // box-shadow inset 0 0 0 1px: a ring just inside the edge.
  float w = line.getStrokeWidth(), half = w / 2;
  line.setColor(RING);
  canvas.drawRoundRect(box.left + half, box.top + half, box.right - half, box.bottom - half, radius - half, radius - half, line);
  // box-shadow inset 0 1px 0: a brighter line along the inside of the top edge, between the corners.
  line.setColor(TOP_EDGE);
  canvas.drawLine(box.left + radius, box.top + w + half, box.right - radius, box.top + w + half, line);
 }

 @Override public void getOutline(Outline outline) {
  outline.setRoundRect(getBounds(), radius);
  outline.setAlpha(0f);
 }

 @Override public void setAlpha(int alpha) {}
 @Override public void setColorFilter(ColorFilter filter) {}
 @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
