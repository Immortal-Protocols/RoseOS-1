package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;
import java.util.ArrayList;

/**
 * The Oxblood system-app glyphs of the committed Rose HiFi prototype (#410; main f6503a0, prototypes/rose-hifi/src/
 * icons.js MONO, "study design 07"): one ivory stroke on a 48-unit grid, 2.6 units wide with round caps and joins, no
 * fill except the accents the reference fills. The reference's twelve marks (phone, messages, the direction arrow used
 * for Compass, camera, mail, calendar, notes, clock, calculator, contacts, settings and the four-circle System folder)
 * are its own path data, parsed here unchanged (Svg). The other suite apps keep the #403 marks in the same language,
 * drawn on a 24-unit grid and doubled. Only Rose's suite and its system folder are drawn this way; regular installed
 * apps keep their own icons (HomeAppTile). Decorative: the cell keeps the app's name and its click.
 */
final class HomeGlyph extends Drawable {
 /** The reference glyph colour (oxblood.css:360, .mono-app svg color #ecdccb). */
 static final int CREAM = 0xffecdccb;
 static final float GRID = 48f, STROKE = 2.6f;
 static final String[] KEYS = {"phone", "messages", "mail", "calendar", "clock", "camera", "calculator", "notes",
   "reminders", "news", "books", "podcasts", "compass", "sky", "weather", "files", "gallery", "settings", "browser",
   "contacts", "memories", "system"};

 final String key;
 /** Each part is a path drawn with its own stroke width (0 = filled). */
 final ArrayList<Path> parts = new ArrayList<>();
 final ArrayList<Float> widths = new ArrayList<>();
 final ArrayList<Path> drawn = new ArrayList<>();
 final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
 final Matrix matrix = new Matrix();
 float scale = 1;

 /** An SVG path for a gear outline centred at (cx, cy): teeth flat-topped at radius outer, the ring between them at
  * radius root; each tooth spans 40% of its pitch at the top and 56% at the root, so the sides slope like a real
  * gear's. One closed path (no fill), drawn with the glyph stroke and round joins. */
 static String gear(float cx, float cy, int teeth, float outer, float root) {
  StringBuilder d = new StringBuilder();
  double pitch = 2 * Math.PI / teeth, top = pitch * .20, base = pitch * .28;
  for (int i = 0; i < teeth; i++) {
   double a = -Math.PI / 2 + i * pitch;
   double[][] pts = {{a - base, root}, {a - top, outer}, {a + top, outer}, {a + base, root}, {a + pitch / 2, root}};
   for (int k = 0; k < pts.length; k++) {
    d.append(i == 0 && k == 0 ? 'M' : 'L');
    d.append(String.format(java.util.Locale.ROOT, "%.2f %.2f", cx + pts[k][1] * Math.cos(pts[k][0]), cy + pts[k][1] * Math.sin(pts[k][0])));
   }
  }
  return d.append('Z').toString();
 }

 HomeGlyph(String key) {
  this.key = key;
  paint.setColor(CREAM);
  paint.setStrokeCap(Paint.Cap.ROUND);
  paint.setStrokeJoin(Paint.Join.ROUND);
  build(key);
 }

 static boolean known(String key) {
  for (String k : KEYS) if (k.equals(key)) return true;
  return false;
 }

 @Override protected void onBoundsChange(Rect bounds) {
  float side = Math.min(bounds.width(), bounds.height());
  scale = side / GRID;
  matrix.setScale(scale, scale);
  matrix.postTranslate(bounds.exactCenterX() - side / 2, bounds.exactCenterY() - side / 2);
  drawn.clear();
  for (Path p : parts) { Path d = new Path(); p.transform(matrix, d); drawn.add(d); }
 }

 @Override public void draw(Canvas canvas) {
  if (getBounds().isEmpty()) return;
  for (int i = 0; i < drawn.size(); i++) {
   float w = widths.get(i);
   paint.setStyle(w == 0 ? Paint.Style.FILL : Paint.Style.STROKE);
   paint.setStrokeWidth(w * scale);
   canvas.drawPath(drawn.get(i), paint);
  }
 }

