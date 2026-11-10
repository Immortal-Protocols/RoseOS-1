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
 /** Rose's microphone-input mute well (oxblood.css:491-492): a 44x44 slot in the row, its plate 32x32 (inset 6),
  * radius 9. Home's touch target is 48x48 about the same centre (MIC_TARGET_W_DP, taking 2dp from each gap). */
 static final int MIC_W_DP = 44, MIC_PLATE_DP = 32, MIC_RADIUS_DP = 9, MIC_TARGET_W_DP = 48;
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
   paint.setShader(new LinearGradient(0, t, 0, t + HEIGHT_DP * d, RoseThemeNow.c(new int[]{PLATE_TOP, PLATE_TOP, PLATE_MID, PLATE_BOTTOM}),
     new float[]{0, 34f / 92f, 46f / 92f, 1}, Shader.TileMode.CLAMP));
   c.drawPath(outline, paint);
   // Sheen: rgba(255,150,165,.07) fading out over the top 40% of the 92dp box.
   int sheen = RoseThemeNow.c(SHEEN);
   paint.setShader(new LinearGradient(0, t, 0, t + .4f * HEIGHT_DP * d, sheen, sheen & 0x00ffffff, Shader.TileMode.CLAMP));
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
   paint.setColor(RoseThemeNow.c(HAIRLINE));
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
  final float d, plateW, plateH, radius;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF plate = new RectF();
  /** The full-width 36dp plate (Rose, switcher). */
  Well(float density) { this(density, -1, PLATE_H_DP, RADIUS_DP); }
  /** A plate of plateWdp x plateHdp centred in the bounds (plateWdp < 0: the full width); the mic's is 32x32 r9. */
  Well(float density, float plateWdp, float plateHdp, float radiusDp) { d = density; plateW = plateWdp; plateH = plateHdp; radius = radiusDp; }
  static Well mic(float density) { return new Well(density, MIC_PLATE_DP, MIC_PLATE_DP, MIC_RADIUS_DP); }

  @Override protected void onBoundsChange(Rect b) { centre(b, plate); }

  /** The plate's rectangle for bounds b (MicGlyph rings the same rectangle). */
  void centre(Rect b, RectF out) {
   float h = plateH * d, w = plateW < 0 ? b.width() : plateW * d;
   out.set(b.exactCenterX() - w / 2, b.exactCenterY() - h / 2, b.exactCenterX() + w / 2, b.exactCenterY() + h / 2);
  }

  @Override public void draw(Canvas c) {
   if (plate.isEmpty()) return;
   float r = radius * d;
   paint.reset(); paint.setAntiAlias(true);
   paint.setShader(new LinearGradient(0, plate.top, 0, plate.bottom, RoseThemeNow.c(FILL_TOP), RoseThemeNow.c(FILL_BOTTOM), Shader.TileMode.CLAMP));
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
   paint.setColor(RoseThemeNow.c(RING));
   c.drawRoundRect(plate.left + h, plate.top + h, plate.right - h, plate.bottom - h, r - h, r - h, paint);
  }

  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

 /** The system switcher symbol: two pairs of offset task cards (index.html:27), viewBox "2 3 60 20" fitted to a
  * 64x28dp box (meet), stroke 2 with round joins; the front cards filled #331019; the right back card's stroke turns
  * ruby along x (#eadbcd at x48 to #ff4a5e at x56.4); a 1dp dark drop shadow. */
 /**
  * Rose's microphone-input mute on the edge (#410; HiFi icons.js mic/micOff on a 24-unit grid at 20dp, oxblood.css
  * :383-384,491): a filled capsule, its cradle and stand in warm ink #e6d4c3; muted, the mic dims to .55 in ruby
  * #ff8c9a with a slash knocked out of the well, and the well gains a 1.4dp ruby inner ring and a soft ruby glow.
  * Drawn over Well.mic of the same bounds (its centred 32x32 r9 plate).
  */
 static final class MicGlyph extends Drawable {
  static final int GLYPH_DP = 20, INK = 0xffe6d4c3, MUTED_INK = 0xffff8c9a, RING = 0xbfff5a6e, GLOW = 0x4dff465f;
  final float d;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  boolean muted;
  MicGlyph(float density) { d = density; }

  void set(boolean muted) { if (this.muted != muted) { this.muted = muted; invalidateSelf(); } }

  @Override public void draw(Canvas c) {
   Rect b = getBounds();
   if (b.isEmpty()) return;
   float cx = b.exactCenterX(), cy = b.exactCenterY();
   if (muted) {
    float h = MIC_PLATE_DP * d, r = MIC_RADIUS_DP * d, w = 1.4f * d;
    RectF plate = new RectF(cx - h / 2 + w / 2, cy - h / 2 + w / 2, cx + h / 2 - w / 2, cy + h / 2 - w / 2);
    paint.reset(); paint.setAntiAlias(true); paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(4 * d); paint.setColor(RoseThemeNow.w(GLOW));
    paint.setMaskFilter(new BlurMaskFilter(5 * d, BlurMaskFilter.Blur.NORMAL));
    c.drawRoundRect(plate, r, r, paint);
    paint.setMaskFilter(null);
    paint.setStrokeWidth(w); paint.setColor(RoseThemeNow.w(RING));
    c.drawRoundRect(plate, r - w / 2, r - w / 2, paint);
   }
   int save = c.save();
   float s = GLYPH_DP * d / 24f;
   c.translate(cx - 12 * s, cy - 12 * s);
   c.scale(s, s);
   int ink = muted ? RoseThemeNow.w(MUTED_INK) : RoseThemeNow.c(INK);
   int body = muted ? (ink & 0x00ffffff) | (Math.round(.55f * 255) << 24) : ink;
   paint.reset(); paint.setAntiAlias(true);
   paint.setColor(body);
   c.drawRoundRect(new RectF(8.6f, 2.5f, 15.4f, 15f), 3.4f, 3.4f, paint);
   paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(1.9f); paint.setStrokeCap(Paint.Cap.ROUND);
   c.drawArc(new RectF(5.6f, 4.4f, 18.4f, 17.2f), 0, 180, false, paint);
   c.drawLine(12, 17.4f, 12, 20.6f, paint);
   c.drawLine(8.8f, 20.8f, 15.2f, 20.8f, paint);
   if (muted) {
    // The slash, knocked out of the well (the reference's white under-stroke, here the well's own dark) then drawn.
    paint.setStrokeWidth(3.4f); paint.setColor(RoseThemeNow.c(0xff1c0609));
    c.drawLine(4.5f, 3.5f, 19.5f, 20.5f, paint);
    paint.setStrokeWidth(1.9f); paint.setColor(ink);
    c.drawLine(4.5f, 3.5f, 19.5f, 20.5f, paint);
   }
   c.restoreToCount(save);
  }
  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter cf) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

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
     if (front && pass == 1) { paint.reset(); paint.setAntiAlias(true); paint.setColor(RoseThemeNow.c(CARD)); paint.setStyle(Paint.Style.FILL); c.drawPath(p, paint); }
     paint.reset(); paint.setAntiAlias(true); paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(2);
     paint.setStrokeJoin(Paint.Join.ROUND); paint.setStrokeCap(Paint.Cap.ROUND);
     if (pass == 0) { paint.setColor(SHADOW); paint.setMaskFilter(new BlurMaskFilter(.6f, BlurMaskFilter.Blur.NORMAL)); }
     else if ("ruby".equals(card[1])) paint.setShader(new LinearGradient(34, 0, 62, 0, RoseThemeNow.c(new int[]{INK, INK, RUBY, RUBY}), new float[]{0, .5f, .8f, 1}, Shader.TileMode.CLAMP));
     else paint.setColor(RoseThemeNow.c(INK));
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
