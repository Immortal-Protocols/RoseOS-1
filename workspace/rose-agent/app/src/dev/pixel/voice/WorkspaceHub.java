package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Project 2: the one owner of the workspace on this phone. The page (WorkspacePage) and the composer provider
 * (SettingsAgentProvider's workspace_compose, from a conversation) both use it, so there is one saved state, one
 * composer, one dispatcher (WorkspaceRequests) and one timing record, and nothing writes the store twice.
 *
 * <ul>
 *   <li>State, composer and dispatcher live on the main thread.</li>
 *   <li>Reading the phone and composing run on one worker thread.</li>
 *   <li>The page listens while it exists. Without it, a conversational request still composes, is saved, and
 *   reads as prepared.</li>
 *   <li>The store is saved after every change. The highest revision saved is kept separately, so the revision
 *   never goes back, even if the store is lost.</li>
 * </ul>
 */
final class WorkspaceHub {
 static final String PREFS="rose-workspace",KEY="state",REVISION="revision";

 /** The page, while it exists. */
 interface Listener {
  void busy();
  /** Draw the state again; timedRequest names a just-committed request whose first frame is timed, or null. */
  void changed(String timedRequest);
 }

 private static WorkspaceHub instance;
 static synchronized WorkspaceHub get(Context context){
  if(instance==null)instance=new WorkspaceHub(context.getApplicationContext());
  return instance;
 }

 final Context app;
 final WorkspaceState state;
 final WorkspaceComposer composer;
 final WorkspaceRequests requests;
 final WorkspaceEngine engine=new WorkspaceEngine(new WorkspaceStarter());
 final WorkspaceTiming timing=new WorkspaceTiming();
 private final ExecutorService worker=Executors.newSingleThreadExecutor();
 /** Rose task requests (up to 3 attempts of 10 s) never hold up composing. */
 private final ExecutorService taskWorker=Executors.newSingleThreadExecutor();
 private final Handler main=new Handler(Looper.getMainLooper());
 private AtomicBoolean cancel=new AtomicBoolean();
 private Listener listener;
 /** The pager is on the workspace (pageShown) and Home's window is visible (windowVisible): only both together is on screen. */
 private boolean pageShown,windowVisible,reading;
 WorkspaceSources.Read lastRead;

 private WorkspaceHub(Context app){
  this.app=app;
  state=WorkspaceState.fromJson(prefs().getString(KEY,null));
  state.raiseRevision(prefs().getLong(REVISION,0));
  composer=new WorkspaceComposer(state);
  requests=new WorkspaceRequests(state,composer);
  tasks=WorkspaceTaskTracker.fromJson(prefs().getString(TASKS,null));
 }

 /** True only with a real model on the phone: today the starter, so prompt is not offered (WorkspaceAgent.kinds). */
 boolean model(){return WorkspaceTiming.isModel(engine.modelId());}

 // ---------- the page ----------

 void attach(Listener page){listener=page;}
 void detach(Listener page){if(listener==page){listener=null;pageShown=false;windowVisible=false;}}
 void shown(boolean now){pageShown=now&&listener!=null;if(onScreen())composer.workspaceShown();if(now)recoverTasks();}
 /** Home's window became visible or not (the person left for another app, or came back). */
 void windowVisible(boolean now){windowVisible=now&&listener!=null;if(onScreen())composer.workspaceShown();}
 /** On screen for section 4.2: the workspace page is the pager's page and Home is in front. Otherwise a commit is prepared, not shown. */
 boolean onScreen(){return pageShown&&windowVisible;}

 void ask(String text){WorkspaceRequests.Step s=requests.page(text);if(s.composes())start(s.gen);}
 void regenerate(){WorkspaceRequests.Step s=requests.pageRegenerate();if(s.composes())start(s.gen);}
 void undo(){if(!composer.busy()&&state.undo())changed(null);}
 void togglePin(String key){if(!composer.busy()&&state.togglePin(key))changed(null);}
 void reopen(int index){if(!composer.busy()&&state.reopen(index))changed(null);}
 void actionResult(String id,String status){state.actionResult(id,status);save();}

 // ---------- Rose tasks (App Core section 4.3, page-task state v1) ----------

 static final String TASKS="tasks";
 /** Each confirmed tap and what App Core has said of it; saved, so a restart keeps it (WorkspaceTaskTracker). */
 WorkspaceTaskTracker tasks;
 boolean tasksEnabled(){return WorkspaceTaskGate.enabled(app);}
 String taskLine(String id){return tasks.line(id);}
 private WorkspaceTaskClient taskClient(){return new WorkspaceTaskClient(WorkspaceTaskGate.gate(app),new WorkspaceTaskTransport(app),WorkspaceTaskTransport.signer(app));}

 /**
  * The person confirmed task id on the confirm sheet: one signed POST /workspace/v1/tasks, with a fresh
  * requestId that every retry reuses. Only for a task in the current snapshot, and only while the gate is on
  * (WorkspaceTaskClient refuses otherwise). Runs on the task worker; the card updates on the main thread.
  */
 void startTask(String id){
  final WorkspaceSnapshot.Task task=lastRead==null?null:lastRead.snapshot.task(id);
  final String requestId="tap-"+java.util.UUID.randomUUID();
  final WorkspaceTaskClient client=taskClient();
  tasks.tapped(id,requestId);saveTasks();
  if(listener!=null)listener.changed(null);
  taskWorker.execute(()->{
   WorkspaceTaskClient.Outcome o;
   try{o=client.start(requestId,task,true);}catch(Throwable t){o=new WorkspaceTaskClient.Outcome(WorkspaceTaskClient.Kind.UNCERTAIN,"internal",null,0);}
   final WorkspaceTaskClient.Outcome done=o;
   main.post(()->{tasks.replied(requestId,done);saveTasks();actionResult(id,done.kind.name().toLowerCase(java.util.Locale.ROOT));if(listener!=null)listener.changed(null);});
  });
 }

 /**
  * A /ws/events message (LocalRoseLink, any thread): a live or replayed workspace_task state is applied to
  * its tap's record on the main thread. Anything else is ignored.
  */
 static void taskEvent(Context context,String raw){
  final WorkspaceTaskTracker.State s=WorkspaceTaskTracker.event(raw);
  if(s==null)return;
  final WorkspaceHub hub=get(context);
  hub.main.post(()->{if(hub.tasks.apply(s)!=null){hub.saveTasks();if(hub.listener!=null)hub.listener.changed(null);}});
 }

 /**
  * Reads App Core's state for every tap whose end is not known yet (signed GET, gate on only): after a restart,
  * or when the page is reached. A 404 or a failure changes nothing.
  */
 void recoverTasks(){
  if(!tasksEnabled())return;
  final java.util.List<String> open=tasks.openTaskIds();if(open.isEmpty())return;
  final WorkspaceTaskClient client=taskClient();
  taskWorker.execute(()->{
   for(String id:open){
    final WorkspaceTaskTracker.State s;
    try{s=client.lookup(id);}catch(Throwable t){continue;}
    if(s!=null)main.post(()->{if(tasks.apply(s)!=null){saveTasks();if(listener!=null)listener.changed(null);}});
   }
  });
 }

 private void saveTasks(){try{prefs().edit().putString(TASKS,tasks.toJson()).apply();}catch(RuntimeException ignored){}}

 // ---------- a conversation (WorkspaceAgent) ----------

 /**
  * App Core's workspace_compose, on the main thread. The reply is called exactly once with the section 4.2
  * result. A requestId answered before gets its first answer again, and nothing new is composed.
  */
 void conversation(WorkspaceAgent.Request r,WorkspaceRequests.Reply reply){
  WorkspaceRequests.Step s=requests.conversation(r,model(),onScreen(),reply);
  if(s.composes())start(s.gen);else if(WorkspaceAgent.UNDO.equals(r.kind))changed(null);
 }

 /** How long the provider waits for a composition before answering uncertain (its own wait is 8 seconds). */
 static final long SERVE_MS=7000;

 /**
  * SettingsAgentProvider's workspace_compose, called on the provider's worker thread, never the main thread.
  * params is the command's params object (section 4.1); the answer is its section 4.2 result. A composition
  * that is still running when the wait ends answers uncertain. It still commits, and a repeat of the same
  * requestId then gets that answer.
  */
 static Map<String,Object> serve(Context context,String paramsJson){
  Map<String,Object> params;
  try{Object o=MiniJson.parse(paramsJson==null?"{}":paramsJson);params=o instanceof Map?castMap(o):null;}catch(RuntimeException e){params=null;}
  final WorkspaceAgent.Request r;
  try{r=WorkspaceAgent.parse(params);}catch(WorkspaceAgent.Refused e){return WorkspaceAgent.result(WorkspaceAgent.requestId(params),"rejected",null,e.reason);}
  if(Looper.myLooper()==Looper.getMainLooper())return WorkspaceAgent.result(r.requestId,"uncertain",null,"main_thread");
  final java.util.concurrent.CountDownLatch done=new java.util.concurrent.CountDownLatch(1);
  final java.util.concurrent.atomic.AtomicReference<Map<String,Object>> answer=new java.util.concurrent.atomic.AtomicReference<>();
  final WorkspaceHub hub=get(context);
  final WorkspaceRequests.Reply reply=WorkspaceRecovery.once(result->{if(answer.compareAndSet(null,result))done.countDown();});
  // Runs on Rose Agent's main thread, which is Home's: a bug here answers uncertain rather than crashing Home.
  hub.main.post(()->WorkspaceRecovery.handle(()->hub.conversation(r,reply),reply,r.requestId));
  try{if(!done.await(SERVE_MS,java.util.concurrent.TimeUnit.MILLISECONDS))return WorkspaceAgent.result(r.requestId,"uncertain",null,"still_composing");}
  catch(InterruptedException e){Thread.currentThread().interrupt();return WorkspaceAgent.result(r.requestId,"uncertain",null,"interrupted");}
  return answer.get();
 }
 @SuppressWarnings("unchecked") private static Map<String,Object> castMap(Object o){return (Map<String,Object>)o;}

 /** The kinds for the provider's capabilities (section 4.1). */
 static List<String> kinds(Context context){return WorkspaceAgent.kinds(get(context).model());}

 // ---------- composing ----------

 private void start(int gen){
  cancel.set(true);cancel=new AtomicBoolean();final AtomicBoolean cancelled=cancel;
  final String requestId=composer.requestId(),trigger=composer.trigger(),prompt=composer.prompt();
  final List<String> avoid=composer.avoid();
  final Set<String> pinned=pinnedIds();
  timing.requested(requestId,trigger,System.nanoTime());
  if(listener!=null)listener.busy();
  worker.execute(()->{
   // Anything at all, an Error such as running out of memory included, still ends this request
   // (WorkspaceRecovery.compose): otherwise the composer would stay busy for ever.
   final WorkspaceSources.Read[] read={null};
   final String text=WorkspaceRecovery.compose(requestId,()->{
    read[0]=WorkspaceSources.read(app,pinned);
    return engine.compose(WorkspaceSnapshot.request(requestId,trigger,prompt,read[0].snapshot,WorkspaceEngine.DEADLINE_MS,avoid),cancelled::get);
   });
   final WorkspaceSources.Read done=read[0];final long at=System.nanoTime();
   main.post(()->WorkspaceRecovery.apply(()->finish(gen,requestId,done,text,at),composer,requests,gen,()->{if(listener!=null)listener.changed(null);}));
  });
 }

 private void finish(int gen,String requestId,WorkspaceSources.Read read,String result,long at){
  WorkspaceComposer.Outcome outcome=read==null?composer.fail(gen,"model_unavailable"):composer.finish(gen,result,read.snapshot.registry);
  timing.resulted(requestId,outcome,composer.lastFailure(),WorkspaceTiming.modelId(result),WorkspaceTiming.composerTiming(result),at);
  if(outcome!=WorkspaceComposer.Outcome.IGNORED&&read!=null)lastRead=read;
  if(outcome==WorkspaceComposer.Outcome.COMMITTED)save();
  // Shown only when the page is on screen now; its first frame is then timed.
  requests.finished(gen,outcome,onScreen());
  if(outcome!=WorkspaceComposer.Outcome.IGNORED&&listener!=null)listener.changed(outcome==WorkspaceComposer.Outcome.COMMITTED?requestId:null);
 }

 /** The item ids of pinned sections: the composer leaves them out, since the page keeps those sections in place. */
 private Set<String> pinnedIds(){
  Set<String> ids=new HashSet<>();WorkspaceLayout l=state.layout();
  if(l!=null)for(WorkspaceLayout.Section c:l.sections)if(state.pinned(c.key)){ids.addAll(c.items);ids.addAll(c.actions);}
  return ids;
 }

 /** Re-reads the phone for rendering, without composing; the saved layout itself is not changed. */
 void refreshSources(){
  if(reading)return;reading=true;
  final Set<String> pinned=pinnedIds();
  worker.execute(()->{
   WorkspaceSources.Read read=null;
   try{read=WorkspaceSources.read(app,pinned);}catch(Throwable ignored){}
   final WorkspaceSources.Read done=read;
   main.post(()->{reading=false;if(done!=null){lastRead=done;if(!composer.busy()&&listener!=null)listener.changed(null);}});
  });
 }

 private void changed(String timed){save();if(listener!=null)listener.changed(timed);}

 private void save(){
  try{prefs().edit().putString(KEY,state.toJson()).putLong(REVISION,Math.max(prefs().getLong(REVISION,0),state.revision())).apply();}
  catch(RuntimeException ignored){}
 }
 private SharedPreferences prefs(){return app.getSharedPreferences(PREFS,Context.MODE_PRIVATE);}
}
