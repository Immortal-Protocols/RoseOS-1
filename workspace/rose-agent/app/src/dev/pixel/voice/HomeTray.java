package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;

/**
 * The fixed Home tray of the selected HiFi (prototypes/rose-hifi at ce126f56, OS-HIFI-HOME-001): a brushed
 * light-metal bar, 92dp high with 22dp corners, holding two 104x66dp pill wells at its ends, Rose and
 * overview, that stay put while the pages swipe beneath. Rose only navigates to the conversation (never
 * starts, mutes or ends voice). Each Home refresh hands it the RoseVisualState read from the voice service
 * (HomeRose), and applyVisual passes it to App's RosePresenceView (show(RoseVisualState)), the one shared
 * renderer; the renderer is excluded from accessibility and the well speaks its state, so there is one focus
 * target. There is no permitted overview route for a non-system Home yet, so that well is disabled and
 * says so; it never opens an unrelated picker.
 */
final class HomeTray extends LinearLayout {
 static final int HEIGHT_DP = 92, GAP_DP = 16, SIDE_DP = 24, WELL_W_DP = 104, WELL_H_DP = 66;
 final View presence, overview;
 final RosePresenceView roseView;
 /** The last visual state applied, and the conversation it came from; null before the first refresh. */
 RoseVisualState shownVisual;
 ConversationState shownConversation;
 boolean released;

 HomeTray(MainActivity activity, Runnable openRose) {
  super(activity);
  setOrientation(HORIZONTAL);
  setGravity(Gravity.CENTER_VERTICAL);
  setPadding(activity.dp(22), 0, activity.dp(22), 0);
  GradientDrawable metal = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
    new int[]{0xfff6f7f9, 0xffe3e7eb, 0xffcfd5db, 0xffe8ebee});
  metal.setCornerRadius(activity.dp(22));
  metal.setStroke(Math.max(1, activity.dp(1)), 0x80ffffff);
  setBackground(metal);
  setElevation(activity.dp(12));

  FrameLayout rose = well(activity);
  // App's renderer draws the state; the well is the one focusable "Open Rose" action and speaks that state.
  roseView = new RosePresenceView(activity);
  roseView.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  rose.addView(roseView, new FrameLayout.LayoutParams(activity.dp(64), activity.dp(64), Gravity.CENTER));
  rose.setClickable(true);
  rose.setFocusable(true);
  rose.setContentDescription("Open Rose");
  rose.setOnClickListener(v -> openRose.run());
  presence = rose;
  addView(rose, new LayoutParams(activity.dp(WELL_W_DP), activity.dp(WELL_H_DP)));

  addView(new View(activity), new LayoutParams(0, 1, 1));

  FrameLayout overviewWell = well(activity);
  overviewWell.addView(new OverviewGlyph(activity), new FrameLayout.LayoutParams(-1, -1));
  overviewWell.setEnabled(false);
  overviewWell.setFocusable(true);
  overviewWell.setAlpha(.55f);
  overviewWell.setContentDescription("Overview, not available yet");
  overview = overviewWell;
  addView(overviewWell, new LayoutParams(activity.dp(WELL_W_DP), activity.dp(WELL_H_DP)));
 }

 /** A recessed pill well: white highlight falling to cool silver, a fine edge and a soft drop. */
 static FrameLayout well(MainActivity activity) {
  FrameLayout well = new FrameLayout(activity);
  GradientDrawable face = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
    new int[]{0xfffdfeff, 0xeaf0f5fb, 0xd9d6e2f0, 0xe6bccde0});
  face.setCornerRadius(activity.dp(WELL_H_DP) / 2f);
  face.setStroke(Math.max(1, activity.dp(1)), 0x526e829e);
  well.setBackground(face);
  well.setElevation(activity.dp(3));
  return well;
 }

 /** One refresh: the session mark and spoken name from the conversation, the presence from the visual state. */
 void show(ConversationState conversation, RoseVisualState visual) {
  if (released) return;
  shownConversation = conversation;
  applyVisual(visual);
 }

 /** The tray is leaving (frame rebuilt, activity destroyed): stop animating and accept no further frames. */
 void release() {
  if (released) return;
  if (shownConversation != null) applyVisual(RoseVisualState.from(shownConversation, Float.NaN, Float.NaN, true, false));
  released = true;
 }

 /** The single mount point for App's RosePresenceView: it renders the state, and the well speaks it. */
 void applyVisual(RoseVisualState visual) {
  shownVisual = visual;
  roseView.show(visual);
  String spoken = "Open Rose. " + RosePresenceModel.describe(visual);
  if (!spoken.contentEquals(presence.getContentDescription())) presence.setContentDescription(spoken);
 }

 /** The approved overview symbol: three separate rounded vertical rectangles, the last one accented blue. */
 static final class OverviewGlyph extends View {
  final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), edge = new Paint(Paint.ANTI_ALIAS_FLAG);
  final RectF bar = new RectF();
  final float unit;

  OverviewGlyph(MainActivity activity) {
   super(activity);
   unit = activity.dp(1);
   edge.setStyle(Paint.Style.STROKE);
   edge.setStrokeWidth(unit);
   setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
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
