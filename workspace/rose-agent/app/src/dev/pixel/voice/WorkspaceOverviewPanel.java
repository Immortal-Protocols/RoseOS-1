package dev.pixel.voice;

import android.content.Context;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.View;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.widget.LinearLayout;
import android.widget.FrameLayout;
import android.widget.ScrollView;
import java.util.Collections;
import java.util.List;

/** The managed workspace overview as an in-screen panel over its host (MANAGED-WORKSPACE-OVERVIEW-001). Its data,
 * the Watch launch and Stop come only through the narrow interfaces below, supplied by the host at App Core's
 * handoff; this class reads no bridge, provider or network itself. Selection and a pending Stop live in a
 * process-wide holder, so closing the panel (Back/Home) and opening it again keeps them, and neither touches the
 * human's foreground app or the work. Missing data stays missing: no binding means no Watch, no Stop route means
 * no Stop. */
final class WorkspaceOverviewPanel extends FrameLayout {
 /** The normalized inputs, read on the UI thread from state the host already holds. */
 interface Source {
  boolean connected();
  WorkspaceOverview.Scope scope();
  List<WorkspaceOverview.Task> tasks();
  /** The latest controller read (may be CHECKING/UNREADABLE). */
  WorkspaceOverview.Workspace workspace();
 }
 /** What the host can do. Each call carries the identity captured at the tap. */
 interface Actions {
  /** An authoritative Stop route with an answer exists; false keeps Stop unavailable. */
  boolean stopRoute();
  /** Sends Stop for exactly this identity; the answer comes back through State.ack. */
  void stop(WorkspaceOverview.Identity task);
  /** Re-reads the controller off the UI thread and opens the viewer only if watchBinding still verifies and equals
   * the binding the row showed at the tap. */
  void watch(WorkspaceOverview.Identity task,WorkspaceOverview.Binding atTap);
  /** The tray's End call well while connected (HiFi ag-mid); the voice conversation's own End. */
  void endCall();
 }
 /** Selection and Stop survive the panel and the activity (one process). */
 static final class State {
  static final State INSTANCE=new State();
  WorkspaceOverview.Identity selected;final WorkspaceOverview.Stop stop=new WorkspaceOverview.Stop();
  /** An answer for a sent Stop; ignored unless it is for the captured identity. */
  synchronized void ack(WorkspaceOverview.Identity about,WorkspaceOverview.Ack ack){stop.ack(about,ack);}
 }

 private final WorkspaceOverviewView view;private Source source;private Actions actions;private WorkspaceOverview.Model shown;
 final State state;
 /** The HiFi observe line: why the last tap opened or stopped nothing. Cleared when the panel opens. */
 private String note="";
 private final View endWell;private final WorkspaceOverviewView.Glyph endGlyph,roseGlyph;

 WorkspaceOverviewPanel(Context c,State state){this(c,state,null,0);}
 /** background: the host's ivory surface (HiFi .ivory-bg), or null for its plain gradient; topInset: the status bar. */
 WorkspaceOverviewPanel(Context c,State state,Drawable background,int topInset){
  super(c);this.state=state;
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
   public void select(WorkspaceOverview.Identity t){state.selected=t;
    WorkspaceOverview.Row r=row(t);note=r==null?"":r.canWatch?"":r.watchReason;
    if(r!=null&&r.canWatch&&actions!=null){actions.watch(t,r.binding);note="";}refresh();}
   public void watch(WorkspaceOverview.Identity t){WorkspaceOverview.Row r=row(t);if(actions!=null&&r!=null&&r.canWatch)actions.watch(t,r.binding);}
   public void workspace(){
    if(shown==null)return;WorkspaceOverview.Row w=shown.watchRow;
    if(w!=null&&actions!=null){note="";actions.watch(w.identity,w.binding);}else note=shown.cardReason();
    refresh();
   }
   public void stop(WorkspaceOverview.Identity t){
    if(actions==null||!actions.stopRoute())return;
    boolean sent;synchronized(state){sent=state.stop.request(t,SystemClock.elapsedRealtime());}
    if(sent)actions.stop(t);refresh();
   }
   public void stopUnavailable(String reason){note=reason;refresh();}
  });
  setVisibility(GONE);
 }
 private WorkspaceOverview.Row row(WorkspaceOverview.Identity t){
  // The exact identity the card was drawn for (id and both scope parts), even while its scope is not yet known.
  if(shown==null||t==null)return null;
  for(WorkspaceOverview.Row r:shown.rows)if(r.identity.id.equals(t.id)&&r.identity.scope.server.equals(t.scope.server)&&r.identity.scope.pairing.equals(t.scope.pairing))return r;
  return null;
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
 /** Back/Home: only hides the panel. The work, the selection and any pending Stop are unchanged. */
 void close(){setVisibility(GONE);}
 /** Redraws from the host's current inputs (no-op when hidden, or when nothing changed). */
 void refresh(){
  if(!isOpen())return;
  WorkspaceOverview.Model m;
  synchronized(state){
   state.stop.tick(SystemClock.elapsedRealtime());
   boolean connected=source!=null&&source.connected();
   List<WorkspaceOverview.Task> tasks=source==null?Collections.<WorkspaceOverview.Task>emptyList():source.tasks();
   m=WorkspaceOverview.of(connected,source==null?WorkspaceOverview.Scope.UNKNOWN:source.scope(),
    source==null?WorkspaceOverview.Workspace.UNREADABLE:source.workspace(),tasks,state.selected,state.stop,actions!=null&&actions.stopRoute());
  }
  shown=m;boolean live=source!=null&&source.connected();
  endGlyph.setVisibility(live?VISIBLE:GONE);roseGlyph.setVisibility(live?GONE:VISIBLE);
  endWell.setContentDescription(live?"End call":"Open Rose");
  view.show(m,note);
 }
 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
}
