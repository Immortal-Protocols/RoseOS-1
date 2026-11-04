package dev.pixel.voice;

import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import java.util.function.BooleanSupplier;

/**
 * Home's shallow edge (#410; the committed Oxblood Rose HiFi, main f6503a0, oxblood.css:371-380,487-496;
 * qa/oxblood/oxblood-contract.json "homeEdge"): the full-width burgundy plate (OxbloodEdge.Plate) at the bottom of
 * Home, 92dp from its concave top to the screen bottom, with two controls formed in it and centred 14dp apart in the
 * flat area: Rose (a 103x44dp control holding the ruby pearl) and the system switcher (106x44dp, two pairs of offset
 * task cards). It stays put while the pages swipe beneath.
 *
 * Rose only navigates to the conversation (never starts or ends voice). Between Rose and the switcher sits Rose's
 * microphone-input mute (RoseMicInput; #410 wave item 6, the HiFi's tray-mic): one state shared with the raised edge,
 * the conversation's mic and the call notification. It withholds only microphone input into Rose Agent; it is not an
 * OS-wide mute, never silences Rose's output or other apps, and never starts, ends or stops a call, task or session.
 * Each Home refresh hands the tray the conversation state and the mute as every surface shows it (the live call's own
 * mute during a call, otherwise the shared state); the pearl is silver while that mute is on, with no connection or
 * listening cue on Home, as in the reference. The switcher control asks for the real system task overview through
 * HomeOverview's one permitted route (OS-OVERVIEW-156-M001); each refresh shows whether that route is available, and a
 * tap while it is not says so and opens nothing else.
 */
final class HomeTray extends LinearLayout {
 static final int HEIGHT_DP = OxbloodEdge.HEIGHT_DP, ROSE_W_DP = OxbloodEdge.ROSE_W_DP, SWITCHER_W_DP = OxbloodEdge.SWITCHER_W_DP,
   CONTROL_H_DP = OxbloodEdge.CONTROL_H_DP, GAP_DP = OxbloodEdge.GAP_DP, PEARL_DP = OxbloodEdge.PEARL_DP;
 final View presence, overview, mic;
 final RosePearl pearl;
 final OxbloodEdge.MicGlyph micGlyph;
 /** The mute last shown (RoseMicInput.shown). */
 boolean shownMicMuted;
 /** The last visual state applied, and the conversation it came from; null before the first refresh. */
 RoseVisualState shownVisual;
 ConversationState shownConversation;
 final BooleanSupplier overviewAvailable;
 boolean released, shownOverviewAvailable;
 /** The extra height below the 92dp edge that the tappable navigation inset needs (three-button navigation). */
 int navigationExtra;
 final float density;

 HomeTray(MainActivity activity, Runnable openRose, Runnable toggleMic, Runnable openOverview, BooleanSupplier overviewAvailable) {
  super(activity);
  density = activity.getResources().getDisplayMetrics().density;
  setOrientation(HORIZONTAL);
  setGravity(Gravity.CENTER);
  setBackground(new OxbloodEdge.Plate(density));
  // Controls are centred in the flat area below the concave top (padding-top 34).
  setPadding(0, activity.dp(OxbloodEdge.FLAT_TOP_DP), 0, 0);
  setClipChildren(false);
  setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);

  FrameLayout rose = control(activity);
  pearl = new RosePearl(density, PEARL_DP);
  View pearlView = new View(activity);
  pearlView.setBackground(pearl);
  pearlView.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  int pearlBox = activity.dp(PEARL_DP + 12);
  rose.addView(pearlView, new FrameLayout.LayoutParams(pearlBox, pearlBox, Gravity.CENTER));
  rose.setContentDescription("Open Rose");
  rose.setOnClickListener(v -> openRose.run());
  presence = rose;
  addView(rose, new LayoutParams(activity.dp(ROSE_W_DP), activity.dp(OxbloodEdge.TARGET_H_DP)));

