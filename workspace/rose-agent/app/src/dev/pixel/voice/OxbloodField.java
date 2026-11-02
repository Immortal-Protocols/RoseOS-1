package dev.pixel.voice;

import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * The Oxblood fields of the committed Rose HiFi prototype (#410; main f6503a0, prototypes/rose-hifi/src/assets
 * oxblood-home.svg and oxblood-rose.svg), drawn natively from the same geometry instead of a raster: polygons, linear
 * and radial gradients in each shape's own bounding box (SVG objectBoundingBox units), hairlines, and the two Gaussian
 * blurs (the beam's soft glow and the join's bloom). The 390x844 artwork is placed as the reference CSS places it
 * (background-size cover, background-position center top): scaled to cover, centred across and top-aligned, never stretched. HOME is Home and the app pages; ROSE is the
 * conversation and Background Work. Decorative: no text, no touch, no state.
 */
final class OxbloodField extends Drawable {
 static final int HOME = 0, ROSE = 1;
 static final float VIEW_W = 390f, VIEW_H = 844f;

 final int kind;
 final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
 float scale = 1, dx, dy;

 OxbloodField(int kind) { this.kind = kind; }

 @Override protected void onBoundsChange(Rect b) {
  scale = Math.max(b.width() / VIEW_W, b.height() / VIEW_H);
  dx = b.left + (b.width() - VIEW_W * scale) / 2f;
  dy = b.top; // background-position: center top (oxblood.css:20-22)
 }

 @Override public void draw(Canvas canvas) {
  if (getBounds().isEmpty()) return;
  int save = canvas.save();
  canvas.clipRect(getBounds());
  canvas.translate(dx, dy);
  canvas.scale(scale, scale);
  if (kind == HOME) home(canvas); else rose(canvas);
  canvas.restoreToCount(save);
 }

 // ---- oxblood-home.svg ----
 private void home(Canvas c) {
  fill(c, rect(0, 0, VIEW_W, VIEW_H), 0xff170104);
  Path pane = poly(0, 0, 290, 0, 208, 226, 0, 297);
  linear(c, pane, 0, 0, .55f, 1, new int[]{0xff5a514f, 0xff2a2221, 0xff171110}, new float[]{0, .5f, 1});
  fill(c, poly(0, 297, 208, 226, 127, 391, 63, 414, 0, 610), 0xff0f0606);
  Path right = poly(290, 0, 390, 0, 390, 300, 63, 414, 127, 391);
  linear(c, right, 1, 0, 0, .8f, new int[]{0xff2a0309, 0xff46040f, 0xff5e0a1a}, new float[]{0, .55f, 1});
  Path fan = poly(127, 391, 390, 190, 390, 300, 63, 414);
  linear(c, fan, 0, 1, 1, 0, new int[]{argb(.9f, 0x8a1426), argb(.3f, 0x5e0a1a), argb(0, 0x2a0309)}, new float[]{0, .6f, 1});
  Path lower = poly(63, 414, 390, 300, 390, 844, 0, 844, 0, 610);
  linear(c, lower, 0, 0, 0, 1, new int[]{0xff3b020c, 0xff250207, 0xff1b0104, 0xff170104}, new float[]{0, .45f, .8f, 1});
  Path beam = poly(284, 0, 312, 0, 136, 392, 120, 392);
  linear(c, beam, 0, 0, 0, 1, new int[]{argb(.26f, 0xf4dccf), argb(.12f, 0xf4dccf), argb(.3f, 0xffd2c8)}, new float[]{0, .7f, 1});
  line(c, 286, 0, 121, 392, 0xfff1e6, .45f, 3.5f, 2.5f);
  line(c, 286, 0, 121, 392, 0xfff1e6, 1, 1.3f, 0);
  line(c, 312, 0, 136, 392, 0xf0cfc2, .45f, .8f, 0);
  line(c, 63, 414, 390, 300, 0xd65a69, .55f, 1, 0);
  line(c, 63, 414, 0, 610, 0xa0283c, .7f, 1, 0);
  line(c, 208, 226, 0, 297, 0xc9b6ad, .22f, .8f, 0);
  // The hot join: a broad bloom and a small bright core, both tilted -20 degrees.
  int save = c.save();
  c.rotate(-20, 128, 388);
  RectF bloom = new RectF(128 - 70, 388 - 40, 128 + 70, 388 + 40);
  paint.reset(); paint.setAntiAlias(true); paint.setDither(true);
  // objectBoundingBox radial (r=.5 of a 140x80 box) is an ellipse: the gradient is squashed by its own local
  // matrix, so the blur stays round as in SVG, where the filter blurs the drawn ellipse in user space.
  RadialGradient join = new RadialGradient(128, 388, 70,
    new int[]{0xfffff3ea, argb(.85f, 0xff6a78), argb(.45f, 0xc0283f), argb(0, 0xc0283f)}, new float[]{0, .25f, .55f, 1}, Shader.TileMode.CLAMP);
  android.graphics.Matrix squash = new android.graphics.Matrix(); squash.setScale(1, 40f / 70f, 128, 388); join.setLocalMatrix(squash);
  paint.setShader(join);
  paint.setAlpha(Math.round(.9f * 255));
  paint.setMaskFilter(blur(9));
  c.drawOval(bloom, paint);
  c.restoreToCount(save);
  save = c.save();
  c.rotate(-20, 127, 390);
  paint.reset(); paint.setAntiAlias(true);
  paint.setColor(0xfffff3ea); paint.setAlpha(Math.round(.85f * 255)); paint.setMaskFilter(blur(2.5f));
  c.drawOval(new RectF(127 - 16, 390 - 7, 127 + 16, 390 + 7), paint);
  c.restoreToCount(save);
 }

