package dev.pixel.voice;

import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.LauncherApps;
import android.content.pm.ResolveInfo;
import android.content.pm.ShortcutInfo;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.text.InputFilter;
import android.text.InputType;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Project 2: the workspace page to the left of Home (HomePages.WORKSPACE), as in the approved HiFi r2
 * (prototypes/rose-hifi/HANDOFF-WORKSPACE.md).
 *
 * <p><b>Fixed geometry.</b> The request field, Arrange, Regenerate and Undo, the status line (20dp), the recent row
 * (28dp) and the canvas never move. While composing, the old sections stay dimmed under a veil and the buttons are
 * disabled in place. A failure keeps the layout and its pins.
 *
 * <p><b>Composition.</b> A request runs on one worker thread:
 * <ol>
 *   <li>WorkspaceSources reads the phone into a snapshot.</li>
 *   <li>WorkspaceEngine runs Models' composer over it.</li>
 *   <li>WorkspaceComposer checks the result on the main thread, and WorkspaceState saves it.</li>
 * </ol>
 * There is no on-phone model yet, so the composer's inference is WorkspaceStarter, and the page says so.
 *
 * <p><b>Rendering.</b> Rendering shows only what is still on the phone (WorkspaceLayout.available). It never
 * reshuffles the saved layout. The first frame of a committed layout is timed (WorkspaceTiming).
 *
 * <p><b>Gestures.</b> A drag that starts on the field or a carousel belongs to it (keepsDrag); anything else pages as
 * on Home. The page is inert while off screen.
 */
final class WorkspacePage extends FrameLayout {
 static final String PREFS="rose-workspace",KEY="state";
 static final int STATUS_DP=20,RECENT_DP=28,HIT_DP=44,COLUMNS=4;

 final MainActivity activity;
 /** The theme the page was drawn in (RoseThemeNow); Home makes a new page when the theme changes. */
 final String theme=RoseThemeNow.id;
 final WorkspaceState state;
 final WorkspaceComposer composer;
 final WorkspaceEngine engine=new WorkspaceEngine(new WorkspaceStarter());
 final WorkspaceTiming timing=new WorkspaceTiming();
 private final ExecutorService worker=Executors.newSingleThreadExecutor();
 private final Handler main=new Handler(Looper.getMainLooper());
 private AtomicBoolean cancel=new AtomicBoolean();
 private WorkspaceSources.Read lastRead;
 private boolean shown;

 final EditText field;final Button arrange,regenerate,undo;final TextView status,label;
 final LinearLayout recents,sections;final View veil;final ScrollView canvas;
 /** Views that keep a drag that starts on them: the field and recents row (fixed) and the carousels drawn now. */
 private final List<View> keeps=new ArrayList<>(),carousels=new ArrayList<>();

 WorkspacePage(MainActivity activity){
  super(activity);
  this.activity=activity;
  state=WorkspaceState.fromJson(prefs().getString(KEY,null));
  composer=new WorkspaceComposer(state);
  setContentDescription("Workspace");
  int ink=RoseThemeNow.c(0xfff4ede0),muted=RoseThemeNow.c(0xb3f4ede0);

  LinearLayout column=new LinearLayout(activity);column.setOrientation(LinearLayout.VERTICAL);
  TextView title=text("Workspace",22,ink);title.setTypeface(Typeface.DEFAULT_BOLD);column.addView(title);
  label=text(WorkspaceStarter.ID.equals(engine.modelId())?WorkspaceStarter.LABEL:"Composed on this phone",12,muted);
  column.addView(label,new LinearLayout.LayoutParams(-1,dp(18)));

  LinearLayout ask=new LinearLayout(activity);ask.setGravity(Gravity.CENTER_VERTICAL);
  field=new EditText(activity);field.setSingleLine(true);field.setHint("Ask for a workspace");field.setTextColor(ink);field.setHintTextColor(muted);
  field.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);field.setImeOptions(EditorInfo.IME_ACTION_GO);
  field.setFilters(new InputFilter[]{new InputFilter.LengthFilter(WorkspaceLayout.MAX_REQUEST)});
  field.setBackground(pill(0x26ffffff,0));field.setPadding(dp(14),0,dp(14),0);
  field.setOnEditorActionListener((v,id,e)->{if(id==EditorInfo.IME_ACTION_GO||id==EditorInfo.IME_ACTION_DONE){ask(field.getText().toString());return true;}return false;});
  ask.addView(field,new LinearLayout.LayoutParams(0,dp(HIT_DP),1));keeps.add(field);
  column.addView(ask,margins(new LinearLayout.LayoutParams(-1,dp(HIT_DP)),0,10,0,0));

