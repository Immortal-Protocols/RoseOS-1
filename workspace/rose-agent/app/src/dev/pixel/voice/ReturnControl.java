package dev.pixel.voice;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.InsetDrawable;
import android.graphics.drawable.LayerDrawable;
import android.graphics.drawable.StateListDrawable;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 1C: the approved V3 return control (Designer's F11/RETURN-1N study, Rose-F11-Return.html SHA-256 798c9a30…955ae5),
 * at the leading edge of Rose's content footer above the chin. It draws one ReturnModel.State; RoseConversationActivity
 * owns the state and the source.
 *
 * <p>V3 styling, in dp/sp for its CSS px: a 36dp surface inside a 48dp hit area (6dp in, top and bottom), 12dp side
 * padding, an 8dp gap, a 14dp back chevron (stroke 1.4 over a 16 grid) and the source name at 12sp in #e9cfbf. The
 * surface: a 150° gradient #49202c/bb to #29111d/cc, a 1dp #b1737c/80 hairline with a lighter #d4a0a5/90 top edge,
 * 7dp radius; pressed #633040 with a #e1b0b2 edge, without moving. Pending reads "Returning…" in #cdb4a6 with a
 * separate Cancel (12sp #c9b3a6, 48dp target). No app icon: V3 invents none.
 */
final class ReturnControl extends LinearLayout {
 static final String TAG = "return-control", CANCEL_TAG = "return-cancel";
 static final int HEIGHT_DP = 48, SURFACE_DP = 36, RADIUS_DP = 7, CHEVRON_DP = 14;
 static final int INK = 0xffe9cfbf, PENDING_INK = 0xffcdb4a6, CANCEL_INK = 0xffc9b3a6;

 final LinearLayout button;
 final TextView label, cancel;
 final View chevron;
 final float density;

 ReturnControl(Context context) {
  super(context);
  density = context.getResources().getDisplayMetrics().density;
  setOrientation(HORIZONTAL);
  setGravity(Gravity.CENTER_VERTICAL);
  setVisibility(GONE);
  button = new LinearLayout(context);
  button.setTag(TAG);
  button.setOrientation(HORIZONTAL);
  button.setGravity(Gravity.CENTER_VERTICAL);
  button.setMinimumHeight(dp(HEIGHT_DP));
  button.setClickable(true);
  button.setFocusable(true);
  button.setBackground(surface());
  // After the background: an InsetDrawable's own padding would otherwise replace the view's.
  button.setPadding(dp(12), dp(6), dp(12), dp(6));
  button.setForeground(HomeAppTile.focusRing(density, RADIUS_DP));
  button.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
  chevron = new View(context);
  chevron.setBackground(new Chevron(density));
  chevron.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  button.addView(chevron, new LayoutParams(dp(CHEVRON_DP), dp(CHEVRON_DP)));
  label = new TextView(context);
  label.setTextSize(12);
  label.setTextColor(INK);
  label.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
  label.setIncludeFontPadding(false);
  label.setSingleLine(true);
  label.setEllipsize(TextUtils.TruncateAt.END);
  label.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  LayoutParams lp = new LayoutParams(-2, -2);
  lp.leftMargin = dp(8);
  button.addView(label, lp);
  // V3: sized to its content (inline-flex), never wider than the footer; a long name truncates inside it.
  addView(button, new LayoutParams(-2, -2));
  cancel = new TextView(context);
  cancel.setTag(CANCEL_TAG);
  cancel.setText(ReturnModel.CANCEL);
  cancel.setTextSize(12);
  cancel.setTextColor(CANCEL_INK);
  cancel.setGravity(Gravity.CENTER);
  cancel.setMinHeight(dp(HEIGHT_DP));
  cancel.setMinWidth(dp(HEIGHT_DP));
  cancel.setClickable(true);
  cancel.setFocusable(true);
  StateListDrawable cb = new StateListDrawable();
  GradientDrawable cp = new GradientDrawable();
  cp.setCornerRadius(dp(RADIUS_DP));
  cp.setColor(0x335c2a39);
  cb.addState(new int[]{android.R.attr.state_pressed}, cp);
  cancel.setBackground(cb);
  cancel.setPadding(dp(8), dp(6), dp(8), dp(6));
  cancel.setContentDescription("Cancel return");
  cancel.setVisibility(GONE);
  LayoutParams cl = new LayoutParams(-2, -2);
  cl.leftMargin = dp(8);
  addView(cancel, cl);
 }

 int dp(float v) { return Math.round(v * density); }

 /** V3's surface, drawn 6dp in from the 48dp target, top and bottom; pressed changes colour only. */
 Drawable surface() {
  StateListDrawable s = new StateListDrawable();
  s.addState(new int[]{android.R.attr.state_pressed}, plate(new int[]{0xff633040, 0xff633040}, 0xffe1b0b2, 0xffe1b0b2));
  s.addState(new int[]{}, plate(new int[]{0xbb49202c, 0xcc29111d}, 0x80b1737c, 0x90d4a0a5));
  return new InsetDrawable(s, 0, dp(6), 0, dp(6));
 }

 Drawable plate(int[] fill, int edge, int topEdge) {
  GradientDrawable body = new GradientDrawable(GradientDrawable.Orientation.TL_BR, fill);
  body.setCornerRadius(dp(RADIUS_DP));
  body.setStroke(Math.max(1, dp(1)), edge);
  // The lighter top edge (V3 border-top-color) over the hairline, between the rounded corners.
  GradientDrawable top = new GradientDrawable();
  top.setColor(topEdge);
  LayerDrawable layers = new LayerDrawable(new Drawable[]{body, top});
  layers.setLayerHeight(1, Math.max(1, dp(1)));
  layers.setLayerGravity(1, Gravity.TOP | Gravity.FILL_HORIZONTAL);
  layers.setLayerInset(1, dp(RADIUS_DP), 0, dp(RADIUS_DP), 0);
  return layers;
 }

 /** Draws the state; hidden unless there is a source to return to (or a request pending). */
 void show(ReturnModel.State s) {
  if (!s.controlShown()) { setVisibility(GONE); return; }
  setVisibility(VISIBLE);
  String t = ReturnModel.text(s);
  if (!t.contentEquals(label.getText())) label.setText(t);
  label.setTextColor(s.pending() ? PENDING_INK : INK);
  ((Chevron) chevron.getBackground()).setColor(s.pending() ? PENDING_INK : INK);
  button.setContentDescription(ReturnModel.spoken(s));
  button.setStateDescription(s.pending() ? "Returning" : null);
  button.setTooltipText(ReturnModel.HELP);
  // Pending: duplicate requests are ignored (the tap does nothing) but the control stays focusable and announced.
  button.setEnabled(!s.pending());
  cancel.setVisibility(s.pending() ? VISIBLE : GONE);
 }

 /** V3's back chevron: the path m10 3 -5 5 5 5 on a 16 grid, a 1.4 round stroke in the label's colour. */
 static final class Chevron extends Drawable {
  final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  Chevron(float density) {
   paint.setStyle(Paint.Style.STROKE);
   paint.setStrokeCap(Paint.Cap.ROUND);
   paint.setStrokeJoin(Paint.Join.ROUND);
   paint.setColor(INK);
   this.density = density;
  }
  final float density;
  void setColor(int c) { if (paint.getColor() != c) { paint.setColor(c); invalidateSelf(); } }
  @Override public void draw(Canvas canvas) {
   float w = getBounds().width(), u = w / 16f;
   paint.setStrokeWidth(1.4f * u);
   Path p = new Path();
   p.moveTo(getBounds().left + 10 * u, getBounds().top + 3 * u);
   p.lineTo(getBounds().left + 5 * u, getBounds().top + 8 * u);
   p.lineTo(getBounds().left + 10 * u, getBounds().top + 13 * u);
   canvas.drawPath(p, paint);
  }
  @Override public void setAlpha(int a) { paint.setAlpha(a); }
  @Override public void setColorFilter(ColorFilter f) { paint.setColorFilter(f); }
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }
}
