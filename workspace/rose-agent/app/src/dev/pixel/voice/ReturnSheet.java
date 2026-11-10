package dev.pixel.voice;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 1C, V3: when the source app is missing or replaced, or the transfer failed, Rose stays and offers Stay in Rose,
 * Open switcher or Go Home, with no relaunch. An overlay inside Rose (V3: the backdrop stops above the system region),
 * so Rose's conversation, work and microphone carry on underneath.
 *
 * <p>V3 tokens, in dp/sp for its CSS px: backdrop #14070d/a8; the sheet a 140° gradient #56313b to #2b1924 with a 1dp
 * #c39a85/77 edge, 14dp radius, 22dp padding, 12dp from the edges; title 21sp medium, body 12sp #ddc7ba at 1.6 line
 * height; Stay in Rose primary (#e7c7b4, ink #30151d), the other two buttons #302327 with a #75595a edge, 7dp radius,
 * 12sp, 48dp targets.
 */
final class ReturnSheet extends FrameLayout {
 static final String TAG = "return-sheet";
 interface Choice { void chosen(String which); }

 final TextView title, body, stay, switcher, home;
 final float density;

 ReturnSheet(Context context, Choice choice) {
  super(context);
  density = context.getResources().getDisplayMetrics().density;
  setTag(TAG);
  setBackgroundColor(0xa814070d);
  setClickable(true); // the backdrop takes touches: Rose stays put underneath until a choice
  setVisibility(GONE);
  LinearLayout sheet = new LinearLayout(context);
  sheet.setOrientation(LinearLayout.VERTICAL);
  GradientDrawable g = new GradientDrawable(GradientDrawable.Orientation.TL_BR, new int[]{0xff56313b, 0xff2b1924});
  g.setCornerRadius(dp(14));
  g.setStroke(Math.max(1, dp(1)), 0x77c39a85);
  sheet.setBackground(g);
  sheet.setPadding(dp(22), dp(22), dp(22), dp(22));
  sheet.setElevation(dp(12));
  title = text(21, 0xfff2e6d9, "sans-serif-medium");
  title.setText(ReturnModel.SHEET_TITLE);
  title.setAccessibilityHeading(true);
  title.setLetterSpacing(-0.5f / 21f);
  sheet.addView(title);
  body = text(12, 0xffddc7ba, "sans-serif");
  body.setText(ReturnModel.SHEET_BODY);
  body.setLineSpacing(0, 1.6f);
  LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(-1, -2);
  bp.topMargin = dp(12);
  sheet.addView(body, bp);
  stay = button(ReturnModel.STAY, true, () -> choice.chosen(ReturnModel.STAY));
  LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-1, -2);
  sp.topMargin = dp(16);
  sheet.addView(stay, sp);
  LinearLayout row = new LinearLayout(context);
  row.setOrientation(LinearLayout.HORIZONTAL);
  switcher = button(ReturnModel.SWITCHER, false, () -> choice.chosen(ReturnModel.SWITCHER));
  home = button(ReturnModel.HOME, false, () -> choice.chosen(ReturnModel.HOME));
  row.addView(switcher, new LinearLayout.LayoutParams(0, -2, 1f));
  LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(0, -2, 1f);
  hp.leftMargin = dp(8);
  row.addView(home, hp);
  LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(-1, -2);
  rp.topMargin = dp(8);
  sheet.addView(row, rp);
  FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
  lp.leftMargin = lp.rightMargin = lp.bottomMargin = dp(12);
  addView(sheet, lp);
 }

 int dp(float v) { return Math.round(v * density); }

 TextView text(int sp, int color, String family) {
  TextView t = new TextView(getContext());
  t.setTextSize(sp);
  t.setTextColor(color);
  t.setTypeface(Typeface.create(family, Typeface.NORMAL));
  return t;
 }

 TextView button(String label, boolean primary, Runnable action) {
  TextView b = text(12, primary ? 0xff30151d : 0xfff2e6d9, primary ? "sans-serif-medium" : "sans-serif");
  b.setText(label);
  b.setGravity(Gravity.CENTER);
  b.setMinHeight(dp(48));
  StateListDrawable states = new StateListDrawable();
  states.addState(new int[]{android.R.attr.state_pressed}, fill(primary ? 0xfff3dccd : 0xff3f2f34, primary ? 0xfff3dccd : 0xff8b6d6e));
  states.addState(new int[]{}, fill(primary ? 0xffe7c7b4 : 0xff302327, primary ? 0xffe7c7b4 : 0xff75595a));
  b.setBackground(states);
  b.setPadding(dp(14), dp(10), dp(14), dp(10));
  b.setClickable(true);
  b.setFocusable(true);
  b.setForeground(HomeAppTile.focusRing(density, 7));
  b.setOnClickListener(v -> action.run());
  return b;
 }

 GradientDrawable fill(int color, int edge) {
  GradientDrawable d = new GradientDrawable();
  d.setColor(color);
  d.setCornerRadius(dp(7));
  d.setStroke(Math.max(1, dp(1)), edge);
  return d;
 }

 void show(boolean shown) {
  boolean was = getVisibility() == VISIBLE;
  setVisibility(shown ? VISIBLE : GONE);
  if (shown && !was) {
   stay.requestFocus();
   announceForAccessibility(ReturnModel.SHEET_TITLE + " " + ReturnModel.SHEET_BODY);
  }
 }
}
