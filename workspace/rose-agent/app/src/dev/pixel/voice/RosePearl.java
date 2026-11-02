package dev.pixel.voice;

import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * The one ruby pearl that stands for Rose on every Oxblood surface (#410; main f6503a0, oxblood.css:337-350,394-398;
 * qa/oxblood/oxblood-contract.json "pearl"). A small sphere: a radial gradient lit from (34%, 28%) running from a
 * near-white glint through ruby to a deep oxblood rim, with a 2dp drop shadow and a faint lower inner shade. Silver when
 * Rose's microphone is truthfully muted (the caller decides: Rose's own call mute only, never an OS-wide claim). The
 * Rose header adds the listening ring and the speaking breath (set by the caller from real state); Home shows neither.
 * Drawn centred in its bounds at size PEARL_DP; decorative, the holding view speaks for it.
 */
final class RosePearl extends Drawable {
 static final int[] RUBY = {0xffffe6e8, 0xffffe6e8, 0xffe8606f, 0xffc43048, 0xff8a1428, 0xff4a0612};
 static final float[] RUBY_STOPS = {0, .07f, .20f, .46f, .76f, 1};
 static final int[] SILVER = {0xffffffff, 0xffffffff, 0xffd9d4d2, 0xff9b9391, 0xff5c5452, 0xff3a3433};
 static final float[] SILVER_STOPS = {0, .08f, .24f, .55f, .85f, 1};
 static final int SHADOW = 0x99000000, INNER_SHADE = 0x59000000;
 static final int RING_GAP = 0xe614080a, LISTEN_RING = 0x8ce65064;

 final float d, sizeDp;
 final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
 boolean silver, listening;
 /** Speaking breath phase 0..1 (0 = the static ring); negative = not speaking. */
 float breath = -1;

 RosePearl(float density, float sizeDp) { d = density; this.sizeDp = sizeDp; }

 /** Returns true when the look changed. */
 boolean set(boolean silver, boolean listening, float breath) {
  if (this.silver == silver && this.listening == listening && this.breath == breath) return false;
  this.silver = silver; this.listening = listening; this.breath = breath;
  invalidateSelf();
  return true;
 }

 @Override public void draw(Canvas c) {
  Rect b = getBounds();
  if (b.isEmpty()) return;
  float r = sizeDp * d / 2, cx = b.exactCenterX(), cy = b.exactCenterY();
  paint.reset(); paint.setAntiAlias(true);
  if (breath >= 0 || listening) {
   // box-shadow 0 0 0 Npx: solid rings outside the pearl, the dark gap first, then the ruby ring and (speaking) a bloom.
   float t = breath < 0 ? 0 : (float) (1 - Math.cos(2 * Math.PI * breath)) / 2; // 0 at 0% and 100%, 1 at 50%
   float gap = 3 + t, ring = breath < 0 ? 4 : 4 + 1.5f * t;
   if (breath >= 0) {
    float blur = 8 + 6 * t, spread = 3 + 3 * t;
    int glowA = Math.round((.25f + .10f * t) * 255);
    paint.setColor((glowA << 24) | 0xe65064);
    paint.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (blur / 2 * d - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL));
    c.drawCircle(cx, cy, r + spread * d, paint);
    paint.setMaskFilter(null);
    int ringA = Math.round((.5f + .25f * t) * 255);
    int rgb = blend(0xe65064, 0xff788c, t);
    paint.setColor((ringA << 24) | rgb);
   } else paint.setColor(LISTEN_RING);
   c.drawCircle(cx, cy, r + ring * d, paint);
   paint.setColor(RING_GAP);
   c.drawCircle(cx, cy, r + gap * d, paint);
  }
  // 0 2px 3px rgba(0,0,0,.6)
  paint.setColor(SHADOW);
  paint.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (1.5f * d - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL));
  c.drawCircle(cx, cy + 2 * d, r, paint);
  paint.setMaskFilter(null);
  // radial-gradient(circle at 34% 28%, ...): radius to the farthest corner of the 2r box.
  float gx = cx - r + .34f * 2 * r, gy = cy - r + .28f * 2 * r;
  float far = (float) Math.hypot(cx + r - gx, cy + r - gy);
  paint.setColor(0xff000000); // opaque: the shader alone colours the pearl
  paint.setShader(new RadialGradient(gx, gy, far, silver ? SILVER : RUBY, silver ? SILVER_STOPS : RUBY_STOPS, Shader.TileMode.CLAMP));
  c.drawCircle(cx, cy, r, paint);
  paint.setShader(null);
  // inset 0 -1px 2px rgba(0,0,0,.35): a soft shade just inside the lower rim.
  int save = c.save();
  android.graphics.Path clip = new android.graphics.Path(); clip.addCircle(cx, cy, r, android.graphics.Path.Direction.CW); c.clipPath(clip);
  paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(2 * d); paint.setColor(INNER_SHADE);
  paint.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (1 * d - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL));
  c.drawCircle(cx, cy - d, r + d, paint);
  c.restoreToCount(save);
 }

 static int blend(int a, int b, float t) {
  int r = Math.round(((a >> 16) & 255) * (1 - t) + ((b >> 16) & 255) * t), g = Math.round(((a >> 8) & 255) * (1 - t) + ((b >> 8) & 255) * t),
    bl = Math.round((a & 255) * (1 - t) + (b & 255) * t);
  return (r << 16) | (g << 8) | bl;
 }

 @Override public int getIntrinsicWidth() { return Math.round((sizeDp + 12) * d); }
 @Override public int getIntrinsicHeight() { return Math.round((sizeDp + 12) * d); }
 @Override public void setAlpha(int alpha) {}
 @Override public void setColorFilter(ColorFilter filter) {}
 @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