 // ---- oxblood-rose.svg ----
 private void rose(Canvas c) {
  linear(c, rect(0, 0, VIEW_W, VIEW_H), 0, 0, 0, 1, new int[]{0xff120a0d, 0xff0e080b, 0xff12060a}, new float[]{0, .6f, 1});
  Path sheet = poly(390, 118, 390, 292, 295, 580);
  linear(c, sheet, 1, 0, 0, 1, new int[]{argb(.35f, 0x8a3a4c), argb(0, 0x8a3a4c)}, new float[]{0, 1});
  line(c, 390, 292, 295, 580, 0xffb3bd, .7f, 1, 0);
  Path plane = poly(295, 580, 390, 515, 390, 844, 123, 844, 206, 642);
  linear(c, plane, 0, 0, 1, 1, new int[]{argb(.9f, 0x7a1428), 0xff36020d, 0xff1a0207}, new float[]{0, .45f, 1});
  Path edge = new Path(); edge.moveTo(206, 642); edge.lineTo(295, 580); edge.lineTo(390, 515);
  stroke(c, edge, 0xffc4c8, .5f, 4, 3);
  stroke(c, edge, 0xffc4c8, 1, 1.2f, 0);
  line(c, 206, 642, 123, 844, 0xc2384c, .55f, 1, 0);
  paint.reset(); paint.setAntiAlias(true); paint.setDither(true);
  paint.setShader(new RadialGradient(295, 580, 26, new int[]{argb(.9f, 0xfff0f0), argb(0, 0xff8a9a)}, null, Shader.TileMode.CLAMP));
  c.drawCircle(295, 580, 26, paint);
 }

 // ---- helpers, all in the 390x844 view box ----
 static int argb(float alpha, int rgb) { return (Math.round(alpha * 255) << 24) | (rgb & 0xffffff); }
 static Path rect(float l, float t, float r, float b) { Path p = new Path(); p.addRect(l, t, r, b, Path.Direction.CW); return p; }
 static Path poly(float... xy) {
  Path p = new Path();
  p.moveTo(xy[0], xy[1]);
  for (int i = 2; i < xy.length; i += 2) p.lineTo(xy[i], xy[i + 1]);
  p.close();
  return p;
 }
 /** feGaussianBlur stdDeviation (view units) as a BlurMaskFilter radius in the same units: Android's blur scales with
  * the canvas like SVG's user-space filter, and Skia takes sigma = 0.57735 * radius + 0.5. */
 static BlurMaskFilter blur(float stdDeviation) {
  return new BlurMaskFilter(Math.max(.1f, (stdDeviation - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL);
 }
 private void fill(Canvas c, Path p, int color) {
  paint.reset(); paint.setAntiAlias(true); paint.setColor(color); c.drawPath(p, paint);
 }
 /** A linearGradient in the shape's objectBoundingBox: (x1,y1)-(x2,y2) are fractions of its bounds. */
 private void linear(Canvas c, Path p, float x1, float y1, float x2, float y2, int[] colors, float[] stops) {
  RectF box = new RectF(); p.computeBounds(box, true);
  paint.reset(); paint.setAntiAlias(true); paint.setDither(true);
  paint.setShader(new LinearGradient(box.left + x1 * box.width(), box.top + y1 * box.height(),
    box.left + x2 * box.width(), box.top + y2 * box.height(), colors, stops, Shader.TileMode.CLAMP));
  c.drawPath(p, paint);
 }
 private void line(Canvas c, float x1, float y1, float x2, float y2, int rgb, float opacity, float width, float blurStd) {
  Path p = new Path(); p.moveTo(x1, y1); p.lineTo(x2, y2);
  stroke(c, p, rgb, opacity, width, blurStd);
 }
 private void stroke(Canvas c, Path p, int rgb, float opacity, float width, float blurStd) {
  paint.reset(); paint.setAntiAlias(true);
  paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(width); paint.setStrokeJoin(Paint.Join.MITER);
  paint.setColor(argb(opacity, rgb));
  if (blurStd > 0) paint.setMaskFilter(blur(blurStd));
  c.drawPath(p, paint);
 }

 @Override public void setAlpha(int alpha) {}
 @Override public void setColorFilter(ColorFilter filter) {}
 @Override public int getOpacity() { return PixelFormat.OPAQUE; }
}
