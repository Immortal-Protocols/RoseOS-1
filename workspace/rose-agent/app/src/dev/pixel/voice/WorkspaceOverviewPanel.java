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

/** Rose's apps as an in-screen panel over its host: exactly the apps currently running under Rose's control
 * (#225/#156, CurrentApps), in the selected HiFi's cards and tray. Its data, the Watch launch and the live preview come
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
 private final View endWell;private final WorkspaceOverviewView.Glyph endGlyph,roseGlyph;

 WorkspaceOverviewPanel(Context c){this(c,null,0);}
 /** background: the host's ivory surface (HiFi .ivory-bg), or null for its plain gradient; topInset: the status bar. */
 WorkspaceOverviewPanel(Context c,Drawable background,int topInset){
  super(c);
  setBackground(background!=null?background:new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,new int[]{0xfff7f9fc,0xffeef3f9}));
  setClickable(true);
  // .surface.agents-surface: 64px top, var(--margin) 24px sides, 150px bottom (clears the tray); no visible heading.
  ScrollView scroll=new ScrollView(c);scroll.setVerticalScrollBarEnabled(false);scroll.setClipToPadding(false);view=new WorkspaceOverviewView(c);
  view.setPadding(dp(ConversationPresentation.MARGIN),topInset+dp(64),dp(ConversationPresentation.MARGIN),dp(150));view.setClipToPadding(false);
  scroll.addView(view);addView(scroll,new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.MATCH_PARENT));
  setContentDescription(WorkspaceOverview.TITLE);
  // .tray (Rose's apps controls): Back, End call (Rose when not connected), and this screen's own selected grid well.
  LinearLayout tray=new LinearLayout(c);tray.setGravity(Gravity.CENTER);
  float density=getResources().getDisplayMetrics().density;
  tray.setPadding(dp(ConversationPresentation.TRAY_PAD-TrayMaterial.POCKET_DP),0,dp(ConversationPresentation.TRAY_PAD-TrayMaterial.POCKET_DP),0);
  tray.setBackground(new TrayMaterial.Tray(density));tray.setElevation(dp(12));tray.setContentDescription("Rose’s apps controls");
  View back=well(new WorkspaceOverviewView.Glyph(c,WorkspaceOverviewView.Glyph.BACK,0xff26303f),false);back.setContentDescription("Back");back.setOnClickListener(v->close());
  endGlyph=new WorkspaceOverviewView.Glyph(c,WorkspaceOverviewView.Glyph.END,WorkspaceOverviewView.STOP);
  roseGlyph=new WorkspaceOverviewView.Glyph(c,WorkspaceOverviewView.Glyph.ROSE,WorkspaceOverviewView.ROSE);
  FrameLayout mid=new FrameLayout(c);mid.addView(endGlyph,new LayoutParams(-1,-1));mid.addView(roseGlyph,new LayoutParams(-1,-1));
  endWell=well(mid,false);
  endWell.setOnClickListener(v->{if(source!=null&&source.connected()&&actions!=null)actions.endCall();else close();});
  View self=well(new WorkspaceOverviewView.Glyph(c,WorkspaceOverviewView.Glyph.GRID,0xff26303f),true);self.setContentDescription("Rose’s apps, current screen");
  for(View w:new View[]{back,endWell,self})tray.addView(w,new LinearLayout.LayoutParams(0,-2,1f));
  LayoutParams tp=new LayoutParams(-1,dp(ConversationPresentation.TRAY_HEIGHT),Gravity.BOTTOM);
  tp.leftMargin=tp.rightMargin=dp(ConversationPresentation.MARGIN);tp.bottomMargin=dp(ConversationPresentation.TRAY_BOTTOM);addView(tray,tp);
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
 /** One HiFi .well (TrayMaterial.Well, shared with Home and the conversation tray) around a glyph; the selected well
  * gets the HiFi .well.selected blue ring. */
 private View well(View glyph,boolean selected){
  float density=getResources().getDisplayMetrics().density;
  FrameLayout cell=new FrameLayout(getContext());cell.setClickable(!selected);cell.setFocusable(!selected);
  FrameLayout lens=new FrameLayout(getContext());lens.setBackground(new TrayMaterial.Well(density));lens.setDuplicateParentStateEnabled(true);
  int w=ConversationPresentation.wellWidth(getResources().getConfiguration().screenWidthDp,TrayMaterial.POCKET_DP);
  lens.addView(glyph,new LayoutParams(-1,-1));
  if(selected){View ring=new View(getContext());GradientDrawable g=new GradientDrawable();g.setCornerRadius(dp(36));g.setStroke(Math.max(1,dp(1.5f)),0xb3508cff);
   ring.setBackground(g);LayoutParams rp=new LayoutParams(-1,-1);rp.setMargins(dp(TrayMaterial.POCKET_DP),dp(TrayMaterial.POCKET_DP),dp(TrayMaterial.POCKET_DP),dp(TrayMaterial.POCKET_DP));lens.addView(ring,rp);}
  cell.addView(lens,new LayoutParams(dp(w+2*TrayMaterial.POCKET_DP),dp(ConversationPresentation.WELL_HEIGHT+2*TrayMaterial.POCKET_DP),Gravity.CENTER));
  cell.setMinimumHeight(dp(ConversationPresentation.WELL_HEIGHT+2*TrayMaterial.POCKET_DP));
  return cell;
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
  boolean live=source!=null&&source.connected();
  endGlyph.setVisibility(live?VISIBLE:GONE);roseGlyph.setVisibility(live?GONE:VISIBLE);
  endWell.setContentDescription(live?"End call":"Open Rose");
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
  View scrim=new View(getContext());scrim.setBackgroundColor(0x471e2d47);scrim.setOnClickListener(v->{closeSheet();});
  host.addView(scrim,new LayoutParams(-1,-1));
  LinearLayout box=new LinearLayout(getContext());box.setOrientation(LinearLayout.VERTICAL);box.setPadding(dp(22),dp(26),dp(22),dp(20));
  GradientDrawable bg=new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,new int[]{0xffffffff,0xfff4f7fb});bg.setCornerRadius(dp(32));box.setBackground(bg);box.setElevation(dp(16));
  TextView title=new TextView(getContext());title.setText(STOP_SHEET_TITLE);title.setTextSize(19);title.setTextColor(WorkspaceOverviewView.INK);box.addView(title);
  TextView desc=new TextView(getContext());desc.setText("Rose stops its session for "+names+". Its apps may stay listed until Android closes them.");desc.setTextSize(14);desc.setTextColor(WorkspaceOverviewView.INK2);desc.setPadding(0,dp(8),0,dp(18));box.addView(desc);
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
  GradientDrawable g=new GradientDrawable();g.setCornerRadius(dp(26));g.setColor(danger?WorkspaceOverviewView.STOP:0xffeef2f7);b.setBackground(g);
  b.setTextColor(danger?0xffffffff:WorkspaceOverviewView.INK);b.setTag(danger?"sheet-stop":"sheet-keep");return b;
 }
 void closeSheet(){if(sheet!=null){removeView(sheet);sheet=null;}}
 private WorkspaceOverviewView.Labels labels;
 private String label(String pkg){String l=labels==null?"":labels.label(pkg);return l==null||l.isEmpty()?WorkspaceOverview.WORKSPACE:l;}
 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
}
