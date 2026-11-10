package dev.pixel.voice;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 1C: the compact named return control (V3) that sits at the bottom left of Rose's content, above the chin. It draws
 * one ReturnModel.State and reports taps; RoseConversationActivity owns the state, the timing and the source.
 *
 * <p>PROVISIONAL STYLING: Designer's V3 handoff (Library version 3, SHA-256 798c9a30…) is being resolved. Until it
 * lands, the control uses the conversation's existing Oxblood tokens: the task-action surface (dark glass, a 1dp lit
 * hairline), ink and secondary ink, a back chevron, the app's own icon when Android has one, and a 48dp touch target
 * around a 40dp pill. Geometry, colours and motion follow the handoff once it arrives.
 */
final class ReturnControl extends LinearLayout {
 static final String TAG = "return-control";
 static final int HEIGHT_DP = 48, PILL_DP = 40, ICON_DP = 18, FADE_MS = 160;

 final TextView chevron, label;
 final ImageView icon;
 final GradientDrawable pill;
 final float density;
 ReturnModel.Phase shownPhase = ReturnModel.Phase.HIDDEN;

 ReturnControl(Context context) {
  super(context);
  density = context.getResources().getDisplayMetrics().density;
  setTag(TAG);
  setOrientation(HORIZONTAL);
  setGravity(Gravity.CENTER_VERTICAL);
  setMinimumHeight(dp(HEIGHT_DP));
  setClickable(true);
  setFocusable(true);
  setVisibility(GONE);
  pill = new GradientDrawable();
  pill.setCornerRadius(dp(PILL_DP) / 2f);
  pill.setColor(0x8c0e080b);
  pill.setStroke(Math.max(1, dp(1)), OxbloodRose.LINE);
  // The pill is drawn 4dp in from the 48dp target, top and bottom.
  setBackground(new android.graphics.drawable.InsetDrawable(pill, 0, dp(4), 0, dp(4)));
  // After the background: an InsetDrawable's own padding would otherwise replace the view's.
  setPadding(dp(14), dp(4), dp(16), dp(4));
  setForeground(HomeAppTile.focusRing(density, PILL_DP / 2));
  chevron = text(17, OxbloodRose.INK, "sans-serif");
  chevron.setText("‹");
  addView(chevron, new LayoutParams(-2, -2));
  icon = new ImageView(context);
  icon.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  icon.setScaleType(ImageView.ScaleType.FIT_CENTER);
  LayoutParams ip = new LayoutParams(dp(ICON_DP), dp(ICON_DP));
  ip.leftMargin = dp(8);
  addView(icon, ip);
  label = text(14, OxbloodRose.INK, "sans-serif-medium");
  label.setSingleLine(true);
  label.setEllipsize(TextUtils.TruncateAt.END);
  LayoutParams lp = new LayoutParams(-2, -2);
  lp.leftMargin = dp(8);
  addView(label, lp);
  setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
 }

 TextView text(int sp, int color, String family) {
  TextView t = new TextView(getContext());
  t.setTextSize(sp);
  t.setTextColor(color);
  t.setTypeface(Typeface.create(family, Typeface.NORMAL));
  t.setIncludeFontPadding(false);
  t.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  return t;
 }

 int dp(float v) { return Math.round(v * density); }

 /** Draws the state. appIcon may be null (no icon is shown, never a placeholder). reducedMotion: no fade. */
 void show(ReturnModel.State s, Drawable appIcon, boolean reducedMotion) {
  boolean wasVisible = getVisibility() == VISIBLE;
  if (!s.visible()) {
   shownPhase = s.phase;
   if (!wasVisible) return;
   animate().cancel();
   if (reducedMotion) { setVisibility(GONE); setAlpha(1f); return; }
   animate().alpha(0f).setDuration(FADE_MS).withEndAction(() -> { setVisibility(GONE); setAlpha(1f); }).start();
   return;
  }
  String t = ReturnModel.text(s);
  if (!t.contentEquals(label.getText())) label.setText(t);
  setContentDescription(ReturnModel.spoken(s));
  boolean unavailable = s.phase == ReturnModel.Phase.UNAVAILABLE;
  chevron.setVisibility(unavailable ? GONE : VISIBLE);
  icon.setImageDrawable(appIcon);
  icon.setVisibility(appIcon == null || unavailable ? GONE : VISIBLE);
  label.setTextColor(unavailable || s.phase == ReturnModel.Phase.RETURNING ? OxbloodRose.INK_2 : OxbloodRose.INK);
  setEnabled(s.actionable());
  setClickable(s.actionable());
  setFocusable(s.actionable());
  setStateDescription(s.phase == ReturnModel.Phase.PENDING || s.phase == ReturnModel.Phase.RETURNING ? "Returning" : null);
  shownPhase = s.phase;
  if (!wasVisible) {
   animate().cancel();
   setVisibility(VISIBLE);
   if (reducedMotion) { setAlpha(1f); setTranslationY(0); return; }
   setAlpha(0f);
   setTranslationY(dp(4));
   animate().alpha(1f).translationY(0).setDuration(FADE_MS).start();
  }
 }
}
