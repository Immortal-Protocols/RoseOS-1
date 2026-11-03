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
 * with a 1px inner ring at .07 and a brighter 1px inner top edge at .10. No drop shadow.
 * Shared by Home, the app pages and the System folder. Decorative only: the cell view keeps the app's name and click.
 *
 * Backdrop blur (#410 gap closure; oxblood.css:38,215 backdrop-filter blur(6-10px)): when Home's field is the backdrop
 * (Backdrop.set by MainActivity), the tile first draws the field's blurred copy (OxbloodField.blurred, sigma 6dp) for
 * exactly the part of the screen it covers, found from the tile's and the pager's positions in the window at draw time,
 * so it stays aligned while pages swipe. In any other window (the folder's dialog), or with no blurred copy, it draws as
 * before, the faint glass over whatever is behind: the readable fallback.
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

 /** Home's field and the view it is drawn behind (the pager), held weakly; cleared when Home is rebuilt. */
 static final class Backdrop {
  private Backdrop() {}
  static java.lang.ref.WeakReference<OxbloodField> field = new java.lang.ref.WeakReference<>(null);
  static java.lang.ref.WeakReference<android.view.View> host = new java.lang.ref.WeakReference<>(null);
  static void set(OxbloodField f, android.view.View h) { field = new java.lang.ref.WeakReference<>(f); host = new java.lang.ref.WeakReference<>(h); }
 }
 static final float BLUR_DP = 6;
 final Paint image = new Paint(Paint.FILTER_BITMAP_FLAG | Paint.ANTI_ALIAS_FLAG);
 final int[] at = new int[2], hostAt = new int[2];
 final Rect src = new Rect();
 final android.graphics.Path round = new android.graphics.Path();

 /** Draws the blurred field behind this tile when it can; false to fall back. */
 boolean drawBackdrop(Canvas canvas) {
  OxbloodField f = Backdrop.field.get();
  android.view.View h = Backdrop.host.get();
  if (f == null || h == null || !(getCallback() instanceof android.view.View)) return false;
  android.view.View self = (android.view.View) getCallback();
  if (!self.isAttachedToWindow() || !h.isAttachedToWindow() || self.getRootView() != h.getRootView()) return false;
  android.graphics.Bitmap blur = f.blurred(BLUR_DP * density);
  if (blur == null) return false;
  self.getLocationInWindow(at); h.getLocationInWindow(hostAt);
  float scale = blur.getWidth() / (float) Math.max(1, f.getBounds().width());
  float x = at[0] - hostAt[0] + box.left, y = at[1] - hostAt[1] + box.top;
  src.set(Math.round(x * scale), Math.round(y * scale), Math.round((x + box.width()) * scale), Math.round((y + box.height()) * scale));
  int save = canvas.save();
  round.reset(); round.addRoundRect(box, radius, radius, android.graphics.Path.Direction.CW);
  canvas.clipPath(round);
  canvas.drawBitmap(blur, src, box, image);
  canvas.restoreToCount(save);
  return true;
 }

 @Override public void draw(Canvas canvas) {
  if (box.isEmpty()) return;
  drawBackdrop(canvas);
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
