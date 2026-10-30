package dev.pixel.voice;

import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import java.util.function.BooleanSupplier;

/**
 * The fixed Home tray of the selected HiFi (prototypes/rose-hifi at ce126f56, OS-HIFI-HOME-001): a brushed
 * light-metal bar, 92dp high with 22dp corners, holding two 104x66dp pill wells at its ends, Rose and
 * overview, that stay put while the pages swipe beneath. Rose only navigates to the conversation (never
 * starts, mutes or ends voice). Each Home refresh hands it the RoseVisualState read from the voice service
 * (HomeRose), and applyVisual passes it to App's RosePresenceView (show(RoseVisualState)), the one shared
 * renderer; the renderer is excluded from accessibility and the well speaks its state, so there is one focus
 * target. The overview well asks for the real system task overview through HomeOverview's one permitted route
 * (OS-OVERVIEW-156-M001); each refresh shows whether that route is available, and a tap while it is not says
 * so and opens nothing else, never an unrelated picker.
 */
final class HomeTray extends LinearLayout {
 static final int HEIGHT_DP = 92, GAP_DP = 16, SIDE_DP = 24, WELL_W_DP = 104, WELL_H_DP = 66;
 final View presence, overview;
 final RosePresenceView roseView;
 /** The last visual state applied, and the conversation it came from; null before the first refresh. */
 RoseVisualState shownVisual;
 ConversationState shownConversation;
 final BooleanSupplier overviewAvailable;
 boolean released, shownOverviewAvailable;

 HomeTray(MainActivity activity, Runnable openRose, Runnable openOverview, BooleanSupplier overviewAvailable) {
  super(activity);
  setOrientation(HORIZONTAL);
  setGravity(Gravity.CENTER_VERTICAL);
  // The wells' glass sits 22dp in from the ends (.tray.two padding); each well view adds its pocket around it.
  setPadding(activity.dp(22 - TrayMaterial.POCKET_DP), 0, activity.dp(22 - TrayMaterial.POCKET_DP), 0);
  setBackground(new TrayMaterial.Tray(activity.getResources().getDisplayMetrics().density));
  setElevation(activity.dp(12));
  // Rose at the reference size overhangs its well, as the HiFi canvas does; nothing here clips it.
  setClipChildren(false);

  FrameLayout rose = well(activity);
  // App's renderer draws the state; the well is the one focusable "Open Rose" action and speaks that state.
  roseView = new RosePresenceView(activity);
  roseView.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  int roseSize = activity.dp(RosePresenceModel.viewSizeFor(RosePresenceModel.TRAY_SPHERE_DP));
  rose.setClipChildren(false);
  rose.addView(roseView, new FrameLayout.LayoutParams(roseSize, roseSize, Gravity.CENTER));
  rose.setClickable(true);
  rose.setFocusable(true);
  rose.setContentDescription("Open Rose");
  rose.setOnClickListener(v -> openRose.run());
  presence = rose;
  addView(rose, new LayoutParams(activity.dp(WELL_W_DP + 2 * TrayMaterial.POCKET_DP), activity.dp(WELL_H_DP + 2 * TrayMaterial.POCKET_DP)));

  addView(new View(activity), new LayoutParams(0, 1, 1));

  FrameLayout overviewWell = well(activity);
  overviewWell.addView(new OverviewGlyph(activity), new FrameLayout.LayoutParams(-1, -1));
  overviewWell.setClickable(true);
  overviewWell.setFocusable(true);
  overviewWell.setOnClickListener(v -> openOverview.run());
  overview = overviewWell;
  this.overviewAvailable = overviewAvailable;
  applyOverview(overviewAvailable.getAsBoolean());
  addView(overviewWell, new LayoutParams(activity.dp(WELL_W_DP + 2 * TrayMaterial.POCKET_DP), activity.dp(WELL_H_DP + 2 * TrayMaterial.POCKET_DP)));
 }

 /** A recessed glass well in its pocket (TrayMaterial.Well): the WELL_W x WELL_H glass plus the pocket margin. */
 static FrameLayout well(MainActivity activity) {
  FrameLayout well = new FrameLayout(activity);
  well.setBackground(new TrayMaterial.Well(activity.getResources().getDisplayMetrics().density));
  return well;
 }

 /** One refresh: the session mark and spoken name from the conversation, the presence from the visual state. */
 void show(ConversationState conversation, RoseVisualState visual) {
  if (released) return;
  shownConversation = conversation;
  applyVisual(visual);
  boolean available = overviewAvailable.getAsBoolean();
  if (available != shownOverviewAvailable) applyOverview(available);
 }

 /** The overview well at full strength with its action when the route is available, dimmed with the reason when not. */
 void applyOverview(boolean available) {
  shownOverviewAvailable = available;
  overview.setAlpha(available ? 1f : .55f);
  overview.setContentDescription(HomeOverview.describe(available));
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
