package dev.pixel.voice;

import android.content.Context;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
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
  /** Re-reads the controller off the UI thread and opens the viewer only if watchBinding still verifies. */
  void watch(WorkspaceOverview.Identity task);
 }
 /** Selection and Stop survive the panel and the activity (one process). */
 static final class State {
  static final State INSTANCE=new State();
  WorkspaceOverview.Identity selected;final WorkspaceOverview.Stop stop=new WorkspaceOverview.Stop();
  /** An answer for a sent Stop; ignored unless it is for the captured identity. */
  synchronized void ack(WorkspaceOverview.Identity about,WorkspaceOverview.Ack ack){stop.ack(about,ack);}
 }

 private final WorkspaceOverviewView view;private Source source;private Actions actions;
 final State state;

 WorkspaceOverviewPanel(Context c,State state){
  super(c);this.state=state;
  setBackgroundColor(0xfff5f7fa);setClickable(true);
  ScrollView scroll=new ScrollView(c);view=new WorkspaceOverviewView(c);
  int pad=dp(24);view.setPadding(pad,dp(72),pad,pad);
  scroll.addView(view);addView(scroll,new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.MATCH_PARENT));
  Button back=new Button(c);back.setText("Back");back.setAllCaps(false);back.setContentDescription("Back");
  back.setOnClickListener(v->close());
  LayoutParams bp=new LayoutParams(LayoutParams.WRAP_CONTENT,dp(48),Gravity.TOP|Gravity.START);bp.leftMargin=dp(16);bp.topMargin=dp(16);
  addView(back,bp);
  view.setListener(new WorkspaceOverviewView.Listener(){
   public void select(WorkspaceOverview.Identity t){state.selected=t;refresh();}
   public void watch(WorkspaceOverview.Identity t){if(actions!=null)actions.watch(t);}
   public void stop(WorkspaceOverview.Identity t){
    if(actions==null||!actions.stopRoute())return;
    boolean sent;synchronized(state){sent=state.stop.request(t,SystemClock.elapsedRealtime());}
    if(sent)actions.stop(t);refresh();
   }
  });
  setVisibility(GONE);
 }
 void bind(Source source,Actions actions){this.source=source;this.actions=actions;}
 boolean isOpen(){return getVisibility()==VISIBLE;}
 void open(){setVisibility(VISIBLE);refresh();}
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
  view.show(m);
 }
 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
}
