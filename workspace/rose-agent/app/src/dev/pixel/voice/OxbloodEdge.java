package dev.pixel.voice;

import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * Home's shallow burgundy edge and the controls formed in it, from the committed Rose HiFi prototype (#410; main
 * f6503a0, oxblood.css:371-380,487-496; values in qa/oxblood/oxblood-contract.json "homeEdge"). Plate: the full width
 * from the edge top to the screen bottom, with a flat top 34dp down and concave 34dp fillets rising to the edge top at
 * both sides, a vertical burgundy gradient and a faint rose sheen; a 1dp ruby hairline follows the concave contour,
 * with a soft dark falloff onto the plate. Well: the 36dp recessed plate (radius 8) of an edge control. SwitcherGlyph:
 * the reference's two pairs of offset task cards, the right back card's edge turning ruby. All decorative; the views
 * that hold them keep the clicks and names.
 */
final class OxbloodEdge {
 private OxbloodEdge() {}

 static final int HEIGHT_DP = 92, FLAT_TOP_DP = 34, FILLET_DP = 34;
 /** HomeTray's controls (oxblood.css:379-380,487-496): Rose 103 and switcher 106 wide, 44dp hit height, 14dp apart; the pearl. */
 static final int ROSE_W_DP = 103, SWITCHER_W_DP = 106, CONTROL_H_DP = 44, GAP_DP = 14, PEARL_DP = 16;
 /** The controls' touch height: the reference's 44, raised to Android's 48dp minimum (the 36dp plate is unchanged). */
 static final int TARGET_H_DP = 48;
 static final int PLATE_TOP = 0xff4a141a, PLATE_MID = 0xff3c0e13, PLATE_BOTTOM = 0xff330c10;
 static final int SHEEN = 0x12ff96a5, HAIRLINE = 0xa6d65468, FALLOFF = 0x73000000;

 /** The concave top contour (fillets and flat), in px for a plate of width w whose edge top is at y=0. */
 static Path contour(float w, float d) {
  float r = FILLET_DP * d, flat = FLAT_TOP_DP * d;
  Path p = new Path();
  p.moveTo(0, 0);
  // Circle centred (r, 0): from (0,0) down to (r, r) along its lower-left quarter.
  p.arcTo(new RectF(0, -r, 2 * r, r), 180, -90);
  p.lineTo(w - r, flat);
  p.arcTo(new RectF(w - 2 * r, -r, w, r), 90, -90);
  return p;
 }

 /** The plate. Its bounds run from the edge top to the screen bottom (taller than 92dp under 3-button navigation). */
 static final class Plate extends Drawable {
  final float d;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
  Path outline = new Path(), top = new Path();
  Plate(float density) { d = density; }

  @Override protected void onBoundsChange(Rect b) {
   top = contour(b.width(), d);
   top.offset(b.left, b.top);
   outline = new Path(top);
   outline.lineTo(b.right, b.bottom);
   outline.lineTo(b.left, b.bottom);
   outline.close();
  }

  @Override public void draw(Canvas c) {
   Rect b = getBounds();
   if (b.isEmpty()) return;
   float t = b.top;
   paint.reset(); paint.setAntiAlias(true); paint.setDither(true);
   // linear-gradient(180deg, #4a141a until 34px, #3c0e13 at 46px, #330c10 at 92px): stops in dp from the edge top.
   paint.setShader(new LinearGradient(0, t, 0, t + HEIGHT_DP * d, new int[]{PLATE_TOP, PLATE_TOP, PLATE_MID, PLATE_BOTTOM},
     new float[]{0, 34f / 92f, 46f / 92f, 1}, Shader.TileMode.CLAMP));
   c.drawPath(outline, paint);
   // Sheen: rgba(255,150,165,.07) fading out over the top 40% of the 92dp box.
   paint.setShader(new LinearGradient(0, t, 0, t + .4f * HEIGHT_DP * d, SHEEN, SHEEN & 0x00ffffff, Shader.TileMode.CLAMP));
   c.drawPath(outline, paint);
   paint.setShader(null);
   // The ::after's box-shadows along the concave contour: a soft dark falloff (0 4px 10px) and a 1px ruby line (0 1px 0).
   int save = c.save();
   c.clipPath(outline);
   // box-shadow 0 4px 10px of the box that ends at the contour: the region above it, 4dp down, blurred (sigma 5).
   Path above = new Path(top);
   above.lineTo(b.right, t - 60 * d);
   above.lineTo(b.left, t - 60 * d);
   above.close();
   above.offset(0, 4 * d);
   paint.setStyle(Paint.Style.FILL);
   paint.setColor(FALLOFF);
   paint.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (5 * d - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL));
   c.drawPath(above, paint);
   paint.setMaskFilter(null);
   paint.setStyle(Paint.Style.STROKE);
   paint.setStrokeWidth(Math.max(1f, d));
   paint.setColor(HAIRLINE);
   Path line = new Path(top); line.offset(0, .5f * Math.max(1f, d));
   c.drawPath(line, paint);
   c.restoreToCount(save);
  }

  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

