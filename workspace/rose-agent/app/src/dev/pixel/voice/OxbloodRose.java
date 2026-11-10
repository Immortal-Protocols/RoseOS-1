package dev.pixel.voice;

import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * The Rose conversation's Oxblood controls (#410; main f6503a0, oxblood.css:88-97,260-267,406-414,500-508;
 * qa/oxblood/oxblood-contract.json "rose"). WorkButton: the upper-right Background Work entry, a 38dp hairline square
 * (radius 10) in its 44dp target with a 2x2 grid of outlined panes; while Rose authoritatively has work running
 * (the caller decides), a rose ring, a soft glow and a 9dp ruby badge at the top right. Pill: the single
 * Connect/Disconnect control's 36dp plate (radius 10) with a fine ivory hairline, and its glyph: a thin ruby x while a
 * call is live or connecting, a 12dp ruby dot when disconnected. Decorative; the views hold the clicks and names.
 */
final class OxbloodRose {
 private OxbloodRose() {}

 static final int INK = 0xffffffff, INK_2 = 0xc7ffffff, LINE = 0x38f1e9e1;

 static final class WorkButton extends Drawable {
  static final int IDLE_RING = 0x9ec8b6ae, WORK_RING = 0xb3ff96a5, WORK_GLOW = 0x59e6465f, BADGE = 0xffff5a68,
    BADGE_RING = 0xff2a0a10, BADGE_GLOW = 0xccff5a68;
  final float d;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  boolean working, reducedMotion;
  WorkButton(float density) { d = density; }
  boolean set(boolean working, boolean reducedMotion) {
   if (this.working == working && this.reducedMotion == reducedMotion) return false;
   this.working = working; this.reducedMotion = reducedMotion; invalidateSelf(); return true;
  }
  @Override public void draw(Canvas c) {
   Rect b = getBounds();
   if (b.isEmpty()) return;
   RectF sq = new RectF(b.left + 3 * d, b.top + 3 * d, b.right - 3 * d, b.bottom - 3 * d);
   float r = 10 * d, w = 1.5f * d;
   paint.reset(); paint.setAntiAlias(true); paint.setStyle(Paint.Style.STROKE);
   if (working) {
    paint.setColor(RoseThemeNow.c(WORK_GLOW)); paint.setStrokeWidth(4 * d);
    paint.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (6 * d - .5f) / .57735f), BlurMaskFilter.Blur.OUTER));
    c.drawRoundRect(sq, r, r, paint);
    paint.setMaskFilter(null);
   }
   paint.setStrokeWidth(w);
   paint.setColor(RoseThemeNow.c(working ? WORK_RING : IDLE_RING));
   c.drawRoundRect(sq.left + w / 2, sq.top + w / 2, sq.right - w / 2, sq.bottom - w / 2, r - w / 2, r - w / 2, paint);
   // 2x2 panes of 8dp, 4dp apart, radius 2, 1.5dp white outline, centred.
   paint.setColor(INK);
   float cell = 8 * d, gap = 4 * d, x0 = b.exactCenterX() - cell - gap / 2, y0 = b.exactCenterY() - cell - gap / 2;
   for (int i = 0; i < 4; i++) {
    float x = x0 + (i % 2) * (cell + gap), y = y0 + (i / 2) * (cell + gap);
    c.drawRoundRect(x + w / 2, y + w / 2, x + cell - w / 2, y + cell - w / 2, 2 * d, 2 * d, paint);
   }
   if (working) {
    float cx = b.right - 4.5f * d, cy = b.top + 4.5f * d, br = 4.5f * d;
    paint.setStyle(Paint.Style.FILL);
    if (!reducedMotion) { // prefers-reduced-motion drops only the badge's glow (oxblood.css:161)
     paint.setColor(RoseThemeNow.w(BADGE_GLOW)); // plan 1E: the running badge is an attention mark, warm in every theme
     paint.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (4 * d - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL));
     c.drawCircle(cx, cy, br, paint);
     paint.setMaskFilter(null);
    }
    paint.setColor(RoseThemeNow.c(BADGE_RING)); c.drawCircle(cx, cy, br + 2 * d, paint);
    paint.setColor(RoseThemeNow.w(BADGE)); c.drawCircle(cx, cy, br, paint);
   }
  }
  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

 /** The Connect/Disconnect plate: 36dp tall, radius 10, rgba(34,12,14,.82), 1dp hairline rgba(241,233,225,.78). */
 static final class Pill extends Drawable {
  static final int FILL = 0xd1220c0e, RING = 0xc7f1e9e1;
  final float d;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  Pill(float density) { d = density; }
  @Override public void draw(Canvas c) {
   Rect b = getBounds();
   if (b.isEmpty()) return;
   float h = 36 * d, r = 10 * d, s = Math.max(1f, d);
   RectF plate = new RectF(b.left, b.exactCenterY() - h / 2, b.right, b.exactCenterY() + h / 2);
   paint.reset(); paint.setAntiAlias(true); paint.setColor(RoseThemeNow.c(FILL));
   c.drawRoundRect(plate, r, r, paint);
   paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(s); paint.setColor(RoseThemeNow.c(RING));
   c.drawRoundRect(plate.left + s / 2, plate.top + s / 2, plate.right - s / 2, plate.bottom - s / 2, r - s / 2, r - s / 2, paint);
  }
  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

 /** The pill's glyph: live = a 15dp ruby x (stroke 1.6 of 24); idle = a 12dp ruby dot (its own fixed ruby). */
 static final class PillGlyph extends Drawable {
  static final int X = 0xffd93a55;
  final float d;
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  boolean live;
  PillGlyph(float density) { d = density; paint.setStrokeCap(Paint.Cap.ROUND); }
  boolean set(boolean live) { if (this.live == live) return false; this.live = live; invalidateSelf(); return true; }
  @Override public void draw(Canvas c) {
   Rect b = getBounds();
   float cx = b.exactCenterX(), cy = b.exactCenterY();
   paint.reset(); paint.setAntiAlias(true);
   if (live) {
    float u = 15 * d / 24f;
    paint.setStyle(Paint.Style.STROKE); paint.setStrokeCap(Paint.Cap.ROUND); paint.setStrokeWidth(Math.max(1f, 1.6f * u)); paint.setColor(RoseThemeNow.w(X));
    float a = 5.8f * u;
    c.drawLine(cx - a, cy - a, cx + a, cy + a, paint);
    c.drawLine(cx + a, cy - a, cx - a, cy + a, paint);
   } else {
    float r = 6 * d;
    paint.setShader(new RadialGradient(cx - r + .34f * 2 * r, cy - r + .30f * 2 * r, 2 * r * 1.0f,
      RoseThemeNow.c(new int[]{0xffffffff, 0xffffb3bf, 0xffe04a62, 0xff8e1a2e}), new float[]{0, .18f, .5f, 1}, Shader.TileMode.CLAMP));
    c.drawCircle(cx, cy, r, paint);
   }
  }
  @Override public int getIntrinsicWidth() { return Math.round(15 * d); }
  @Override public int getIntrinsicHeight() { return Math.round(15 * d); }
  @Override public void setAlpha(int alpha) {}
  @Override public void setColorFilter(ColorFilter filter) {}
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }

 /** The caption (app.js:350): Connecting… / Rose speaking / Microphone muted / Listening / Disconnected, from the
  * session state and the visual surround (speaking only from fresh measured output). */
 static String caption(ConversationState s, RoseVisualState v) {
  if (s == null) return "Disconnected";
  if (s.phase == ConversationState.Phase.CONNECTING) return "Connecting…";
  if (s.phase != ConversationState.Phase.ACTIVE) return "Disconnected";
  if (v != null && v.surround == RoseVisualState.Surround.SPEAKING) return "Rose speaking";
  return s.muted ? "Microphone muted" : "Listening";
 }
}