  LinearLayout controls=new LinearLayout(activity);controls.setGravity(Gravity.CENTER_VERTICAL);
  arrange=button(WorkspaceComposer.ARRANGE,()->ask(null));regenerate=button("Regenerate",this::regenerate);undo=button("Undo",this::undo);
  controls.addView(arrange,new LinearLayout.LayoutParams(-2,dp(HIT_DP)));
  controls.addView(regenerate,margins(new LinearLayout.LayoutParams(-2,dp(HIT_DP)),8,0,0,0));
  controls.addView(undo,margins(new LinearLayout.LayoutParams(-2,dp(HIT_DP)),8,0,0,0));
  column.addView(controls,margins(new LinearLayout.LayoutParams(-1,dp(HIT_DP)),0,8,0,0));

  status=text("",13,muted);status.setSingleLine(true);status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
  column.addView(status,margins(new LinearLayout.LayoutParams(-1,dp(STATUS_DP)),0,6,0,0));
  HorizontalScrollView recentScroll=new HorizontalScrollView(activity);recentScroll.setHorizontalScrollBarEnabled(false);
  recents=new LinearLayout(activity);recents.setGravity(Gravity.CENTER_VERTICAL);recentScroll.addView(recents);
  column.addView(recentScroll,new LinearLayout.LayoutParams(-1,dp(RECENT_DP)));keeps.add(recentScroll);

  FrameLayout stage=new FrameLayout(activity);
  canvas=new ScrollView(activity);canvas.setVerticalScrollBarEnabled(false);
  sections=new LinearLayout(activity);sections.setOrientation(LinearLayout.VERTICAL);sections.setPadding(0,dp(8),0,dp(16));
  canvas.addView(sections);stage.addView(canvas,new FrameLayout.LayoutParams(-1,-1));
  veil=new View(activity);veil.setBackgroundColor(0x33000000);veil.setVisibility(View.GONE);veil.setClickable(true);
  stage.addView(veil,new FrameLayout.LayoutParams(-1,-1));
  column.addView(stage,new LinearLayout.LayoutParams(-1,0,1));
  addView(column,new FrameLayout.LayoutParams(-1,-1));