 @Override public int getIntrinsicWidth() { return -1; }
 @Override public int getIntrinsicHeight() { return -1; }
 @Override public void setAlpha(int alpha) { paint.setAlpha(alpha); invalidateSelf(); }
 @Override public void setColorFilter(ColorFilter filter) { paint.setColorFilter(filter); invalidateSelf(); }
 @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }

 private void stroke(String d) { stroke(d, STROKE); }
 private void stroke(String d, float width) { parts.add(Svg.parse(d)); widths.add(width); }
 private void fill(String d) { parts.add(Svg.parse(d)); widths.add(0f); }
 private void circle(float cx, float cy, float r, float width) {
  Path p = new Path(); p.addCircle(cx, cy, r, Path.Direction.CW); parts.add(p); widths.add(width);
 }
 private void rect(float x, float y, float w, float h, float rx, float width) {
  Path p = new Path(); p.addRoundRect(new RectF(x, y, x + w, y + h), rx, rx, Path.Direction.CW); parts.add(p); widths.add(width);
 }
 /** A #403 mark on the 24-unit grid, doubled onto the 48-unit grid. */
 private void legacy(Path p, float width24) {
  Matrix twice = new Matrix(); twice.setScale(2, 2); p.transform(twice); parts.add(p); widths.add(width24 * 2);
 }

 private void build(String key) {
  switch (key) {
   // ---- the reference's own marks (icons.js:46-57), verbatim path data ----
   case "phone":
    stroke("M14.5 9.5c1.6-.9 3.4-.4 4.2 1.1l2.6 4.7c.7 1.3.3 2.9-.9 3.7l-2 1.3c1.4 3.5 4.3 6.7 7.9 8.3l1.4-2c.8-1.2 2.4-1.6 3.7-.9l4.7 2.6c1.5.8 2 2.6 1.1 4.2l-1.5 2.4c-1.2 1.8-3.4 2.7-5.5 2-9.3-3.1-15.6-9.6-18.6-18.6-.7-2.1.2-4.3 2-5.5z");
    break;
   case "messages":
    stroke("M24 11c-8.8 0-15 5.1-15 11.5 0 3.6 1.9 6.7 5 8.8L12.5 37l6.3-3.4c1.6.4 3.4.6 5.2.6 8.8 0 15-5.1 15-11.7S32.8 11 24 11z");
    break;
   case "compass": // the reference's maps arrow: Rose's suite has Compass where the study shows Maps
    fill("M37 11 12.5 22.6l10.3 2.6 2.6 10.3z");
    break;
   case "camera":
    rect(8.5f, 15, 31, 22, 3.5f, STROKE);
    stroke("M18 15l2.2-4h7.6L30 15");
    circle(24, 26, 5.6f, STROKE);
    circle(13, 19.5f, .6f, 0); circle(35, 19.5f, .6f, 0);
    // stroke-width also applies to the two filled dots in the reference (fill and stroke both currentColor)
    circle(13, 19.5f, .6f, STROKE); circle(35, 19.5f, .6f, STROKE);
    break;
   case "mail":
    rect(8.5f, 13.5f, 31, 21, 2.5f, STROKE);
    stroke("M9.5 15l14.5 11 14.5-11");
    break;
   case "calendar":
    rect(9.5f, 12, 29, 26, 3.5f, STROKE);
    stroke("M16.5 8.5v6M31.5 8.5v6M9.5 19h29");
    for (int r = 0; r < 3; r++) for (int c = 0; c < 4; c++) { circle(17 + c * 4.7f, 24.5f + r * 4.5f, .45f, 0); circle(17 + c * 4.7f, 24.5f + r * 4.5f, .45f, STROKE); }
    break;
   case "notes":
    stroke("M12 16.5h2M19 16.5h17M12 24h24M15 31.5h18");
    break;
   case "clock":
    circle(24, 24, 14, STROKE);
    stroke("M24 16v8.5l5.5 3.5");
    break;
   case "calculator":
    rect(13, 8.5f, 22, 31, 3.5f, STROKE);
    stroke("M17.5 14.5h13M18.5 22h.01M24 22h.01M29.5 22h.01M18.5 27.5h.01M24 27.5h.01M29.5 27.5h.01M18.5 33h.01M24 33h.01M29.5 33h.01", 3.2f);
    break;
   case "contacts":
    circle(24, 19, 6, STROKE);
    stroke("M13 37c1.5-6.2 5.5-9 11-9s9.5 2.8 11 9");
    break;
   case "settings": // F05: a gear (eight teeth on a ring, a centre hole), never the reference's sun-like rays
    stroke(gear(24, 24, 8, 15.5f, 11.5f));
    circle(24, 24, 4.6f, STROKE);
    break;
   case "system": // icons.js:53, the System apps folder
    circle(17, 17, 6, 0); circle(17, 17, 6, 2.4f);
    circle(31, 17, 5.8f, 2.4f);
    circle(17, 31, 5.8f, 2.4f);
    circle(31, 31, 6, 0); circle(31, 31, 6, 2.4f);
    circle(24, 24, 1.1f, 0);
    break;
   // ---- the other suite apps: #403 marks in the same language (24-unit grid, doubled) ----
   default: {
    Path s = new Path(), f = new Path();
    legacy24(key, s, f);
    if (!s.isEmpty()) legacy(s, 1.3f);
    if (!f.isEmpty()) legacy(f, 0);
   }
  }
 }

 private static void segment(Path p, float x1, float y1, float x2, float y2) { p.moveTo(x1, y1); p.lineTo(x2, y2); }
 private static void dot(Path p, float x, float y, float r) { p.addCircle(x, y, r, Path.Direction.CW); }

 /** The #403 marks for the suite apps the study does not draw, on a 24-unit grid. */
 static void legacy24(String key, Path s, Path f) {
  switch (key) {
   case "reminders":
    s.moveTo(4f, 7f); s.lineTo(5.6f, 8.6f); s.lineTo(8.4f, 5.6f); segment(s, 11.5f, 7f, 20f, 7f);
    s.moveTo(4f, 12.6f); s.lineTo(5.6f, 14.2f); s.lineTo(8.4f, 11.2f); segment(s, 11.5f, 12.6f, 20f, 12.6f);
    s.addCircle(6.2f, 18.4f, 1.6f, Path.Direction.CW); segment(s, 11.5f, 18.4f, 20f, 18.4f);
    break;
   case "news":
    s.moveTo(17f, 8.2f); s.lineTo(20.5f, 8.2f); s.lineTo(20.5f, 17.6f); s.cubicTo(20.5f, 18.7f, 19.6f, 19.6f, 18.5f, 19.6f);
    s.lineTo(5.5f, 19.6f); s.cubicTo(4.4f, 19.6f, 3.5f, 18.7f, 3.5f, 17.6f); s.lineTo(3.5f, 4.4f); s.lineTo(17f, 4.4f); s.lineTo(17f, 17.6f);
    s.cubicTo(17f, 18.7f, 17.9f, 19.6f, 18.5f, 19.6f);
    segment(s, 6.6f, 8.2f, 13.9f, 8.2f); segment(s, 6.6f, 11.8f, 13.9f, 11.8f); segment(s, 6.6f, 15.4f, 11.4f, 15.4f);
    break;
   case "books":
    s.moveTo(12f, 6.6f); s.cubicTo(10f, 5f, 7f, 4.5f, 3.5f, 5f); s.lineTo(3.5f, 18.6f); s.cubicTo(7f, 18.1f, 10f, 18.6f, 12f, 20.1f);
    s.cubicTo(14f, 18.6f, 17f, 18.1f, 20.5f, 18.6f); s.lineTo(20.5f, 5f); s.cubicTo(17f, 4.5f, 14f, 5f, 12f, 6.6f); s.close();
    segment(s, 12f, 6.6f, 12f, 20.1f);
    break;
   case "podcasts":
    dot(f, 12f, 10f, 1.7f);
    s.addArc(new RectF(7.5f, 5.5f, 16.5f, 14.5f), 135f, 270f);
    s.addArc(new RectF(4f, 2f, 20f, 18f), 135f, 270f);
    segment(s, 12f, 13.4f, 12f, 21f);
    break;
   case "sky": {
    s.addCircle(12f, 12.5f, 5.2f, Path.Direction.CW);
    Path ring = new Path(); ring.addArc(new RectF(2.5f, 9f, 21.5f, 16f), 10f, 160f);
    Matrix tilt = new Matrix(); tilt.setRotate(-18f, 12f, 12.5f); ring.transform(tilt); s.addPath(ring);
    dot(f, 19f, 4.8f, .8f);
    break;
   }
   case "weather":
    s.addArc(new RectF(5.5f, 4.5f, 12.5f, 11.5f), 150f, 220f);
    segment(s, 9f, 1.9f, 9f, 2.6f); segment(s, 3.6f, 5.2f, 4.2f, 5.7f); segment(s, 2.4f, 8f, 3.1f, 8f); segment(s, 14.4f, 5.2f, 13.8f, 5.7f);
    s.moveTo(7.2f, 19.6f); s.lineTo(17.4f, 19.6f); s.cubicTo(19.6f, 19.6f, 21f, 18.1f, 21f, 16.3f); s.cubicTo(21f, 14.4f, 19.5f, 13f, 17.6f, 13.1f);
    s.cubicTo(17f, 10.9f, 15f, 9.4f, 12.7f, 9.7f); s.cubicTo(10.6f, 10f, 9.2f, 11.7f, 9f, 13.7f); s.cubicTo(7.3f, 13.7f, 5.4f, 14.9f, 5.4f, 16.7f);
    s.cubicTo(5.4f, 18.4f, 6.4f, 19.6f, 7.2f, 19.6f); s.close();
    break;
   case "files":
    s.moveTo(3.5f, 7f); s.cubicTo(3.5f, 5.9f, 4.4f, 5f, 5.5f, 5f); s.lineTo(9.5f, 5f); s.lineTo(11.5f, 7f); s.lineTo(18.5f, 7f);
    s.cubicTo(19.6f, 7f, 20.5f, 7.9f, 20.5f, 9f); s.lineTo(20.5f, 17.5f); s.cubicTo(20.5f, 18.6f, 19.6f, 19.5f, 18.5f, 19.5f);
    s.lineTo(5.5f, 19.5f); s.cubicTo(4.4f, 19.5f, 3.5f, 18.6f, 3.5f, 17.5f); s.close();
    segment(s, 3.5f, 10f, 20.5f, 10f);
    break;
   case "gallery":
    s.addRoundRect(new RectF(3.4f, 5f, 20.6f, 19f), 2f, 2f, Path.Direction.CW);
    s.moveTo(5.2f, 16.8f); s.lineTo(9.8f, 11.8f); s.lineTo(13.4f, 15.2f); s.lineTo(15.6f, 13.2f); s.lineTo(18.8f, 16.4f);
    s.addCircle(15.8f, 9f, 1.4f, Path.Direction.CW);
    break;
   case "browser":
    s.addCircle(12f, 12f, 8.6f, Path.Direction.CW);
    s.addOval(new RectF(8.2f, 3.4f, 15.8f, 20.6f), Path.Direction.CW);
    segment(s, 3.4f, 12f, 20.6f, 12f);
    break;
   case "memories":
    s.moveTo(12f, 3.4f); s.cubicTo(12.6f, 8.6f, 15.4f, 11.4f, 20.6f, 12f); s.cubicTo(15.4f, 12.6f, 12.6f, 15.4f, 12f, 20.6f);
    s.cubicTo(11.4f, 15.4f, 8.6f, 12.6f, 3.4f, 12f); s.cubicTo(8.6f, 11.4f, 11.4f, 8.6f, 12f, 3.4f); s.close();
    break;
   default:
    break;
  }
 }

 /** A minimal SVG path-data parser for the reference glyphs: M L H V C S Z in absolute and relative forms, with
  * implicit repeats. Anything else (arcs, quadratics) is not used by these glyphs and is refused loudly. */
 static final class Svg {
  private Svg() {}
  static Path parse(String d) {
   Path p = new Path();
   int[] at = {0};
   float x = 0, y = 0, sx = 0, sy = 0, cx2 = 0, cy2 = 0;
   char cmd = 0, prev = 0;
   while (true) {
    skip(d, at);
    if (at[0] >= d.length()) break;
    char c = d.charAt(at[0]);
    if (Character.isLetter(c)) { cmd = c; at[0]++; }
    else if (cmd == 'M') cmd = 'L'; else if (cmd == 'm') cmd = 'l';
    boolean rel = Character.isLowerCase(cmd);
    switch (Character.toUpperCase(cmd)) {
     case 'M': { float nx = num(d, at), ny = num(d, at); if (rel) { nx += x; ny += y; } p.moveTo(nx, ny); x = sx = nx; y = sy = ny; break; }
     case 'L': { float nx = num(d, at), ny = num(d, at); if (rel) { nx += x; ny += y; } p.lineTo(nx, ny); x = nx; y = ny; break; }
     case 'H': { float nx = num(d, at); if (rel) nx += x; p.lineTo(nx, y); x = nx; break; }
     case 'V': { float ny = num(d, at); if (rel) ny += y; p.lineTo(x, ny); y = ny; break; }
     case 'C': {
      float x1 = num(d, at), y1 = num(d, at), x2 = num(d, at), y2 = num(d, at), nx = num(d, at), ny = num(d, at);
      if (rel) { x1 += x; y1 += y; x2 += x; y2 += y; nx += x; ny += y; }
      p.cubicTo(x1, y1, x2, y2, nx, ny); cx2 = x2; cy2 = y2; x = nx; y = ny; break;
     }
     case 'S': {
      float x2 = num(d, at), y2 = num(d, at), nx = num(d, at), ny = num(d, at);
      if (rel) { x2 += x; y2 += y; nx += x; ny += y; }
      char pu = Character.toUpperCase(prev);
      float x1 = pu == 'C' || pu == 'S' ? 2 * x - cx2 : x, y1 = pu == 'C' || pu == 'S' ? 2 * y - cy2 : y;
      p.cubicTo(x1, y1, x2, y2, nx, ny); cx2 = x2; cy2 = y2; x = nx; y = ny; break;
     }
     case 'Z': p.close(); x = sx; y = sy; break;
     default: throw new IllegalArgumentException("unsupported path command " + cmd);
    }
    prev = cmd;
   }
   return p;
  }
  private static void skip(String d, int[] at) {
   while (at[0] < d.length() && (d.charAt(at[0]) == ' ' || d.charAt(at[0]) == ',' || d.charAt(at[0]) == '\n')) at[0]++;
  }
  /** One number: optional sign, digits, one decimal point (".9.5" is two numbers), optional exponent. */
  static float num(String d, int[] at) {
   skip(d, at);
   int start = at[0], i = start;
   if (i < d.length() && (d.charAt(i) == '-' || d.charAt(i) == '+')) i++;
   boolean dot = false;
   while (i < d.length()) {
    char c = d.charAt(i);
    if (Character.isDigit(c)) i++;
    else if (c == '.' && !dot) { dot = true; i++; }
    else break;
   }
   if (i < d.length() && (d.charAt(i) == 'e' || d.charAt(i) == 'E')) {
    i++;
    if (i < d.length() && (d.charAt(i) == '-' || d.charAt(i) == '+')) i++;
    while (i < d.length() && Character.isDigit(d.charAt(i))) i++;
   }
   if (i == start) throw new IllegalArgumentException("expected a number at " + start + " in " + d);
   at[0] = i;
   return Float.parseFloat(d.substring(start, i));
  }
 }
}
