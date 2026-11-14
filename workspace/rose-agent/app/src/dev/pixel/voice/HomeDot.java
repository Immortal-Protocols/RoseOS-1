package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * One Home page dot of the committed Oxblood Rose HiFi (#410; main f6503a0, oxblood.css:364-367,514-515;
 * qa/oxblood/oxblood-contract.json "homeGrid.dots"): a 7dp warm grey dot at 45%, and for the current page (the view's
 * selected state) a 7.5dp ivory dot #f4ede0 in a soft warm glow (0 0 4px rgba(255,235,220,.45)). Indicator only, as in
 * the reference: paging is by swipe, and the pager itself offers scroll actions and names the page for accessibility.
 */
final class HomeDot extends Drawable {
 static final float DOT_DP = 7, CURRENT_DP = 7.5f, GLOW_DP = 4;
 static final int IDLE = 0x73c8beba, CURRENT = 0xfff4ede0, GLOW = 0x73ffebdc;

 final float density;
 final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), glow = new Paint(Paint.ANTI_ALIAS_FLAG);
 boolean current;
 /** Project 2: the workspace's mark, a four-point star before Home's round dots (the approved HiFi r2). It is lit
  * on the workspace; on any other page it is outlined and unlit. */
 final boolean star;
 static final float STAR_DP = 10, STAR_INNER = .36f, STAR_STROKE_DP = 1.2f;
 final android.graphics.Path starPath = new android.graphics.Path();

 HomeDot(float density) {
  this(density, false);
 }

 HomeDot(float density, boolean star) {
  this.density = density;
  this.star = star;
 }

 /** Plan 1E: the theme the glow was built in; the glow is rebuilt when the theme changes. */
 String glowTheme;

 @Override protected void onBoundsChange(android.graphics.Rect bounds) {
  glowTheme = RoseThemeNow.id;
  int glowColor = RoseThemeNow.c(GLOW);
  float r = CURRENT_DP * density / 2, outer = r + GLOW_DP * density;
  glow.setShader(new RadialGradient(bounds.exactCenterX(), bounds.exactCenterY(), outer,
    new int[]{glowColor, glowColor, glowColor & 0x00ffffff}, new float[]{0f, r / outer, 1f}, Shader.TileMode.CLAMP));
  if (star) {
   starPath.reset();
   float cx = bounds.exactCenterX(), cy = bounds.exactCenterY(), outerStar = STAR_DP * density / 2, inner = outerStar * STAR_INNER;
   for (int k = 0; k < 8; k++) {
    double a = Math.PI / 4 * k - Math.PI / 2; float rad = k % 2 == 0 ? outerStar : inner;
    float x = cx + (float) (Math.cos(a) * rad), y = cy + (float) (Math.sin(a) * rad);
    if (k == 0) starPath.moveTo(x, y); else starPath.lineTo(x, y);
   }
   starPath.close();
  }
 }

 @Override public void draw(Canvas canvas) {
  float cx = getBounds().exactCenterX(), cy = getBounds().exactCenterY();
  if (!RoseThemeNow.id.equals(glowTheme)) onBoundsChange(getBounds());
  if (star) {
   if (current) {
    canvas.drawCircle(cx, cy, (CURRENT_DP / 2 + GLOW_DP) * density, glow);
    fill.setStyle(Paint.Style.FILL); fill.setColor(RoseThemeNow.c(CURRENT));
   } else {
    fill.setStyle(Paint.Style.STROKE); fill.setStrokeWidth(STAR_STROKE_DP * density); fill.setColor(RoseThemeNow.c(IDLE));
   }
   canvas.drawPath(starPath, fill);
   fill.setStyle(Paint.Style.FILL);
   return;
  }
  if (current) {
   float r = CURRENT_DP * density / 2, outer = r + GLOW_DP * density;
   canvas.drawCircle(cx, cy, outer, glow);
   fill.setColor(RoseThemeNow.c(CURRENT));
   canvas.drawCircle(cx, cy, r, fill);
  } else {
   fill.setColor(RoseThemeNow.c(IDLE));
   canvas.drawCircle(cx, cy, DOT_DP * density / 2, fill);
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
