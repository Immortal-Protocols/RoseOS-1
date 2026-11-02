package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.drawable.AdaptiveIconDrawable;
import android.graphics.drawable.Drawable;
import android.os.Build;

/**
 * The simplified Home app glyphs (#403), after the Home rows of "Rose — Sunlit Instruments | Visual Study"
 * (Rose-07-Home-No-Status and Rose-06-Oxblood-Shared-Tasks, the subdued Oxblood Home): one warm cream monoline mark
 * per app, rounded caps and joins, no fill except where the reference fills (the direction arrow, small dots), on the
 * subdued tile (HomeGlassTile). Drawn on a 24-unit grid scaled to the bounds, so it needs no resources and stays crisp
 * at any density. The study's own eight marks (phone, speech bubble, arrow, camera, envelope, dotted calendar, lines,
 * and the circles) set the language; the other suite apps get marks in the same language. Decorative only: the tile
 * view keeps the app's name and its click.
 */
final class HomeGlyph extends Drawable {
 /** The reference's glyph colour, sampled from the study (about #F5E6D4 at the stroke centre). */
 static final int CREAM = 0xfff3e4d0;
 /** Stroke width in grid units: about 2.5dp at the 44dp icon size, as in the reference (about 4% of the tile). */
 static final float STROKE = 1.35f, GRID = 24f;
 static final String[] KEYS = {"phone", "messages", "mail", "calendar", "clock", "camera", "calculator", "notes",
   "reminders", "news", "books", "podcasts", "compass", "sky", "weather", "files", "gallery", "settings", "browser",
   "contacts", "memories", "more"};

 final String key;
 final Path stroke = new Path(), fill = new Path();
 final Paint line = new Paint(Paint.ANTI_ALIAS_FLAG), solid = new Paint(Paint.ANTI_ALIAS_FLAG);
 final Matrix matrix = new Matrix();
 final Path drawStroke = new Path(), drawFill = new Path();

 HomeGlyph(String key) {
  this.key = key;
  line.setStyle(Paint.Style.STROKE);
  line.setStrokeCap(Paint.Cap.ROUND);
  line.setStrokeJoin(Paint.Join.ROUND);
  line.setColor(CREAM);
  solid.setStyle(Paint.Style.FILL);
  solid.setColor(CREAM);
  build(key, stroke, fill);
 }

 static boolean known(String key) {
  for (String k : KEYS) if (k.equals(key)) return true;
  return false;
 }

 @Override protected void onBoundsChange(Rect bounds) {
  float side = Math.min(bounds.width(), bounds.height()), scale = side / GRID;
  matrix.setScale(scale, scale);
  matrix.postTranslate(bounds.centerX() - side / 2, bounds.centerY() - side / 2);
  stroke.transform(matrix, drawStroke);
  fill.transform(matrix, drawFill);
  line.setStrokeWidth(STROKE * scale);
 }

 @Override public void draw(Canvas canvas) {
  if (getBounds().isEmpty()) return;
  canvas.drawPath(drawFill, solid);
  canvas.drawPath(drawStroke, line);
 }