  FrameLayout micControl = control(activity);
  micGlyph = new OxbloodEdge.MicGlyph(density);
  micControl.setBackground(new android.graphics.drawable.LayerDrawable(new android.graphics.drawable.Drawable[]{OxbloodEdge.Well.mic(density), micGlyph}));
  micControl.setOnClickListener(v -> toggleMic.run());
  mic = micControl;
  applyMic(false);
  // The reference's 44dp slot 14dp from each neighbour, as a 48dp touch target about the same centre.
  int grow = (OxbloodEdge.MIC_TARGET_W_DP - OxbloodEdge.MIC_W_DP) / 2;
  LayoutParams micParams = new LayoutParams(activity.dp(OxbloodEdge.MIC_TARGET_W_DP), activity.dp(OxbloodEdge.TARGET_H_DP));
  micParams.leftMargin = activity.dp(GAP_DP - grow);
  addView(micControl, micParams);

  FrameLayout switcher = control(activity);
  View glyph = new View(activity);
  glyph.setBackground(new OxbloodEdge.SwitcherGlyph(density));
  glyph.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  switcher.addView(glyph, new FrameLayout.LayoutParams(activity.dp(OxbloodEdge.SwitcherGlyph.BOX_W_DP), activity.dp(OxbloodEdge.SwitcherGlyph.BOX_H_DP), Gravity.CENTER));
  switcher.setOnClickListener(v -> openOverview.run());
  overview = switcher;
  this.overviewAvailable = overviewAvailable;
  applyOverview(overviewAvailable.getAsBoolean());
  LayoutParams switcherParams = new LayoutParams(activity.dp(SWITCHER_W_DP), activity.dp(OxbloodEdge.TARGET_H_DP));
  switcherParams.leftMargin = activity.dp(GAP_DP - grow);
  addView(switcher, switcherParams);
 }

 /** One edge control: the recessed plate (OxbloodEdge.Well) in a 44dp-tall hit area that presses down 1dp. */
 static FrameLayout control(MainActivity activity) {
  FrameLayout control = new FrameLayout(activity);
  control.setBackground(new OxbloodEdge.Well(activity.getResources().getDisplayMetrics().density));
  control.setClickable(true);
  control.setFocusable(true);
  control.setClipChildren(false);
  control.setStateListAnimator(HomeAppTile.pressAnimator(activity, View.TRANSLATION_Y, 0, activity.dp(1), 160));
  control.setForeground(HomeAppTile.focusRing(activity.getResources().getDisplayMetrics().density, OxbloodEdge.Well.RADIUS_DP));
  return control;
 }

 /** The edge is 92dp plus whatever the tappable navigation inset needs below it. */
 int heightPx() { return Math.round(HEIGHT_DP * density) + navigationExtra; }

 /** One refresh: the session mark and spoken name from the conversation; the mic well and the pearl from the mute as
  * every surface shows it (RoseMicInput.shown). */
 void show(ConversationState conversation, RoseVisualState visual, boolean micMuted) {
  if (released) return;
  shownConversation = conversation;
  applyMic(micMuted);
  applyVisual(visual);
  boolean available = overviewAvailable.getAsBoolean();
  if (available != shownOverviewAvailable) applyOverview(available);
 }

 /** The switcher control at full strength with its action when the route is available, dimmed with the reason when not. */
 void applyOverview(boolean available) {
  shownOverviewAvailable = available;
  overview.setAlpha(available ? 1f : .55f);
  overview.setContentDescription(HomeOverview.describe(available));
 }

 /** The edge is leaving (frame rebuilt, activity destroyed): accept no further frames. */
 void release() {
  released = true;
 }

 /** The mic well: Rose's microphone-input mute, named for what a tap does. */
 void applyMic(boolean muted) {
  shownMicMuted = muted;
  micGlyph.set(muted);
  String spoken = muted ? "Rose’s microphone is muted. Unmute" : "Mute Rose’s microphone";
  if (!spoken.contentEquals(mic.getContentDescription() == null ? "" : mic.getContentDescription())) mic.setContentDescription(spoken);
 }

 /** The pearl: silver while Rose's microphone input is muted (the shown mute); no other cue on Home. */
 void applyVisual(RoseVisualState visual) {
  shownVisual = visual;
  pearl.set(shownMicMuted, false, -1);
  String spoken = (shownConversation != null && shownConversation.phase == ConversationState.Phase.ACTIVE ? "Return to Rose call. " : "Open Rose. ")
    + RosePresenceModel.describe(visual);
  if (!spoken.contentEquals(presence.getContentDescription())) presence.setContentDescription(spoken);
 }
}
