package dev.pixel.voice;

import android.content.Context;
import android.view.Gravity;
import android.view.View;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.widget.LinearLayout;
import android.widget.FrameLayout;
import android.widget.ScrollView;

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
 }
 /** Watch can't open for this app from here (no verified handle): said, not silently ignored. */
 static final String WATCH_APP_UNAVAILABLE="Watching this app from here isn’t available yet.";

 private final WorkspaceOverviewView view;private Source source;private Actions actions;
 /** What is shown: only the source's complete reports, by CurrentApps' rules (no history, no resurrection). */
 final CurrentApps.Tracker apps=new CurrentApps.Tracker();
 /** The HiFi observe line: why the last tap opened or stopped nothing. Cleared when the panel opens. */
 private String note="";
 /** The live preview (#225): requested only while open and resumed, for an app's verified handle. */
 final WorkspaceOverviewPreview preview=new WorkspaceOverviewPreview();private boolean resumed=true;
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
  });
  setVisibility(GONE);
 }
 private CurrentApps.App find(String entryId){for(CurrentApps.App a:apps.apps())if(a.entryId.equals(entryId))return a;return null;}
 /** The app's own preview/Watch handle as reported with it, scoped to the report's registration; null without one. */
 WorkspaceOverview.Binding handle(CurrentApps.App a){
  if(a==null||a.preview==null||!a.preview.complete())return null;
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
 void setLabels(WorkspaceOverviewView.Labels labels){view.setLabels(labels);}
 /** Back: only hides the panel; the work is unchanged and any preview is cancelled. */
 void close(){setVisibility(GONE);preview.cancel();}
 /** The host activity resumed / paused (Home, another app, the lock screen): pausing cancels and drops any preview. */
 void resume(){resumed=true;refresh();}
 void pause(){resumed=false;preview.cancel();}
 /** The host is going away: nothing in flight may land, nothing is kept. */
 void teardown(){resumed=false;preview.cancel();apps.reset();}
 /** A preview answer for a request this panel made (UI thread). Shown only if WorkspaceOverviewPreview accepts it. */
 void previewAnswer(WorkspaceOverviewPreview.Request r,WorkspaceOverviewPreview.Response a){
  if(preview.answer(r,a,System.currentTimeMillis()))refresh();
 }
 /** Redraws from the host's current inputs (no-op when hidden, or when nothing changed). */
 void refresh(){
  if(!isOpen())return;
  apps.apply(source==null?null:source.currentApps());
  boolean live=source!=null&&source.connected();
  endGlyph.setVisibility(live?VISIBLE:GONE);roseGlyph.setVisibility(live?GONE:VISIBLE);
  endWell.setContentDescription(live?"End call":"Open Rose");
  // #225: only an app's own verified handle is ever previewed (one at a time); without one there is no request.
  CurrentApps.App target=null;for(CurrentApps.App a:apps.apps())if(handle(a)!=null){target=a;break;}
  WorkspaceOverview.Binding b=handle(target);long now=System.currentTimeMillis();
  WorkspaceOverviewPreview.Request r=preview.next(resumed,b,now);
  if(r!=null&&actions!=null)actions.requestPreview(r);
  view.show(apps.state(),apps.reason(),apps.apps(),note,target==null?null:target.entryId,preview.frameFor(b,now));
 }
 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
}