 @Override public int getIntrinsicWidth() { return -1; }
 @Override public int getIntrinsicHeight() { return -1; }
 @Override public void setAlpha(int alpha) { line.setAlpha(alpha); solid.setAlpha(alpha); invalidateSelf(); }
 @Override public void setColorFilter(ColorFilter filter) { line.setColorFilter(filter); solid.setColorFilter(filter); invalidateSelf(); }
 @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }

 private static void circle(Path p, float x, float y, float r) { p.addCircle(x, y, r, Path.Direction.CW); }
 private static void segment(Path p, float x1, float y1, float x2, float y2) { p.moveTo(x1, y1); p.lineTo(x2, y2); }
 private static void round(Path p, float l, float t, float r, float b, float radius) {
  p.addRoundRect(new RectF(l, t, r, b), radius, radius, Path.Direction.CW);
 }

 /** The marks, on a 24-unit grid with content between about 3 and 21. */
 static void build(String key, Path s, Path f) {
  switch (key) {
   case "phone": // a handset, as the study's first mark
    s.moveTo(7f, 3.6f); s.lineTo(9.4f, 3.6f); s.lineTo(10.7f, 7.3f); s.lineTo(8.9f, 8.9f);
    s.cubicTo(9.9f, 11.5f, 12.5f, 14.1f, 15.1f, 15.1f); s.lineTo(16.7f, 13.3f); s.lineTo(20.4f, 14.6f); s.lineTo(20.4f, 17f);
    s.cubicTo(20.4f, 18.9f, 18.9f, 20.5f, 17f, 20.4f); s.cubicTo(10f, 19.9f, 4.1f, 14f, 3.6f, 7f);
    s.cubicTo(3.5f, 5.1f, 5.1f, 3.6f, 7f, 3.6f); s.close();
    break;
   case "messages": // an oval speech bubble with a short tail, lower left
    s.addOval(new RectF(3.5f, 4.5f, 20.5f, 17.5f), Path.Direction.CW);
    s.moveTo(7.6f, 16.3f); s.lineTo(6.3f, 20.2f); s.lineTo(10.9f, 17.4f);
    break;
   case "mail": // an envelope
    round(s, 3.2f, 5.5f, 20.8f, 18.5f, 1.8f);
    s.moveTo(3.8f, 6.6f); s.lineTo(12f, 12.9f); s.lineTo(20.2f, 6.6f);
    break;
   case "calendar": // a page with two rings, a header line and a grid of dots
    round(s, 3.8f, 5.6f, 20.2f, 20.4f, 2f);
    segment(s, 8f, 3.4f, 8f, 7.2f); segment(s, 16f, 3.4f, 16f, 7.2f); segment(s, 3.8f, 9.6f, 20.2f, 9.6f);
    for (int row = 0; row < 3; row++) for (int col = 0; col < 4; col++) circle(f, 7.9f + col * 2.75f, 12.7f + row * 2.6f, .55f);
    break;
   case "clock":
    circle(s, 12f, 12f, 8.5f);
    s.moveTo(12f, 7.4f); s.lineTo(12f, 12f); s.lineTo(15.4f, 14f);
    break;
   case "camera": // a body, the top housing, a lens and a small light
    s.moveTo(5.5f, 7.2f); s.lineTo(8.3f, 7.2f); s.lineTo(9.6f, 5f); s.lineTo(14.4f, 5f); s.lineTo(15.7f, 7.2f); s.lineTo(18.5f, 7.2f);
    s.cubicTo(19.9f, 7.2f, 21f, 8.3f, 21f, 9.7f); s.lineTo(21f, 17f); s.cubicTo(21f, 18.4f, 19.9f, 19.5f, 18.5f, 19.5f);
    s.lineTo(5.5f, 19.5f); s.cubicTo(4.1f, 19.5f, 3f, 18.4f, 3f, 17f); s.lineTo(3f, 9.7f); s.cubicTo(3f, 8.3f, 4.1f, 7.2f, 5.5f, 7.2f); s.close();
    circle(s, 12f, 13.2f, 3.4f);
    circle(f, 17.7f, 9.9f, .7f);
    break;
   case "calculator":
    round(s, 5f, 3f, 19f, 21f, 2.6f);
    round(s, 8f, 6f, 16f, 9.4f, 1f);
    for (int row = 0; row < 2; row++) for (int col = 0; col < 3; col++) circle(f, 8.6f + col * 3.4f, 13.3f + row * 3.9f, .85f);
    break;
   case "notes": // the study's lines mark: a short lead dash, then three lines
    segment(s, 4.6f, 7f, 5.8f, 7f); segment(s, 8.6f, 7f, 19.4f, 7f);
    segment(s, 4.6f, 12f, 19.4f, 12f); segment(s, 4.6f, 17f, 14.6f, 17f);
    break;
   case "reminders": // two checked rows and an open one
    s.moveTo(4f, 7f); s.lineTo(5.6f, 8.6f); s.lineTo(8.4f, 5.6f); segment(s, 11.5f, 7f, 20f, 7f);
    s.moveTo(4f, 12.6f); s.lineTo(5.6f, 14.2f); s.lineTo(8.4f, 11.2f); segment(s, 11.5f, 12.6f, 20f, 12.6f);
    circle(s, 6.2f, 18.4f, 1.6f); segment(s, 11.5f, 18.4f, 20f, 18.4f);
    break;
   case "news": // a folded paper with three lines
    s.moveTo(17f, 8.2f); s.lineTo(20.5f, 8.2f); s.lineTo(20.5f, 17.6f); s.cubicTo(20.5f, 18.7f, 19.6f, 19.6f, 18.5f, 19.6f);
    s.lineTo(5.5f, 19.6f); s.cubicTo(4.4f, 19.6f, 3.5f, 18.7f, 3.5f, 17.6f); s.lineTo(3.5f, 4.4f); s.lineTo(17f, 4.4f); s.lineTo(17f, 17.6f);
    s.cubicTo(17f, 18.7f, 17.9f, 19.6f, 18.5f, 19.6f);
    segment(s, 6.6f, 8.2f, 13.9f, 8.2f); segment(s, 6.6f, 11.8f, 13.9f, 11.8f); segment(s, 6.6f, 15.4f, 11.4f, 15.4f);
    break;
   case "books": // an open book
    s.moveTo(12f, 6.6f); s.cubicTo(10f, 5f, 7f, 4.5f, 3.5f, 5f); s.lineTo(3.5f, 18.6f); s.cubicTo(7f, 18.1f, 10f, 18.6f, 12f, 20.1f);
    s.cubicTo(14f, 18.6f, 17f, 18.1f, 20.5f, 18.6f); s.lineTo(20.5f, 5f); s.cubicTo(17f, 4.5f, 14f, 5f, 12f, 6.6f); s.close();
    segment(s, 12f, 6.6f, 12f, 20.1f);
    break;
   case "podcasts": // a source with two arcs and a stem
    circle(f, 12f, 10f, 1.7f);
    s.addArc(new RectF(7.5f, 5.5f, 16.5f, 14.5f), 135f, 270f);
    s.addArc(new RectF(4f, 2f, 20f, 18f), 135f, 270f);
    segment(s, 12f, 13.4f, 12f, 21f);
    break;
   case "compass": // the study's direction arrow, filled
    f.moveTo(20.2f, 3.8f); f.lineTo(3.8f, 10.9f); f.lineTo(11.2f, 12.8f); f.lineTo(13.1f, 20.2f); f.close();
    break;
   case "sky": { // a ringed planet and a small star
    circle(s, 12f, 12.5f, 5.2f);
    Path ring = new Path(); ring.addArc(new RectF(2.5f, 9f, 21.5f, 16f), 10f, 160f);
    Matrix tilt = new Matrix(); tilt.setRotate(-18f, 12f, 12.5f); ring.transform(tilt); s.addPath(ring);
    circle(f, 19f, 4.8f, .8f);
    break;
   }
   case "weather": // a sun behind a cloud
    s.addArc(new RectF(5.5f, 4.5f, 12.5f, 11.5f), 150f, 220f);
    segment(s, 9f, 1.9f, 9f, 2.6f); segment(s, 3.6f, 5.2f, 4.2f, 5.7f); segment(s, 2.4f, 8f, 3.1f, 8f); segment(s, 14.4f, 5.2f, 13.8f, 5.7f);
    s.moveTo(7.2f, 19.6f); s.lineTo(17.4f, 19.6f); s.cubicTo(19.6f, 19.6f, 21f, 18.1f, 21f, 16.3f); s.cubicTo(21f, 14.4f, 19.5f, 13f, 17.6f, 13.1f);
    s.cubicTo(17f, 10.9f, 15f, 9.4f, 12.7f, 9.7f); s.cubicTo(10.6f, 10f, 9.2f, 11.7f, 9f, 13.7f); s.cubicTo(7.3f, 13.7f, 5.4f, 14.9f, 5.4f, 16.7f);
    s.cubicTo(5.4f, 18.4f, 6.4f, 19.6f, 7.2f, 19.6f); s.close();
    break;
   case "files": // a folder
    s.moveTo(3.5f, 7f); s.cubicTo(3.5f, 5.9f, 4.4f, 5f, 5.5f, 5f); s.lineTo(9.5f, 5f); s.lineTo(11.5f, 7f); s.lineTo(18.5f, 7f);
    s.cubicTo(19.6f, 7f, 20.5f, 7.9f, 20.5f, 9f); s.lineTo(20.5f, 17.5f); s.cubicTo(20.5f, 18.6f, 19.6f, 19.5f, 18.5f, 19.5f);
    s.lineTo(5.5f, 19.5f); s.cubicTo(4.4f, 19.5f, 3.5f, 18.6f, 3.5f, 17.5f); s.close();
    segment(s, 3.5f, 10f, 20.5f, 10f);
    break;
   case "gallery": // a frame, hills and a sun
    round(s, 3.4f, 5f, 20.6f, 19f, 2f);
    s.moveTo(5.2f, 16.8f); s.lineTo(9.8f, 11.8f); s.lineTo(13.4f, 15.2f); s.lineTo(15.6f, 13.2f); s.lineTo(18.8f, 16.4f);
    circle(s, 15.8f, 9f, 1.4f);
    break;
   case "settings": // a wheel with eight teeth
    circle(s, 12f, 12f, 6.4f); circle(s, 12f, 12f, 2.6f);
    for (int i = 0; i < 8; i++) {
     double a = Math.toRadians(i * 45);
     segment(s, 12f + 6.4f * (float) Math.cos(a), 12f + 6.4f * (float) Math.sin(a), 12f + 8.8f * (float) Math.cos(a), 12f + 8.8f * (float) Math.sin(a));
    }
    break;
   case "browser": // a globe
    circle(s, 12f, 12f, 8.6f);
    s.addOval(new RectF(8.2f, 3.4f, 15.8f, 20.6f), Path.Direction.CW);
    segment(s, 3.4f, 12f, 20.6f, 12f);
    break;
   case "contacts": // a person
    circle(s, 12f, 8.4f, 3.6f);
    s.moveTo(4.8f, 20.2f); s.cubicTo(4.8f, 16.2f, 8f, 14.1f, 12f, 14.1f); s.cubicTo(16f, 14.1f, 19.2f, 16.2f, 19.2f, 20.2f);
    break;
   case "memories": // a four-point spark
    s.moveTo(12f, 3.4f); s.cubicTo(12.6f, 8.6f, 15.4f, 11.4f, 20.6f, 12f); s.cubicTo(15.4f, 12.6f, 12.6f, 15.4f, 12f, 20.6f);
    s.cubicTo(11.4f, 15.4f, 8.6f, 12.6f, 3.4f, 12f); s.cubicTo(8.6f, 11.4f, 11.4f, 8.6f, 12f, 3.4f); s.close();
    break;
   case "more": // the system folder: three dots
    circle(f, 6.4f, 12f, 1.6f); circle(f, 12f, 12f, 1.6f); circle(f, 17.6f, 12f, 1.6f);
    break;
   default:
    break;
  }
 }

 /** An app's own monochrome themed-icon layer (Android 13+), tinted cream and scaled like a launcher's themed icon,
  * or null when the app has none (the caller then shows the app's own icon unchanged). The layer is the app's own
  * artwork, so its identity is the app's, never redrawn here. */
 static Drawable themed(Drawable icon) {
  if (Build.VERSION.SDK_INT < 33 || !(icon instanceof AdaptiveIconDrawable)) return null;
  Drawable mono = ((AdaptiveIconDrawable) icon).getMonochrome();
  if (mono == null) return null;
  mono = mono.mutate();
  mono.setTint(CREAM);
  return new Themed(mono);
 }

 /** An adaptive layer is 108 units with the visible icon in the middle 72: the layer is drawn at 1.5 times the bounds,
  * centred and clipped, so the visible part fills the icon area as the app's ordinary icon would. */
 static final class Themed extends Drawable {
  final Drawable layer;
  Themed(Drawable layer) { this.layer = layer; }
  @Override protected void onBoundsChange(Rect b) {
   int w = Math.round(b.width() * 1.5f), h = Math.round(b.height() * 1.5f);
   layer.setBounds(b.centerX() - w / 2, b.centerY() - h / 2, b.centerX() - w / 2 + w, b.centerY() - h / 2 + h);
  }
  @Override public void draw(Canvas canvas) {
   int save = canvas.save();
   canvas.clipRect(getBounds());
   layer.draw(canvas);
   canvas.restoreToCount(save);
  }
  @Override public int getIntrinsicWidth() { return -1; }
  @Override public int getIntrinsicHeight() { return -1; }
  @Override public void setAlpha(int alpha) { layer.setAlpha(alpha); }
  @Override public void setColorFilter(ColorFilter filter) { layer.setColorFilter(filter); }
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }
}