  int side=dp(HomeAppLayout.MARGIN_DP);
  setPadding(side,dp(HomeAppLayout.TOP_DP),side,0);
  setOnApplyWindowInsetsListener((v,i)->{v.setPadding(side+i.getSystemWindowInsetLeft(),dp(HomeAppLayout.TOP_DP)+i.getSystemWindowInsetTop(),side+i.getSystemWindowInsetRight(),i.getSystemWindowInsetBottom());return i;});
  render(null);
  shown(false);
 }

 // ---------- requests ----------

 /** Arrange (text null) or a typed request. A newer request supersedes one still composing. */
 void ask(String text){
  int gen=composer.begin(text,false);
  if(gen<0)return;
  hideKeyboard();
  start(gen);
 }
 void regenerate(){int gen=composer.regenerate();if(gen>=0)start(gen);}
 void undo(){if(!composer.busy()&&state.undo()){save();render(null);}}

 private void start(int gen){
  cancel.set(true);cancel=new AtomicBoolean();final AtomicBoolean cancelled=cancel;
  final String requestId=composer.requestId(),trigger=composer.trigger(),prompt=composer.prompt();
  final List<String> avoid=composer.avoid();
  final Set<String> pinned=pinnedIds();
  timing.requested(requestId,trigger,System.nanoTime());
  showBusy();
  worker.execute(()->{
   WorkspaceSources.Read read;String result;
   try{
    read=WorkspaceSources.read(activity,pinned);
    result=engine.compose(WorkspaceSnapshot.request(requestId,trigger,prompt,read.snapshot,WorkspaceEngine.DEADLINE_MS,avoid),cancelled::get);
   }catch(RuntimeException e){read=null;result=WorkspaceEngine.failed(requestId,"model_unavailable");}
   final WorkspaceSources.Read done=read;final String text=result;final long at=System.nanoTime();
   main.post(()->finish(gen,requestId,done,text,at));
  });
 }

 private void finish(int gen,String requestId,WorkspaceSources.Read read,String result,long at){
  WorkspaceComposer.Outcome outcome=read==null?composer.fail(gen,"model_unavailable"):composer.finish(gen,result,read.snapshot.registry);
  timing.resulted(requestId,outcome,composer.lastFailure(),WorkspaceTiming.modelId(result),WorkspaceTiming.composerTiming(result),at);
  if(outcome==WorkspaceComposer.Outcome.IGNORED)return;
  if(read!=null)lastRead=read;
  if(outcome==WorkspaceComposer.Outcome.COMMITTED){save();render(requestId);}
  else render(null);
 }

 /** The item ids of pinned sections: the composer leaves them out, since the page keeps those sections in place. */
 private Set<String> pinnedIds(){
  Set<String> ids=new HashSet<>();WorkspaceLayout l=state.layout();
  if(l!=null)for(WorkspaceLayout.Section c:l.sections)if(state.pinned(c.key)){ids.addAll(c.items);ids.addAll(c.actions);}
  return ids;
 }

 // ---------- rendering ----------

 private void showBusy(){
  status.setText(composer.status());
  veil.setVisibility(View.VISIBLE);sections.setAlpha(.45f);
  arrange.setEnabled(false);regenerate.setEnabled(false);undo.setEnabled(false);
 }

 /** Draws the current layout. timedRequest names a just-committed request whose first frame is timed. */
 void render(String timedRequest){
  veil.setVisibility(View.GONE);sections.setAlpha(1f);
  status.setText(composer.status());
  arrange.setEnabled(true);regenerate.setEnabled(state.request()!=null);undo.setEnabled(state.canUndo());
  renderRecents();
  sections.removeAllViews();carousels.clear();
  WorkspaceLayout layout=state.layout();
  if(layout!=null&&lastRead==null){
   // A saved layout waits for the first read of the phone, so it never draws an app that has gone.
   refreshSources();return;
  }
  if(layout==null){
   TextView empty=text("Tap "+WorkspaceComposer.ARRANGE+", or ask for what you're doing.",15,RoseThemeNow.c(0xb3f4ede0));
   sections.addView(empty);
  }else{
   WorkspaceLayout shown=lastRead==null?layout:layout.available(lastRead.snapshot.present());
   for(WorkspaceLayout.Section c:shown.sections)sections.addView(section(c),margins(new LinearLayout.LayoutParams(-1,-2),0,0,0,14));
  }
  if(timedRequest!=null)timeFirstFrame(timedRequest);
 }

 private void renderRecents(){
  recents.removeAllViews();
  List<WorkspaceState.Entry> list=state.recent();
  for(int i=0;i<list.size();i++){
   final int index=i;WorkspaceState.Entry e=list.get(i);
   if(e.layout==state.layout())continue;
   String name=e.request==null||WorkspaceComposer.ARRANGE.equals(e.request)?"Best guess":e.request;
   TextView chip=text(name,12,RoseThemeNow.c(0xfff4ede0));chip.setSingleLine(true);chip.setGravity(Gravity.CENTER);
   chip.setBackground(pill(0x1fffffff,0));chip.setPadding(dp(12),0,dp(12),0);chip.setContentDescription("Reopen "+name);
   chip.setOnClickListener(v->{if(!composer.busy()&&state.reopen(index)){save();render(null);}});
   recents.addView(chip,margins(new LinearLayout.LayoutParams(-2,dp(RECENT_DP)),0,0,6,0));
  }
 }

 private View section(final WorkspaceLayout.Section c){
  LinearLayout box=new LinearLayout(activity);box.setOrientation(LinearLayout.VERTICAL);
  box.setBackground(pill(0x1affffff,0));box.setPadding(dp(14),dp(6),dp(6),dp(12));
  LinearLayout head=new LinearLayout(activity);head.setGravity(Gravity.CENTER_VERTICAL);
  TextView title=text(c.title,15,RoseThemeNow.c(0xfff4ede0));title.setTypeface(Typeface.DEFAULT_BOLD);
  head.addView(title,new LinearLayout.LayoutParams(0,-2,1));
  final boolean pinned=state.pinned(c.key);
  Button pin=button(pinned?"Pinned":"Pin",()->{if(!composer.busy()&&state.togglePin(c.key)){save();render(null);}});
  pin.setContentDescription((pinned?"Unpin ":"Pin ")+c.title);
  head.addView(pin,new LinearLayout.LayoutParams(-2,dp(HIT_DP)));
  box.addView(head);
  switch(c.component){
   case "grid":box.addView(grid(c.items));break;
   case "carousel":{HorizontalScrollView s=new HorizontalScrollView(activity);s.setHorizontalScrollBarEnabled(false);LinearLayout row=new LinearLayout(activity);for(String id:c.items)row.addView(tile(id),new LinearLayout.LayoutParams(dp(84),-2));s.addView(row);carousels.add(s);box.addView(s);break;}
   case "button":{LinearLayout row=new LinearLayout(activity);row.setGravity(Gravity.CENTER_VERTICAL);for(String id:c.items)row.addView(action(id),margins(new LinearLayout.LayoutParams(0,dp(HIT_DP),1),0,0,6,0));box.addView(row);break;}
   default:{for(String id:c.items)box.addView(text(labelOf(id),14,RoseThemeNow.c(0xfff4ede0)),new LinearLayout.LayoutParams(-1,dp(HIT_DP)));
    if(!c.actions.isEmpty()){LinearLayout row=new LinearLayout(activity);for(String id:c.actions)row.addView(action(id),margins(new LinearLayout.LayoutParams(0,dp(HIT_DP),1),0,0,6,0));box.addView(row);}}
  }
  return box;
 }

 private View grid(List<String> ids){
  LinearLayout rows=new LinearLayout(activity);rows.setOrientation(LinearLayout.VERTICAL);LinearLayout row=null;
  for(int i=0;i<ids.size();i++){
   if(i%COLUMNS==0){row=new LinearLayout(activity);rows.addView(row,new LinearLayout.LayoutParams(-1,-2));}
   row.addView(tile(ids.get(i)),new LinearLayout.LayoutParams(0,-2,1));
  }
  if(row!=null)for(int k=ids.size()%COLUMNS;k>0&&k<COLUMNS;k++)row.addView(new View(activity),new LinearLayout.LayoutParams(0,1,1));
  return rows;
 }

 /** An app as Home draws and opens it, or an action as a tile. */
 private View tile(String id){
  ResolveInfo app=lastRead==null?null:lastRead.apps.get(id);
  if(app!=null)return activity.appTile(app);
  return action(id);
 }

 private View action(final String id){
  boolean task=id.startsWith("task:");
  Button b=button(labelOf(id),()->act(id));
  b.setContentDescription(labelOf(id)+(task?", Rose task, asks first":""));
  if(task)b.setBackground(pill(0,0x99f4ede0));
  return b;
 }

 private String labelOf(String id){
  if(lastRead!=null){
   for(Object o:(List<?>)lastRead.snapshot.snapshot.get("apps"))if(id.equals(((java.util.Map<?,?>)o).get("id")))return String.valueOf(((java.util.Map<?,?>)o).get("label"));
   for(Object o:(List<?>)lastRead.snapshot.snapshot.get("actions"))if(id.equals(((java.util.Map<?,?>)o).get("id")))return String.valueOf(((java.util.Map<?,?>)o).get("label"));
  }
  return "Unavailable";
 }

 /** A direct action acts at once; a Rose task asks first. The outcome is shown, never assumed. */
 private void act(String id){
  if(composer.busy())return;
  try{
   if(id.startsWith("app:")){ResolveInfo r=lastRead==null?null:lastRead.apps.get(id);if(r!=null){WorkspaceLauncher.open(activity,r,labelOf(id));state.actionResult(id,"opened");}}
   else if(id.startsWith("sc:")){
    ShortcutInfo s=lastRead==null?null:lastRead.shortcuts.get(id);LauncherApps launcher=activity.getSystemService(LauncherApps.class);
    if(s!=null&&launcher!=null){launcher.startShortcut(s.getPackage(),s.getId(),null,null,Process.myUserHandle());state.actionResult(id,"opened");}
   }
   else if(id.startsWith("intent:")){Intent i=WorkspaceSources.template(id.substring("intent:".length()));if(i!=null){activity.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));state.actionResult(id,"opened");}}
   else if(id.startsWith("task:")){
    // Rose tasks need App Core's route, which no server enables yet, so none are offered in the snapshot. If
    // one is ever shown, it still asks first and then says it can't start.
    new AlertDialog.Builder(activity).setTitle(labelOf(id)).setMessage("Rose tasks aren't available on this phone yet.")
     .setPositiveButton("OK",null).show();
   }
  }catch(ActivityNotFoundException|SecurityException|IllegalStateException e){
   state.actionResult(id,"failed");status.setText("Couldn't open "+labelOf(id));
  }
  save();
 }

 /** WorkspaceTiming's render point: the first draw after a committed layout, timed on the draw pass itself. */
 private void timeFirstFrame(final String requestId){
  final ViewTreeObserver observer=sections.getViewTreeObserver();
  final ViewTreeObserver.OnDrawListener[] once=new ViewTreeObserver.OnDrawListener[1];
  once[0]=()->{
   timing.rendered(requestId,System.nanoTime());
   main.post(()->{if(observer.isAlive())observer.removeOnDrawListener(once[0]);});
  };
  observer.addOnDrawListener(once[0]);
  sections.invalidate();
 }

 // ---------- the page in the pager ----------

 /** The pager settled on (or left) the workspace. Off screen the page is inert, and the offer card has done its job once reached. */
 void shown(boolean now){
  shown=now;
  setImportantForAccessibility(now?View.IMPORTANT_FOR_ACCESSIBILITY_AUTO:View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
  setDescendantFocusability(now?ViewGroup.FOCUS_AFTER_DESCENDANTS:ViewGroup.FOCUS_BLOCK_DESCENDANTS);
  if(now){composer.workspaceShown();refreshSources();}else{hideKeyboard();field.clearFocus();}
 }

 /**
  * Re-reads the phone for rendering, without composing: a removed app or shortcut drops out of the layout on
  * screen (WorkspaceLayout.available), and the saved layout itself is not changed.
  */
 private boolean reading;
 void refreshSources(){
  if(reading)return;reading=true;
  final Set<String> pinned=pinnedIds();
  worker.execute(()->{
   WorkspaceSources.Read read=null;
   try{read=WorkspaceSources.read(activity,pinned);}catch(RuntimeException ignored){}
   final WorkspaceSources.Read done=read;
   main.post(()->{reading=false;if(done!=null){lastRead=done;if(!composer.busy())render(null);}});
  });
 }

 /** A drag that starts on the request field or a carousel belongs to it, not to the pager (MainActivity.canSwipePages). */
 boolean keepsDrag(MotionEvent event){
  if(!shown)return false;
  for(View v:keeps)if(activity.touchInside(v,event))return true;
  for(View v:carousels)if(activity.touchInside(v,event))return true;
  return false;
 }

 /** The last few requests' timing, for benchmarks and logs (ids, triggers and numbers only). */
 String timingJson(){return timing.json();}

 void release(){cancel.set(true);worker.shutdownNow();}

 // ---------- small helpers ----------

 private void save(){try{prefs().edit().putString(KEY,state.toJson()).apply();}catch(RuntimeException ignored){}}
 private SharedPreferences prefs(){return activity.getSharedPreferences(PREFS,Context.MODE_PRIVATE);}
 private void hideKeyboard(){InputMethodManager imm=activity.getSystemService(InputMethodManager.class);if(imm!=null)imm.hideSoftInputFromWindow(field.getWindowToken(),0);}
 private int dp(float v){return activity.dp(v);}
 private TextView text(String s,float size,int color){TextView t=new TextView(activity);t.setText(s);t.setTextSize(size);t.setTextColor(color);t.setGravity(Gravity.CENTER_VERTICAL);return t;}
 private Button button(String s,Runnable run){
  Button b=new Button(activity);b.setText(s);b.setAllCaps(false);b.setTextSize(14);b.setTextColor(RoseThemeNow.c(0xfff4ede0));
  b.setMinHeight(dp(HIT_DP));b.setMinimumHeight(dp(HIT_DP));b.setPadding(dp(14),0,dp(14),0);b.setBackground(pill(0x2effffff,0));
  b.setOnClickListener(v->run.run());return b;
 }
 private GradientDrawable pill(int fill,int stroke){GradientDrawable d=new GradientDrawable();d.setCornerRadius(dp(16));d.setColor(RoseThemeNow.c(fill));if(stroke!=0)d.setStroke(dp(1.5f),RoseThemeNow.c(stroke));return d;}
 /** Margins in dp. */
 private LinearLayout.LayoutParams margins(LinearLayout.LayoutParams p,int l,int t,int r,int b){p.setMargins(dp(l),dp(t),dp(r),dp(b));return p;}
}