 /** A control's recessed plate: 36dp tall (vertically centred in the 44dp hit area), radius 8, a faint dark-to-rose
  * fill, a 1dp warm inner ring and a 1px dark inner top shadow. Pressed moves the view down 1dp (HomeTray), not this. */
 static final class Well extends Drawable {
  static final int PLATE_H_DP = 36, RADIUS_DP = 8;
  static final int FILL_TOP = 0x47140407, FILL_BOTTOM = 0x1f46121c, RING = 0x66eccdbe, TOP_SHADOW = 0x66000000;
  final float d;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF plate = new RectF();
  Well(float density) { d = density; }

  @Override protected void onBoundsChange(Rect b) {
   float h = PLATE_H_DP * d;
   plate.set(b.left, b.exactCenterY() - h / 2, b.right, b.exactCenterY() + h / 2);
  }

  @Override public void draw(Canvas c) {
   if (plate.isEmpty()) return;
   float r = RADIUS_DP * d;
   paint.reset(); paint.setAntiAlias(true);
   paint.setShader(new LinearGradient(0, plate.top, 0, plate.bottom, FILL_TOP, FILL_BOTTOM, Shader.TileMode.CLAMP));
   c.drawRoundRect(plate, r, r, paint);
   paint.setShader(null);
   // inset 0 1px 2px rgba(0,0,0,.4): a soft dark band just inside the top edge.
   int save = c.save();
   Path clip = new Path(); clip.addRoundRect(plate, r, r, Path.Direction.CW); c.clipPath(clip);
   paint.setStyle(Paint.Style.STROKE);
   paint.setStrokeWidth(2 * d);
   paint.setColor(TOP_SHADOW);
   paint.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (1 * d - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL));
   c.drawRoundRect(plate.left - d, plate.top - d + d, plate.right + d, plate.bottom + 3 * d, r, r, paint);
   paint.setMaskFilter(null);
   c.restoreToCount(save);
   // inset 0 0 0 1px: the warm ring just inside the edge.
   float s = Math.max(1f, d), h = s / 2;
   paint.setStrokeWidth(s);
   paint.setColor(RING);
   c.drawRoundRect(plate.left + h, plate.top + h, plate.right - h, plate.bottom - h, r - h, r - h, paint);
  }

  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

 /** The system switcher symbol: two pairs of offset task cards (index.html:27), viewBox "2 3 60 20" fitted to a
  * 64x28dp box (meet), stroke 2 with round joins; the front cards filled #331019; the right back card's stroke turns
  * ruby along x (#eadbcd at x48 to #ff4a5e at x56.4); a 1dp dark drop shadow. */
 static final class SwitcherGlyph extends Drawable {
  static final int BOX_W_DP = 64, BOX_H_DP = 28, INK = 0xffeadbcd, CARD = 0xff331019, RUBY = 0xffff4a5e, SHADOW = 0x80000000;
  static final String[][] CARDS = {{"M10 11h18l-6 10H4z", "back"}, {"M10 5h18l-6 10H4z", "front"},
    {"M42 11h18l-6 10H36z", "ruby"}, {"M42 5h18l-6 10H36z", "front"}};
  final float d;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  final android.graphics.Matrix m = new android.graphics.Matrix();
  float scale = 1, ox, oy;
  SwitcherGlyph(float density) { d = density; paint.setStrokeJoin(Paint.Join.ROUND); paint.setStrokeCap(Paint.Cap.ROUND); }

  @Override protected void onBoundsChange(Rect b) {
   float w = BOX_W_DP * d, h = BOX_H_DP * d;
   scale = Math.min(w / 60f, h / 20f);
   ox = b.exactCenterX() - 30 * scale;
   oy = b.exactCenterY() - 10 * scale;
  }

  @Override public void draw(Canvas c) {
   if (getBounds().isEmpty()) return;
   int save = c.save();
   c.translate(ox - 2 * scale, oy - 3 * scale);
   c.scale(scale, scale);
   for (int pass = 0; pass < 2; pass++) { // pass 0: the drop shadow, 1dp down and softened; pass 1: the cards
    if (pass == 0) { c.save(); c.translate(0, d / scale); }
    for (String[] card : CARDS) {
     Path p = HomeGlyph.Svg.parse(card[0]);
     boolean front = "front".equals(card[1]);
     if (front && pass == 1) { paint.reset(); paint.setAntiAlias(true); paint.setColor(CARD); paint.setStyle(Paint.Style.FILL); c.drawPath(p, paint); }
     paint.reset(); paint.setAntiAlias(true); paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(2);
     paint.setStrokeJoin(Paint.Join.ROUND); paint.setStrokeCap(Paint.Cap.ROUND);
     if (pass == 0) { paint.setColor(SHADOW); paint.setMaskFilter(new BlurMaskFilter(.6f, BlurMaskFilter.Blur.NORMAL)); }
     else if ("ruby".equals(card[1])) paint.setShader(new LinearGradient(34, 0, 62, 0, new int[]{INK, INK, RUBY, RUBY}, new float[]{0, .5f, .8f, 1}, Shader.TileMode.CLAMP));
     else paint.setColor(INK);
     c.drawPath(p, paint);
    }
    if (pass == 0) c.restore();
   }
   c.restoreToCount(save);
  }

  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }
}
