package dev.pixel.voice;

import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import java.util.function.BooleanSupplier;

/**
 * The fixed Home tray (OS-HIFI-HOME-001; #157: Nathan's selected Home image): a brushed silver bar, 92dp high
 * with small corners, holding two 112x80dp oval glass wells, Rose and overview, centred about a quarter of the way
 * in from each end, that stay put while the pages swipe beneath. Rose only navigates to the conversation (never
 * starts, mutes or ends voice). Each Home refresh hands it the RoseVisualState read from the voice service
 * (HomeRose), and applyVisual passes it to App's RosePresenceView (show(RoseVisualState)), the one shared
 * renderer; the renderer is excluded from accessibility and the well speaks its state, so there is one focus
 * target. The overview well asks for the real system task overview through HomeOverview's one permitted route
 * (OS-OVERVIEW-156-M001); each refresh shows whether that route is available, and a tap while it is not says
 * so and opens nothing else, never an unrelated picker.
 */
final class HomeTray extends LinearLayout {
 static final int HEIGHT_DP = 92, GAP_DP = 16, SIDE_DP = 24, WELL_W_DP = 112, WELL_H_DP = 80;
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
  // #157: the image's wells are centred 24.5% and 75.5% along the bar; on a 363dp bar that puts the 112dp glass about
  // 33dp in from each end. Each well view adds its pocket around the glass.
  setPadding(activity.dp(33 - TrayMaterial.POCKET_DP), 0, activity.dp(33 - TrayMaterial.POCKET_DP), 0);
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

 /** The approved overview symbol: three separate rounded vertical rectangles, the last one accented blue
  * (TrayMaterial.Overview); decorative, the well speaks for it. */
 static final class OverviewGlyph extends View {
  OverviewGlyph(MainActivity activity) {
   super(activity);
   setBackground(new TrayMaterial.Overview(activity.getResources().getDisplayMetrics().density));
   setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  }
 }
}
