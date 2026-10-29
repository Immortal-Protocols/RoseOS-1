package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * The fixed Home tray from the HiFi brief (OS-HIFI-HOME-001): Rose and overview controls that stay put
 * while the pages swipe beneath. Rose only navigates to the conversation (never starts, mutes or ends voice)
 * and carries the quiet session mark from the authoritative ConversationState. There is no permitted
 * overview route for a non-system Home yet, so that control is shown disabled and says so; it never opens
 * an unrelated picker. The animated Rose renderer is a later Design handoff; this is the static mark.
 */
final class HomeTray extends LinearLayout {
 static final int HEIGHT_DP = 84, GAP_DP = 14, SIDE_DP = 16;
 final View presence, overview;
 final TextView sessionMark;
 HomeSessionMark.Mark shownMark;

 HomeTray(MainActivity activity, Runnable openRose) {
  super(activity);
  setOrientation(HORIZONTAL);
  setGravity(Gravity.CENTER_VERTICAL);
  setPadding(activity.dp(18), activity.dp(9), activity.dp(18), activity.dp(9));
  GradientDrawable metal = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
    new int[]{0xfff6f7f9, 0xffe3e7eb, 0xffcfd5db, 0xffe8ebee});
  metal.setCornerRadius(activity.dp(22));
  metal.setStroke(Math.max(1, activity.dp(1)), 0x66ffffff);
  setBackground(metal);
  setElevation(activity.dp(10));

  FrameLayout rose = new FrameLayout(activity);
  presence = RoseHomeView.presence(activity, openRose);
  rose.addView(presence, new FrameLayout.LayoutParams(activity.dp(66), activity.dp(66), Gravity.CENTER));
  sessionMark = activity.text("", 12, 0xff283e39);
  sessionMark.setGravity(Gravity.CENTER);
  sessionMark.setIncludeFontPadding(false);
  GradientDrawable markBack = new GradientDrawable();
  markBack.setShape(GradientDrawable.OVAL);
  markBack.setColor(0xf2fbfcf5);
  markBack.setStroke(Math.max(1, activity.dp(1)), 0xffe0e6d8);
  sessionMark.setBackground(markBack);
  sessionMark.setElevation(activity.dp(8));
  sessionMark.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  sessionMark.setTranslationX(activity.dp(26));
  sessionMark.setTranslationY(-activity.dp(26));
  sessionMark.setVisibility(GONE);
  rose.addView(sessionMark, new FrameLayout.LayoutParams(activity.dp(24), activity.dp(24), Gravity.CENTER));
  addView(rose, new LayoutParams(0, -1, 1));

  overview = new OverviewGlyph(activity);
  overview.setEnabled(false);
  overview.setAlpha(.5f);
  overview.setFocusable(true);
  overview.setContentDescription("Overview, not available yet");
  FrameLayout overviewSlot = new FrameLayout(activity);
  overviewSlot.addView(overview, new FrameLayout.LayoutParams(activity.dp(66), activity.dp(66), Gravity.CENTER));
  addView(overviewSlot, new LayoutParams(0, -1, 1));
 }

 /** The session from the authoritative state: microphone on, muted or connecting; nothing otherwise. */
 void showSession(ConversationState state) {
  HomeSessionMark.Mark mark = HomeSessionMark.of(state);
  if (mark == shownMark) return;
  shownMark = mark;
  switch (mark) {
   case LISTENING: sessionMark.setText("🎙"); break;
   case MUTED: sessionMark.setText("🔇"); break;
   case CONNECTING: sessionMark.setText("◌"); break;
   default: sessionMark.setText(""); break;
  }
  sessionMark.setVisibility(mark == HomeSessionMark.Mark.NONE ? GONE : VISIBLE);
  String spoken = HomeSessionMark.description(mark);
  presence.setContentDescription(spoken.isEmpty() ? "Open Rose" : "Open Rose. " + spoken);
 }

 /** The approved overview symbol: three separate rounded vertical rectangles, the last one accented. */
 static final class OverviewGlyph extends View {
  final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), edge = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF bar = new RectF();
  final float unit;

  OverviewGlyph(MainActivity activity) {
   super(activity);
   unit = activity.dp(1);
   edge.setStyle(Paint.Style.STROKE);
   edge.setStrokeWidth(unit);
   GradientDrawable well = new GradientDrawable();
   well.setShape(GradientDrawable.OVAL);
   well.setColor(0xccffffff);
   well.setStroke(Math.max(1, activity.dp(1)), 0x80748aa8);
   setBackground(well);
  }

  @Override protected void onDraw(Canvas canvas) {
   float width = 11 * unit, gap = 4 * unit, total = 3 * width + 2 * gap;
   float left = (getWidth() - total) / 2, middle = getHeight() / 2f;
   for (int i = 0; i < 3; i++) {
    float half = (i == 1 ? 17 : 15) * unit;
    bar.set(left + i * (width + gap), middle - half, left + i * (width + gap) + width, middle + half);
    fill.setColor(i == 2 ? 0xff2f6be6 : 0xfff2f5f9);
    edge.setColor(i == 2 ? 0x992f6be6 : 0x8c788caa);
    canvas.drawRoundRect(bar, 5 * unit, 5 * unit, fill);
    canvas.drawRoundRect(bar, 5 * unit, 5 * unit, edge);
   }
  }
 }
}
