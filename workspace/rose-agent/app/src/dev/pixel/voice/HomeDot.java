package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * One Home page dot of the selected HiFi (prototypes/rose-hifi at ce126f56, style.css .page-dots button i): an 8dp
 * white circle at 45% with a fine rgba(30,60,100,.15) ring, and for the current page (aria-current, here the view's
 * selected state) a 9dp solid white circle in a soft white glow (0 0 6px rgba(255,255,255,.8)). Drawn centred in
 * its 44dp touch target; the view keeps the click and the spoken page name.
 */
final class HomeDot extends Drawable {
 static final int DOT_DP = 8, CURRENT_DP = 9, GLOW_DP = 6;
 static final int IDLE = 0x73ffffff, RING = 0x261e3c64, CURRENT = 0xffffffff, GLOW = 0xccffffff;

 final float density;
 final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), ring = new Paint(Paint.ANTI_ALIAS_FLAG),
   glow = new Paint(Paint.ANTI_ALIAS_FLAG);
 boolean current;

 HomeDot(float density) {
  this.density = density;
  ring.setStyle(Paint.Style.STROKE);
  ring.setStrokeWidth(Math.max(1f, density));
  ring.setColor(RING);
 }

 @Override protected void onBoundsChange(android.graphics.Rect bounds) {
  float r = CURRENT_DP * density / 2, outer = r + GLOW_DP * density;
  glow.setShader(new RadialGradient(bounds.exactCenterX(), bounds.exactCenterY(), outer,
    new int[]{GLOW, GLOW, 0x00ffffff}, new float[]{0f, r / outer, 1f}, Shader.TileMode.CLAMP));
 }

 @Override public void draw(Canvas canvas) {
  float cx = getBounds().exactCenterX(), cy = getBounds().exactCenterY();
  if (current) {
   float r = CURRENT_DP * density / 2, outer = r + GLOW_DP * density;
   canvas.drawCircle(cx, cy, outer, glow);
   fill.setColor(CURRENT);
   canvas.drawCircle(cx, cy, r, fill);
  } else {
   float r = DOT_DP * density / 2;
   fill.setColor(IDLE);
   canvas.drawCircle(cx, cy, r, fill);
   canvas.drawCircle(cx, cy, r + ring.getStrokeWidth() / 2, ring);
  }
 }

 @Override public boolean isStateful() { return true; }

 @Override protected boolean onStateChange(int[] state) {
  boolean now = false;
  for (int s : state) if (s == android.R.attr.state_selected) now = true;
  if (now == current) return false;
  current = now;
  invalidateSelf();
  return true;
 }

 @Override public void setAlpha(int alpha) {}
 @Override public void setColorFilter(ColorFilter filter) {}
 @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
