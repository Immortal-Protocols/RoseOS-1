package dev.pixel.voice;

import android.content.Context;
import android.view.Gravity;
import android.view.View;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.widget.LinearLayout;
import android.widget.FrameLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Rose's apps (Background Work) as an in-screen panel over its host: exactly the apps currently running under Rose's
 * control (#225/#156, CurrentApps), in the Oxblood cards with the pearl and Back to Rose at the top (#410). Its data, the Watch launch and the live preview come
 * only through the narrow interfaces below; this class reads no bridge, provider or network itself. Closing it (Back)
 * or leaving (Home, lock) never touches the work or the human's foreground app; reopening starts from the source's
 * current report, never from what was shown before. Missing data stays missing: no report means unavailable, no
 * verified preview handle means no live view. */
final class WorkspaceOverviewPanel extends FrameLayout {
 /** The inputs, read on the UI thread from state the host already holds. */
 interface Source {
  /** The voice conversation is live (the tray's middle well ends it; otherwise it opens Rose). */
  boolean connected();
  /** The latest complete report of the apps running under Rose's control (CurrentApps.Snapshot). */
  CurrentApps.Snapshot currentApps();
  /** Rose's own call is live with its microphone muted (the header pearl turns silver); never an OS-wide claim. */
  default boolean muted(){return false;}
 }
 /** What the host can do. */
 interface Actions {
  /** Re-reads the controller off the UI thread and opens its viewer only for exactly this app's session (atTap), if
   * that is still the live one. */
  void watch(CurrentApps.App app,WorkspaceOverview.Binding atTap);
  /** The tray's End call well while connected (HiFi ag-mid); the voice conversation's own End. */
  void endCall();
  /** Sends one read-only controller preview request (#225) off the UI thread and hands its answer to
   * previewAnswer on the UI thread. A host without the provider leaves this as is: no preview, the text body stays. */
  default void requestPreview(WorkspaceOverviewPreview.Request request){}
  /** The host can send the controller's owner Stop for one session (owner_stop {sessionId, taskId}); false keeps Stop
   * unavailable with its reason. */
  default boolean stopRoute(){return false;}
  /** Sends owner_stop for exactly this captured pair off the UI thread; its answer comes back through stopAnswer. */
  default void ownerStop(String sessionId,String taskId){}
  /** Reads the routed sessions summary off the UI thread (to confirm a pending Stop); entries come back through
   * stopSummary. */
  default void checkStops(){}
 }
 /** Session Stops survive Back, Home and re-entry (one process); a confirmed one is kept only to say so. */
 static final java.util.Map<String,CurrentApps.SessionStop> STOPS=new java.util.LinkedHashMap<>();
 static final String STOP_SHEET_TITLE="Stop Rose’s work in these apps?";
 static final String STOP_DONE_NOTE="Stopped. Its apps stay listed until Android closes them.";
 static final String STOP_UNKNOWN_NOTE="Rose hasn’t confirmed the stop. Its apps may still be running.";
 /** Watch can't open for this app from here (no verified handle): said, not silently ignored. */
 static final String WATCH_APP_UNAVAILABLE="Watching this app from here isn’t available yet.";

 private final WorkspaceOverviewView view;private Source source;private Actions actions;
 /** What is shown: only the source's complete reports, by CurrentApps' rules (no history, no resurrection). */
 final CurrentApps.Tracker apps=new CurrentApps.Tracker();
 /** The HiFi observe line: why the last tap opened or stopped nothing. Cleared when the panel opens. */
 private String note="";
 /** The live preview (#225): one gate per previewable controller session (two at most), requested only while open
  * and resumed, each for its own session's front app. */
 final java.util.Map<String,WorkspaceOverviewPreview> gates=new java.util.LinkedHashMap<>();private boolean resumed=true;
 private long lastStopCheck;private View sheet;
 private final RosePearl pearl;

 WorkspaceOverviewPanel(Context c){this(c,null,0);}
 /** background: the host's Oxblood Rose field (null: a new one); topInset: the status bar. */
 WorkspaceOverviewPanel(Context c,Drawable background,int topInset){
  super(c);
  setBackground(background!=null?background:new OxbloodField(OxbloodField.ROSE));
  setClickable(true);
  float density=getResources().getDisplayMetrics().density;
  // #410 (oxblood.css:418-422): no visible heading; the grid starts 60dp under the header row, 24dp in, 40dp clear at
  // the bottom, and scrolls under the fixed header.
  int headTop=Math.max(dp(40),topInset+dp(4));
  ScrollView scroll=new ScrollView(c);scroll.setVerticalScrollBarEnabled(false);scroll.setClipToPadding(false);view=new WorkspaceOverviewView(c);
  view.setPadding(dp(ConversationPresentation.MARGIN),headTop+dp(60),dp(ConversationPresentation.MARGIN),dp(40));view.setClipToPadding(false);
  scroll.addView(view);addView(scroll,new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.MATCH_PARENT));
  setContentDescription(WorkspaceOverview.TITLE);
  // Header (oxblood.css:418-420,509-512): the 18dp pearl, then Back to Rose: a 44dp target, 40dp plate radius 10.
  LinearLayout head=new LinearLayout(c);head.setGravity(Gravity.CENTER_VERTICAL);
  pearl=new RosePearl(density,18);View pv=new View(c);pv.setBackground(pearl);pv.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  head.addView(pv,new LinearLayout.LayoutParams(dp(30),dp(30)));
  FrameLayout back=new FrameLayout(c);
  GradientDrawable plate=new GradientDrawable();plate.setColor(0x40140a0c);plate.setCornerRadius(dp(10));plate.setStroke(Math.max(1,Math.round(1.25f*density)),0xd9beb4ae);
  android.graphics.drawable.InsetDrawable inset=new android.graphics.drawable.InsetDrawable(plate,dp(2));back.setBackground(inset);
  back.addView(new WorkspaceOverviewView.Glyph(c,WorkspaceOverviewView.Glyph.BACK,WorkspaceOverviewView.OX_INK),new LayoutParams(-1,-1));
  back.setClickable(true);back.setFocusable(true);back.setContentDescription("Back to Rose");back.setOnClickListener(v->close());back.setForeground(HomeAppTile.focusRing(density,10));
  LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(dp(44),dp(44));bp.leftMargin=dp(10);head.addView(back,bp);
  LayoutParams hp=new LayoutParams(-2,dp(44),Gravity.TOP|Gravity.START);hp.topMargin=headTop;hp.leftMargin=dp(18);addView(head,hp);
  view.setListener(new WorkspaceOverviewView.Listener(){
   public void app(String entryId){
    CurrentApps.App a=find(entryId);if(a==null)return;
    WorkspaceOverview.Binding b=handle(a);
    if(b!=null&&actions!=null){note="";actions.watch(a,b);}else note=WATCH_APP_UNAVAILABLE;
    refresh();
   }
   public void stopUnavailable(String reason){note=reason;refresh();}
   public void stop(CurrentApps.App a){askStop(a);}
  });
  setVisibility(GONE);
 }
 private CurrentApps.App find(String entryId){for(CurrentApps.App a:apps.apps())if(a.entryId.equals(entryId))return a;return null;}
 /** The app's own preview/Watch handle as reported with it, scoped to the report's registration; null without one. */
 WorkspaceOverview.Binding handle(CurrentApps.App a){
  // Watch: the legacy single-session reader only (an app with no binding reference). Bound apps of the two-app contract
  // wait for App Core's per-session viewer, so they have no Watch handle yet (never a guessed second-session view).
  if(a==null||a.preview==null||!a.preview.complete()||!a.sessionId.isEmpty())return null;
  return new WorkspaceOverview.Binding(a.preview.controllerTaskId,a.preview.sessionId,a.packageName,new WorkspaceOverview.Scope("current-apps",apps.registration()));
 }
 void bind(Source source,Actions actions){this.source=source;this.actions=actions;}
 boolean isOpen(){return getVisibility()==VISIBLE;}
 void open(){note="";setVisibility(VISIBLE);refresh();}
 /** Supplies the workspace app's real icon to the window card. */
 void setIcons(WorkspaceOverviewView.Icons icons){view.setIcons(icons);}
 /** Supplies the app's visible name to its card. */
 void setLabels(WorkspaceOverviewView.Labels labels){this.labels=labels;view.setLabels(labels);}
 /** Back: only hides the panel; the work is unchanged and any preview is cancelled. */
 void close(){setVisibility(GONE);closeSheet();cancelPreviews();}
 private void cancelPreviews(){for(WorkspaceOverviewPreview g:gates.values())g.cancel();gates.clear();}
 /** The host activity resumed / paused (Home, another app, the lock screen): pausing cancels and drops any preview. */
 void resume(){resumed=true;refresh();}
 void pause(){resumed=false;cancelPreviews();}
 /** The host is going away: nothing in flight may land, nothing is kept. */
 void teardown(){resumed=false;cancelPreviews();apps.reset();}
 /** A preview answer for a request this panel made (UI thread). Shown only if WorkspaceOverviewPreview accepts it. */
 void previewAnswer(WorkspaceOverviewPreview.Request r,WorkspaceOverviewPreview.Response a){
  WorkspaceOverviewPreview g=r==null?null:gates.get(r.sessionId());
  if(g!=null&&g.answer(r,a,System.currentTimeMillis()))refresh();
 }
 /** Redraws from the host's current inputs (no-op when hidden, or when nothing changed). */
 void refresh(){
  if(!isOpen())return;
  apps.apply(source==null?null:source.currentApps());
  pearl.set(source!=null&&source.muted(),false,-1);
  // #225: each bound session's front app (its own handle) is previewed through its own gate; nothing else ever is.
  long now=System.currentTimeMillis();
  java.util.Map<String,CurrentApps.App> targets=new java.util.LinkedHashMap<>();
  for(CurrentApps.App a:apps.apps())if(a.preview!=null&&a.preview.complete()&&!targets.containsKey(a.preview.sessionId))targets.put(a.preview.sessionId,a);
  for(java.util.Iterator<java.util.Map.Entry<String,WorkspaceOverviewPreview>> it=gates.entrySet().iterator();it.hasNext();){
   java.util.Map.Entry<String,WorkspaceOverviewPreview> e=it.next();if(!targets.containsKey(e.getKey())){e.getValue().cancel();it.remove();}}
  java.util.Map<String,WorkspaceOverviewPreview.Frame> frames=new java.util.HashMap<>();
  for(CurrentApps.App a:targets.values()){
   WorkspaceOverviewPreview g=gates.get(a.preview.sessionId);if(g==null){g=new WorkspaceOverviewPreview();gates.put(a.preview.sessionId,g);}
   WorkspaceOverview.Binding b=new WorkspaceOverview.Binding(a.preview.controllerTaskId,a.preview.sessionId,a.packageName,new WorkspaceOverview.Scope("current-apps",apps.registration()));
   WorkspaceOverviewPreview.Request r=g.next(resumed,b,now);
   if(r!=null&&actions!=null)actions.requestPreview(r);
   WorkspaceOverviewPreview.Frame f=g.frameFor(b,now);if(f!=null)frames.put(a.entryId,f);
  }
  // Session Stops: time out to unknown, and ask for a confirmation about once a second while any may still settle.
  java.util.Map<String,CurrentApps.SessionStop.Phase> phases=new java.util.HashMap<>();boolean checking=false;
  synchronized(STOPS){for(CurrentApps.SessionStop st:STOPS.values()){st.tick(now);phases.put(st.sessionId,st.phase);checking|=st.checking(now);}}
  if(checking&&actions!=null&&now-lastStopCheck>=1000){lastStopCheck=now;actions.checkStops();}
  view.show(apps.state(),apps.reason(),apps.apps(),note,frames,phases,actions!=null&&actions.stopRoute());
 }
 /** owner_stop's own reply for the captured pair (UI thread): confirmed true settles it; anything else waits. */
 void stopAnswer(String sessionId,String taskId,Boolean confirmed){
  synchronized(STOPS){CurrentApps.SessionStop st=STOPS.get(sessionId);if(st!=null&&st.taskId.equals(taskId==null?"":taskId))st.answered(confirmed);}
  noteStops();refresh();
 }
 /** One routed sessions-summary entry (UI thread): may confirm the pending Stop of exactly its stopped pair. */
 void stopSummary(String slotStatus,String stopReason,String stoppedSessionId,String stoppedTaskId){
  synchronized(STOPS){for(CurrentApps.SessionStop st:STOPS.values())st.summary(slotStatus,stopReason,stoppedSessionId,stoppedTaskId);}
  noteStops();refresh();
 }
 private void noteStops(){
  synchronized(STOPS){for(CurrentApps.SessionStop st:STOPS.values()){
   if(st.phase==CurrentApps.SessionStop.Phase.STOPPED)note=STOP_DONE_NOTE;else if(st.phase==CurrentApps.SessionStop.Phase.UNKNOWN)note=STOP_UNKNOWN_NOTE;}}
 }
 /** The HiFi Stop sheet (style.css .sheet, views.js renderSheet): names every app of the session it stops. */
 void askStop(final CurrentApps.App a){
  if(a==null||a.stop==null||actions==null||!actions.stopRoute())return;
  final String sid=a.stop.sessionId,tid=a.stop.controllerTaskId;
  StringBuilder names=new StringBuilder();
  for(CurrentApps.App o:apps.apps())if(sid.equals(o.sessionId)){if(names.length()>0)names.append(", ");names.append(label(o.packageName));}
  closeSheet();
  FrameLayout host=new FrameLayout(getContext());host.setTag("stop-sheet");
  View scrim=new View(getContext());scrim.setBackgroundColor(0x80080203);scrim.setOnClickListener(v->{closeSheet();});
  host.addView(scrim,new LayoutParams(-1,-1));
  LinearLayout box=new LinearLayout(getContext());box.setOrientation(LinearLayout.VERTICAL);box.setPadding(dp(22),dp(26),dp(22),dp(20));
  GradientDrawable bg=new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,new int[]{0xff3a1219,0xff24090e});bg.setCornerRadius(dp(32));box.setBackground(bg);box.setElevation(dp(16));
  TextView title=new TextView(getContext());title.setText(STOP_SHEET_TITLE);title.setTextSize(22);title.setLetterSpacing(-.02f);title.setTextColor(WorkspaceOverviewView.OX_INK);title.setAccessibilityHeading(true);box.addView(title);
  TextView desc=new TextView(getContext());desc.setText("Rose stops its session for "+names+". Its apps may stay listed until Android closes them.");desc.setTextSize(15);desc.setLineSpacing(0,1.45f);desc.setTextColor(WorkspaceOverviewView.OX_INK2);desc.setPadding(0,dp(8),0,dp(20));box.addView(desc);
  LinearLayout row=new LinearLayout(getContext());
  TextView keep=sheetButton("Keep working",false),stop=sheetButton("Stop",true);
  keep.setOnClickListener(v->closeSheet());
  stop.setOnClickListener(v->{closeSheet();
   synchronized(STOPS){STOPS.put(sid,new CurrentApps.SessionStop(sid,tid,System.currentTimeMillis()));}
   note="";actions.ownerStop(sid,tid);refresh();});
  LinearLayout.LayoutParams kp=new LinearLayout.LayoutParams(0,dp(52),1f),sp=new LinearLayout.LayoutParams(0,dp(52),1f);sp.leftMargin=dp(10);
  row.addView(keep,kp);row.addView(stop,sp);box.addView(row);
  LayoutParams bp=new LayoutParams(-1,-2,Gravity.BOTTOM);bp.setMargins(dp(12),0,dp(12),dp(12));host.addView(box,bp);
  sheet=host;addView(host,new LayoutParams(-1,-1));
 }
 private TextView sheetButton(String text,boolean danger){
  TextView b=new TextView(getContext());b.setText(text);b.setGravity(Gravity.CENTER);b.setTextSize(15);b.setClickable(true);b.setFocusable(true);
  GradientDrawable g=new GradientDrawable();g.setCornerRadius(dp(26));g.setColor(danger?0xffc42a3a:0x47000000);if(!danger)g.setStroke(Math.max(1,dp(1)),0x47ecd5c6);b.setBackground(g);
  b.setTextColor(danger?0xffffffff:WorkspaceOverviewView.OX_INK);b.setForeground(HomeAppTile.focusRing(getResources().getDisplayMetrics().density,26));b.setTag(danger?"sheet-stop":"sheet-keep");return b;
 }
 void closeSheet(){if(sheet!=null){removeView(sheet);sheet=null;}}
 private WorkspaceOverviewView.Labels labels;
 private String label(String pkg){String l=labels==null?"":labels.label(pkg);return l==null||l.isEmpty()?WorkspaceOverview.WORKSPACE:l;}
 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
}
