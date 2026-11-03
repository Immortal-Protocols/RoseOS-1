package dev.pixel.voice;

import android.animation.ValueAnimator;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;
import android.view.animation.PathInterpolator;

/**
 * The display's glass sheen from the committed Oxblood reference (#410; main f6503a0, style.css:405-418,542-545,
 * 617-620 with oxblood.css:27-28,368): a still pane of faint light over the whole screen, drawn as a foreground that
 * never takes a touch. Two 118-degree bands (a bright one about a third of the way across, a fainter one near two
 * thirds) on a 240% gradient box placed at 26% 30%, a soft glow from the top-left corner, and a fine highlight along
 * the top and left edges. A narrow skewed band sweeps across once every 8s (1.76s of travel after a 1.2s start, then
 * rest); it runs only while the sheen is visible with animations on, and with animations off it stays parked offscreen.
 * The whole layer is drawn at the surface's opacity: .35 on Home, .55 on Background Work, none on Rose.
 */
final class GlassSheen extends Drawable {
 static final float HOME = .35f, BACKGROUND_WORK = .55f;
 static final long CYCLE_MS = 8000, DELAY_MS = 1200;
 static final float TRAVEL = .22f;

 final float opacity;
 final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
 final PathInterpolator travel = new PathInterpolator(.5f, 0f, .3f, 1f);
 ValueAnimator sweep;
 /** Sweep progress 0..1 across the screen, or -1 while parked offscreen. */
 float band = -1;
 LinearGradient bands, sweepBand;
 RadialGradient glow;

 GlassSheen(float opacity) { this.opacity = opacity; }

 @Override protected void onBoundsChange(Rect b) {
  float w = b.width(), h = b.height();
  if (w <= 0 || h <= 0) return;
  // linear-gradient(118deg, ...) on a 240% x 240% box positioned at 26% 30%.
  float bw = 2.4f * w, bh = 2.4f * h, bx = b.left + (w - bw) * .26f, by = b.top + (h - bh) * .30f;
  double a = Math.toRadians(118);
  float dx = (float) Math.sin(a), dy = (float) -Math.cos(a);
  float len = Math.abs(bw * dx) + Math.abs(bh * dy), cx = bx + bw / 2, cy = by + bh / 2;
  bands = new LinearGradient(cx - dx * len / 2, cy - dy * len / 2, cx + dx * len / 2, cy + dy * len / 2,
    new int[]{0, 0, 0x0affffff, 0x42ffffff, 0x12ffffff, 0, 0, 0x1affffff, 0},
    new float[]{0, .31f, .33f, .365f, .39f, .43f, .61f, .635f, .66f}, Shader.TileMode.CLAMP);
  // radial-gradient(55% 28% at 6% 0%, rgba(255,255,255,.2), transparent 70%): an ellipse, so the circle is squashed.
  float rx = .55f * w, ry = .28f * h;
  glow = new RadialGradient(b.left + .06f * w, b.top, rx, new int[]{0x33ffffff, 0x00ffffff}, new float[]{0, .7f}, Shader.TileMode.CLAMP);
  Matrix squash = new Matrix(); squash.setScale(1, ry / rx, b.left + .06f * w, b.top); glow.setLocalMatrix(squash);
  // The sweep band: 42% of the width, 0 .26 .12 0 across it.
  float sw = .42f * w;
  sweepBand = new LinearGradient(0, 0, sw, 0, new int[]{0, 0x42ffffff, 0x1ffffaf0, 0}, new float[]{0, .44f, .56f, 1}, Shader.TileMode.CLAMP);
 }

 @Override public boolean setVisible(boolean visible, boolean restart) {
  boolean changed = super.setVisible(visible, restart);
  if (visible && ValueAnimator.areAnimatorsEnabled()) {
   if (sweep == null) {
    sweep = ValueAnimator.ofFloat(0f, 1f);
    sweep.setDuration(CYCLE_MS);
    sweep.setStartDelay(DELAY_MS);
    sweep.setRepeatCount(ValueAnimator.INFINITE);
    sweep.setInterpolator(new android.view.animation.LinearInterpolator());
    sweep.addUpdateListener(v -> {
     float p = (float) v.getAnimatedValue(), next = p < TRAVEL ? travel.getInterpolation(p / TRAVEL) : -1;
     if (next != band) { band = next; invalidateSelf(); }
    });
    sweep.start();
   }
  } else stopSweep();
  return changed;
 }

 void stopSweep() {
  if (sweep != null) { sweep.cancel(); sweep = null; }
  if (band != -1) { band = -1; invalidateSelf(); }
 }

 @Override public void draw(Canvas c) {
  Rect b = getBounds();
  if (b.isEmpty() || bands == null) return;
  int layer = c.saveLayerAlpha(b.left, b.top, b.right, b.bottom, Math.round(opacity * 255));
  // Opaque paint for the shaders: a shader is modulated by the paint's alpha, and the highlight lines below leave it at
  // .22, which would otherwise dim every frame after the first.
  paint.setColor(0xff000000);
  paint.setShader(bands); c.drawRect(b, paint);
  paint.setShader(glow); c.drawRect(b, paint);
  paint.setShader(null);
  // inset 0 1px 0 rgba(255,255,255,.6) and inset 1px 0 0 rgba(255,255,255,.22)
  float px = Math.max(1f, b.width() / 411f);
  paint.setColor(0x99ffffff); c.drawRect(b.left, b.top, b.right, b.top + px, paint);
  paint.setColor(0x38ffffff); c.drawRect(b.left, b.top, b.left + px, b.bottom, paint);
  if (band >= 0) {
   // translateX from -160% to 300% of the band's width, skewX(-22deg), top and bottom 25% beyond the screen.
   float sw = .42f * b.width(), x = b.left + (-1.6f + 4.6f * band) * sw;
   int save = c.save();
   // CSS skewX(-22deg) about the band's centre: x' = x + tan(-22deg) * y.
   float bh = 1.5f * b.height();
   c.translate(x + sw / 2, b.top - .25f * b.height() + bh / 2);
   c.skew((float) Math.tan(Math.toRadians(-22)), 0);
   c.translate(-sw / 2, -bh / 2);
   paint.setColor(0xff000000);
   paint.setShader(sweepBand);
   c.drawRect(0, 0, sw, bh, paint);
   paint.setShader(null);
   c.restoreToCount(save);
  }
  c.restoreToCount(layer);
 }

 @Override public void setAlpha(int alpha) {}
 @Override public void setColorFilter(ColorFilter filter) {}
 @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
