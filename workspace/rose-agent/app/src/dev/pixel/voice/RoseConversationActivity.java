package dev.pixel.voice;

import android.animation.ValueAnimator;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.*;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.provider.Settings;
import android.view.*;
import android.widget.*;

/** P2 voice-only conversation surface. Owns no voice engine: it renders VoiceService's state and sends
 * the same commands as the launcher and tray (talk, tray_mute, tray_unmute, end). Leaving, locking,
 * muting or ending voice never touches tasks. Notices are exactly Connected, Muted, Unmuted, Call ended.
 * Layout follows Rose-Fold-v3.html (SHA-256 67bf7a50…05ee): labelled call controls (Speak/Cancel,
 * Mute/Unmute, End call) and a light notice card entering from the top. */
public final class RoseConversationActivity extends Activity {
 // Prototype palette: paper #fafbf7, ink #283e39, control #e7eddf, end #aa4b3a; dark variants keep bar icons legible.
 static int c(int light,int dark){return PixelAppearance.dark()?dark:light;}
 static int paper(){return c(0xfffafbf7,0xff142019);} static int ink(){return c(0xff283e39,0xffe6eee8);}
 static int control(){return c(0xffe7eddf,0xff2f4a3c);} static int line(){return c(0xffe0e6d8,0xff3c5a4b);}
 static int noticeBg(){return c(0xfffffff6,0xff1f3329);} static int endInk(){return c(0xffaa4b3a,0xffefa786);}
 final Handler handler=new Handler(Looper.getMainLooper());
 final ConversationState.Notices notices=new ConversationState.Notices();
 ConversationState state;
 Presence presence;LinearLayout notice;TextView noticeIcon,noticeText,failure;
 LinearLayout content;ScrollView presentedScroll;TextView taskEyebrow,taskTitle,taskBody,taskNote;String lastTaskKey="",shownTaskId="";
 // Watch/Stop for the selected task (APP-P5-001). Controller fields come from its get_state provider.
 LinearLayout taskActions;TextView watch,stopTask,stopNote;
 volatile String ctlStatus="",ctlSession="",ctlTask="";volatile boolean ctlPolling;long ctlPolledAt;boolean watchOpening;
 String stopPendingFor,stopMessage="";boolean stopInFlight;
 Control primary,end;
 int noticeSerial;
 final Runnable tick=new Runnable(){public void run(){refresh();handler.postDelayed(this,150);}};

 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
 boolean reducedMotion(){return Settings.Global.getFloat(getContentResolver(),Settings.Global.ANIMATOR_DURATION_SCALE,1f)==0f;}

 /** One call control: round icon above a short label, as in the prototype's .call-control. */
 final class Control extends LinearLayout {
  final TextView icon,label;
  Control(int labelColor){super(RoseConversationActivity.this);setOrientation(VERTICAL);setGravity(Gravity.CENTER_HORIZONTAL);setClickable(true);setFocusable(true);
   icon=new TextView(getContext());icon.setTextSize(20);icon.setGravity(Gravity.CENTER);icon.setTextColor(ink());
   GradientDrawable g=new GradientDrawable();g.setShape(GradientDrawable.OVAL);g.setColor(control());g.setStroke(dp(1),line());icon.setBackground(g);
   icon.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);addView(icon,new LayoutParams(dp(56),dp(56)));
   label=new TextView(getContext());label.setTextSize(13);label.setTextColor(labelColor);label.setGravity(Gravity.CENTER);label.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
   label.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);LayoutParams lp=new LayoutParams(-2,-2);lp.topMargin=dp(6);addView(label,lp);
   setPadding(dp(14),dp(6),dp(14),dp(6));setMinimumWidth(dp(88));}
  void show(String glyph,String text,String description){icon.setText(glyph);label.setText(text);setContentDescription(description);}
 }

 @Override protected void onCreate(Bundle b){
  PixelAppearance.attach(this);super.onCreate(b);PixelAppearance.bars(this);
  setVolumeControlStream(android.media.AudioManager.STREAM_VOICE_CALL);
  getWindow().setStatusBarColor(paper());getWindow().setNavigationBarColor(paper());
  FrameLayout root=new FrameLayout(this);root.setBackgroundColor(paper());
  // Presence plus the presented output of the current task (Rose-Fold-v3 .presented): eyebrow, title, body.
  content=new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);content.setGravity(Gravity.CENTER);
  presence=new Presence(this);content.addView(presence,new LinearLayout.LayoutParams(dp(240),dp(240)));
  presentedScroll=new ScrollView(this);presentedScroll.setVisibility(View.GONE);
  LinearLayout presented=new LinearLayout(this);presented.setOrientation(LinearLayout.VERTICAL);presented.setPadding(dp(24),dp(8),dp(24),dp(16));
  taskEyebrow=presentedText(13,eyebrow(),"sans-serif-medium");presented.addView(taskEyebrow);
  taskTitle=presentedText(26,ink(),"sans-serif-medium");taskTitle.setAccessibilityHeading(true);taskTitle.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
  LinearLayout.LayoutParams ttp=new LinearLayout.LayoutParams(-1,-2);ttp.topMargin=dp(10);presented.addView(taskTitle,ttp);
  taskBody=presentedText(16,ink(),"sans-serif");taskBody.setLineSpacing(dp(6),1);
  LinearLayout.LayoutParams tbp=new LinearLayout.LayoutParams(-1,-2);tbp.topMargin=dp(14);presented.addView(taskBody,tbp);
  taskNote=presentedText(13,eyebrow(),"sans-serif");LinearLayout.LayoutParams tnp=new LinearLayout.LayoutParams(-1,-2);tnp.topMargin=dp(12);presented.addView(taskNote,tnp);
  stopNote=presentedText(14,ink(),"sans-serif");stopNote.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
  LinearLayout.LayoutParams snp=new LinearLayout.LayoutParams(-1,-2);snp.topMargin=dp(12);presented.addView(stopNote,snp);
  taskActions=new LinearLayout(this);taskActions.setGravity(Gravity.START);
  watch=taskAction("View workspace",ink());watch.setContentDescription("View this task's background workspace");watch.setOnClickListener(v->openWorkspace());taskActions.addView(watch);
  stopTask=taskAction("Stop task",endInk());stopTask.setOnClickListener(v->confirmStop());
  LinearLayout.LayoutParams sp=new LinearLayout.LayoutParams(-2,-2);sp.leftMargin=dp(12);taskActions.addView(stopTask,sp);
  LinearLayout.LayoutParams tap=new LinearLayout.LayoutParams(-1,-2);tap.topMargin=dp(16);presented.addView(taskActions,tap);
  presentedScroll.addView(presented);content.addView(presentedScroll,new LinearLayout.LayoutParams(-1,0,1f));
  FrameLayout.LayoutParams cp=new FrameLayout.LayoutParams(-1,-1);cp.topMargin=statusInset()+dp(56);cp.bottomMargin=dp(190);root.addView(content,cp);

  notice=new LinearLayout(this);notice.setGravity(Gravity.CENTER_VERTICAL);notice.setPadding(dp(18),dp(12),dp(18),dp(12));notice.setMinimumHeight(dp(56));
  GradientDrawable nb=new GradientDrawable();nb.setColor(noticeBg());nb.setCornerRadius(dp(24));nb.setStroke(dp(1),line());notice.setBackground(nb);notice.setElevation(dp(6));
  noticeIcon=new TextView(this);noticeIcon.setTextSize(18);noticeIcon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);notice.addView(noticeIcon);
  noticeText=new TextView(this);noticeText.setTextSize(15);noticeText.setTextColor(ink());noticeText.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
  LinearLayout.LayoutParams tp=new LinearLayout.LayoutParams(-2,-2);tp.leftMargin=dp(12);notice.addView(noticeText,tp);
  notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);notice.setVisibility(View.GONE);
  FrameLayout.LayoutParams np=new FrameLayout.LayoutParams(-1,-2,Gravity.TOP);np.leftMargin=np.rightMargin=dp(18);np.topMargin=dp(12)+statusInset();root.addView(notice,np);

  LinearLayout bottom=new LinearLayout(this);bottom.setOrientation(LinearLayout.VERTICAL);bottom.setGravity(Gravity.CENTER_HORIZONTAL);
  failure=new TextView(this);failure.setTextSize(14);failure.setTextColor(ink());failure.setGravity(Gravity.CENTER);
  failure.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);bottom.addView(failure,new LinearLayout.LayoutParams(-2,dp(28)));
  LinearLayout row=new LinearLayout(this);row.setGravity(Gravity.CENTER);
  primary=new Control(ink());primary.setOnClickListener(v->primary());row.addView(primary);
  end=new Control(endInk());end.show("📞","End call","End voice conversation");end.setOnClickListener(v->send("end"));row.addView(end);
  LinearLayout.LayoutParams rp=new LinearLayout.LayoutParams(-2,-2);rp.topMargin=dp(8);bottom.addView(row,rp);
  FrameLayout.LayoutParams bp=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.CENTER_HORIZONTAL);bp.bottomMargin=dp(32);root.addView(bottom,bp);
  TextView more=new TextView(this);more.setText("⋯");more.setTextSize(24);more.setTextColor(ink());more.setGravity(Gravity.CENTER);more.setContentDescription("More options");
  more.setOnClickListener(v->{PopupMenu m=new PopupMenu(this,v);m.getMenu().add("Update Rose Agent").setOnMenuItemClickListener(i->{checkForUpdate();return true;});m.show();});
  FrameLayout.LayoutParams mp=new FrameLayout.LayoutParams(dp(48),dp(48),Gravity.TOP|Gravity.END);mp.topMargin=statusInset()+dp(4);mp.rightMargin=dp(8);root.addView(more,mp);
  setContentView(root);
 }
 TextView taskAction(String text,int color){TextView t=presentedText(15,color,"sans-serif-medium");t.setText(text);t.setGravity(Gravity.CENTER);t.setMinHeight(dp(44));
  t.setPadding(dp(16),0,dp(16),0);GradientDrawable g=new GradientDrawable();g.setCornerRadius(dp(22));g.setStroke(dp(1),line());t.setBackground(g);t.setClickable(true);t.setFocusable(true);return t;}
 static int eyebrow(){return c(0xff677c6f,0xffb6ccbc);}
 TextView presentedText(int size,int color,String family){TextView t=new TextView(this);t.setTextSize(size);t.setTextColor(color);t.setTypeface(Typeface.create(family,Typeface.NORMAL));return t;}
 int statusInset(){int id=getResources().getIdentifier("status_bar_height","dimen","android");return id>0?getResources().getDimensionPixelSize(id):dp(24);}

 @Override protected void onResume(){super.onResume();notices.reset();handler.removeCallbacks(tick);handler.post(tick);}
 @Override protected void onPause(){handler.removeCallbacks(tick);clearNotice();super.onPause();}

 void refresh(){
  VoiceService s=VoiceService.instance;
  state=s==null?ConversationState.of(false,false,false,false,false,null,null,null)
   :ConversationState.of(true,s.live,s.connected,s.muted,s.communicating,s.lastEndReason,s.lastStartError,s.backendReachable);
  String n=notices.observe(state);
  if(ConversationState.CLEAR.equals(n))clearNotice();else if(n!=null)showNotice(n);
  boolean active=state.active();
  if(active)primary.show(state.muted?"🔇":"🎙",state.muted?"Unmute":"Mute",state.muted?"Microphone muted. Unmute":"Microphone on. Mute");
  else if(state.phase==ConversationState.Phase.CONNECTING)primary.show("📞","Cancel","Cancel voice connection");
  else primary.show("🎙","Speak","Start voice conversation");
  end.setVisibility(state.canEnd()?View.VISIBLE:View.GONE);
  failure.setText(state.failureText());
  presence.update(state,s==null||!active?0f:s.trayOutputLevel,reducedMotion());
  renderTask(s);
 }
 /** The current task from VoiceService's copy of the server's task projection, which outlives this
  * screen, voice sessions and navigation; returning here shows the same task. Read-only: no Stop. */
 void renderTask(VoiceService s){
  String raw=s==null?null:s.taskStateJson;
  boolean available=s!=null&&s.taskStateAvailable&&(s.taskStateStreamConnected||SystemClock.elapsedRealtime()-s.taskStateReceivedAt<5000);
  pollController();
  String key=raw+"|"+available+"|"+ctlStatus+"|"+ctlSession+"|"+ctlTask+"|"+stopPendingFor+"|"+stopMessage+"|"+stopInFlight;if(key.equals(lastTaskKey))return;lastTaskKey=key;
  TaskPresentation t=TaskPresentation.NONE;String state="";
  if(raw!=null)try{org.json.JSONObject cur=new org.json.JSONObject(raw).optJSONObject("current");
   if(cur!=null){state=cur.optString("state");t=TaskPresentation.of(true,cur.optString("id"),state,cur.optString("title"),cur.optString("activity"),cur.optString("progress"),cur.optString("result"),available);}
  }catch(org.json.JSONException e){t=TaskPresentation.NONE;}
  // A pending Stop ends only on that task's own authoritative state, or when the task is gone/replaced.
  if(stopPendingFor!=null&&TaskControl.settled(stopPendingFor,t.visible?t.id:null,state)){stopPendingFor=null;stopMessage="";}
  if(!t.visible||!t.id.equals(shownTaskId))if(stopPendingFor==null&&!stopInFlight)stopMessage="";
  LinearLayout.LayoutParams pp=(LinearLayout.LayoutParams)presence.getLayoutParams();int size=dp(t.visible?132:240);
  if(pp.width!=size){pp.width=pp.height=size;presence.setLayoutParams(pp);}
  presentedScroll.setVisibility(t.visible?View.VISIBLE:View.GONE);
  if(!t.visible){shownTaskId="";return;}
  if(!t.id.equals(shownTaskId)){shownTaskId=t.id;presentedScroll.scrollTo(0,0);}
  taskEyebrow.setText(t.eyebrow);taskTitle.setText(t.title);
  taskBody.setVisibility(t.body.isEmpty()?View.GONE:View.VISIBLE);if(!t.body.isEmpty())TaskText.set(taskBody,t.body);
  taskNote.setVisibility(t.note.isEmpty()?View.GONE:View.VISIBLE);taskNote.setText(t.note);
  boolean running=available&&(state.equals("running")||state.equals("checking"));
  boolean canWatch=TaskControl.watchable(t.id,t.active(),ctlStatus,ctlSession,ctlTask);
  boolean canStop=running&&stopPendingFor==null&&!stopInFlight;
  watch.setVisibility(canWatch?View.VISIBLE:View.GONE);stopTask.setVisibility(canStop?View.VISIBLE:View.GONE);
  taskActions.setVisibility(canWatch||canStop?View.VISIBLE:View.GONE);
  stopNote.setText(stopInFlight?"Sending Stop…":stopMessage);stopNote.setVisibility(stopInFlight||!stopMessage.isEmpty()?View.VISIBLE:View.GONE);
 }
 /** Reads the background controller's session (same-signer provider) about once a second, off the UI thread. */
 void pollController(){
  if(ctlPolling||SystemClock.uptimeMillis()-ctlPolledAt<1000)return;ctlPolling=true;ctlPolledAt=SystemClock.uptimeMillis();
  new Thread(()->{String[] c=controllerState();ctlStatus=c[0];ctlSession=c[1];ctlTask=c[2];ctlPolling=false;},"rose-controller-state").start();
 }
 /** {status, sessionId, taskId} from the controller's get_state; empty strings when unavailable. Off the UI thread. */
 String[] controllerState(){String st="",se="",tk="";
  try{Bundle reply=getContentResolver().call(android.net.Uri.parse("content://dev.pixel.backgroundcontrol.agent"),"get_state",null,null);
   org.json.JSONObject c=new org.json.JSONObject(reply==null?"{}":reply.getString("result","{}"));
   st=c.optString("status");se=c.isNull("sessionId")?"":c.optString("sessionId");tk=c.isNull("taskId")?"":c.optString("taskId");
  }catch(Exception unavailable){/* No controller, or not permitted: nothing to watch. */}
  return new String[]{st,se,tk};
 }
 /** Opens the existing viewer bound to the selected task: the task id is captured at the tap, the controller
  * is read fresh, and the viewer receives that task id with its own session id (TaskControl.watchBinding), so
  * it can refuse to show a session that is gone or now carries another task. Opening never stops anything. */
 void openWorkspace(){
  final String selected=shownTaskId;if(selected.isEmpty()||watchOpening)return;watchOpening=true;
  new Thread(()->{final String[] c=controllerState();runOnUiThread(()->{
   watchOpening=false;ctlStatus=c[0];ctlSession=c[1];ctlTask=c[2];lastTaskKey="";
   if(isFinishing()||isDestroyed())return;
   String[] binding=TaskControl.watchBinding(selected,selected.equals(shownTaskId),c[0],c[1],c[2]);
   if(binding==null){stopMessage="That task’s workspace is no longer available.";return;}
   try{startActivity(new Intent().setComponent(new android.content.ComponentName("dev.pixel.backgroundcontrol","dev.pixel.backgroundcontrol.AgentWorkspaceActivity"))
    .putExtra(TaskControl.EXTRA_WATCH_TASK_ID,binding[0]).putExtra(TaskControl.EXTRA_WATCH_SESSION_ID,binding[1]).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));}
   catch(Exception e){stopMessage="The workspace viewer couldn’t open.";}
  });},"rose-watch-open").start();
 }
 /** Confirms Stop for the task shown now. The id is captured here, so a later task is never stopped by this dialog. */
 void confirmStop(){
  final String id=shownTaskId;final String title=taskTitle.getText().toString();if(id.isEmpty())return;
  new android.app.AlertDialog.Builder(this).setTitle("Stop this task?").setMessage(title+"\n\nThis stops only this task. Your voice conversation is not affected.")
   .setNegativeButton("Cancel",null).setPositiveButton("Stop task",(d,w)->sendStop(id)).show();
 }
 /** The existing authenticated Owner Stop: POST /task/stop {taskId}. Its answer is shown as it is. */
 void sendStop(String id){
  VoiceService s=VoiceService.instance;stopInFlight=true;lastTaskKey="";
  new Thread(()->{int status=0;org.json.JSONObject r=new org.json.JSONObject();
   if(s!=null&&s.ssl!=null&&s.endpoint!=null)try{
    javax.net.ssl.HttpsURLConnection c=(javax.net.ssl.HttpsURLConnection)new java.net.URL(s.endpoint+"/task/stop").openConnection();
    try{c.setSSLSocketFactory(s.ssl);c.setHostnameVerifier((h,x)->true);c.setConnectTimeout(6000);c.setReadTimeout(12000);c.setRequestMethod("POST");
     c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);
     byte[] body=new org.json.JSONObject().put("taskId",id).put("source","conversation").toString().getBytes("UTF-8");c.setFixedLengthStreamingMode(body.length);
     try(java.io.OutputStream o=c.getOutputStream()){o.write(body);}
     status=c.getResponseCode();if(status==200)r=new org.json.JSONObject(VoiceService.read(c.getInputStream()));}
    finally{c.disconnect();}
   }catch(Exception e){status=0;android.util.Log.w("RoseStop","stop failed: "+e.getClass().getSimpleName());}
   TaskControl result=TaskControl.stopResult(status,r.optString("status",null),r.optString("taskId",null),r.optString("worker",null),r.optString("workspace",null),r.optBoolean("duplicate"),id);
   runOnUiThread(()->{stopInFlight=false;
    boolean pending=result.outcome==TaskControl.Outcome.STOPPING||result.outcome==TaskControl.Outcome.ALREADY_STOPPING;
    stopPendingFor=pending?id:null;stopMessage=result.message;lastTaskKey="";});
  },"rose-task-stop").start();
 }
 void showNotice(String text){
  int serial=++noticeSerial;handler.removeCallbacksAndMessages(notice);notice.animate().cancel();
  noticeText.setText(text);noticeIcon.setText(text.equals(ConversationState.MUTED)?"🔇":text.equals(ConversationState.CALL_ENDED)?"📞":"🎙");
  notice.setVisibility(View.VISIBLE);
  if(reducedMotion()){notice.setTranslationY(0);notice.setAlpha(1);}
  else{notice.setTranslationY(-dp(96));notice.setAlpha(0);notice.animate().translationY(0).alpha(1).setDuration(220).start();}
  handler.postAtTime(()->{if(serial!=noticeSerial)return;
   if(reducedMotion())notice.setVisibility(View.GONE);
   else notice.animate().translationY(-dp(96)).alpha(0).setDuration(220).withEndAction(()->{if(serial==noticeSerial)notice.setVisibility(View.GONE);}).start();
  },notice,SystemClock.uptimeMillis()+2200);
 }
 /** Withdraws any notice and its pending dismissal; a newer notice is never hidden by an older timer. */
 void clearNotice(){noticeSerial++;handler.removeCallbacksAndMessages(notice);notice.animate().cancel();notice.setVisibility(View.GONE);}
 void primary(){
  String cmd=state==null?null:state.primaryCommand();if(cmd==null)return;
  if("talk".equals(cmd)&&checkSelfPermission("android.permission.RECORD_AUDIO")!=PackageManager.PERMISSION_GRANTED){
   requestPermissions(new String[]{"android.permission.RECORD_AUDIO","android.permission.POST_NOTIFICATIONS"},1);return;}
  send(cmd);
 }
 @Override public void onRequestPermissionsResult(int r,String[] p,int[] g){super.onRequestPermissionsResult(r,p,g);
  if(r==1&&checkSelfPermission("android.permission.RECORD_AUDIO")==PackageManager.PERMISSION_GRANTED)send("talk");}
 /** Same delivery as MainActivity.command and the tray: VoiceService performs it or reports failure through its state.
  * A new attempt withdraws the previous session's notice at once rather than waiting for the next poll. */
 void send(String action){if("talk".equals(action))clearNotice();startForegroundService(new Intent(this,VoiceService.class).setAction(action));}

 /** "Update Rose Agent": one authenticated GET /apps over the voice service's existing pinned connection.
  * An available newer version can be installed only by tapping Install (SelfUpdate admission, then the
  * service's verified delivery); checking alone never downloads or installs. */
 void checkForUpdate(){
  android.app.AlertDialog dialog=new android.app.AlertDialog.Builder(this).setTitle("Update Rose Agent").setMessage("Checking for updates…")
   .setNegativeButton("Close",null).setNeutralButton("Check again",null).setPositiveButton("Install",null).create();
  dialog.setOnShowListener(d->{Button again=dialog.getButton(android.app.AlertDialog.BUTTON_NEUTRAL),install=dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE);
   install.setVisibility(View.GONE);again.setOnClickListener(v->runUpdateCheck(dialog,again,install));runUpdateCheck(dialog,again,install);});
  dialog.show();
 }
 void runUpdateCheck(android.app.AlertDialog dialog,Button again,Button install){
  dialog.setMessage("Checking for updates…");again.setEnabled(false);install.setVisibility(View.GONE);
  boolean busy=state!=null&&(state.active()||state.phase==ConversationState.Phase.CONNECTING);
  String self=getPackageName();long installed;
  try{installed=getPackageManager().getPackageInfo(self,0).getLongVersionCode();}catch(Exception e){installed=-1;}
  final long version=installed;VoiceService s=VoiceService.instance;
  new Thread(()->{
   int status=0;java.util.List<UpdateCheck.Record> apps=null;
   if(s!=null&&s.ssl!=null&&s.endpoint!=null)try{
    javax.net.ssl.HttpsURLConnection c=(javax.net.ssl.HttpsURLConnection)new java.net.URL(s.endpoint+"/apps").openConnection();
    try{c.setSSLSocketFactory(s.ssl);c.setHostnameVerifier((h,x)->true);c.setConnectTimeout(6000);c.setReadTimeout(15000);
     status=c.getResponseCode();if(status==200)apps=parseApps(VoiceService.read(c.getInputStream()));}
    finally{c.disconnect();}
   }catch(Exception e){status=0;android.util.Log.w("RoseUpdate","check failed: "+e.getClass().getSimpleName());}
   UpdateCheck result=UpdateCheck.evaluate(status,apps,version,self,busy);
   runOnUiThread(()->{if(!dialog.isShowing())return;
    String attempt=selfUpdateText(result.offered,version);dialog.setMessage(result.message+(attempt.isEmpty()?"":"\n\n"+attempt));again.setEnabled(true);
    if(result.state==UpdateCheck.State.AVAILABLE&&result.offeredId!=null){install.setVisibility(View.VISIBLE);install.setOnClickListener(v->startSelfUpdate(dialog,install,result));}});
  },"rose-update-check").start();
 }
 /** The owner's explicit Install: admitted by the service (voice idle, newer, not already installing, install
  * capability present), then followed from its recorded state until the dialog closes. */
 void startSelfUpdate(android.app.AlertDialog dialog,Button install,UpdateCheck offer){
  VoiceService s=VoiceService.instance;if(s==null){dialog.setMessage("Rose Agent’s voice service isn’t running, so nothing was installed.");return;}
  SelfUpdate.Admission a=s.installSelf(offer.offeredId,offer.offered);
  dialog.setMessage(SelfUpdate.admissionText(a));if(a!=SelfUpdate.Admission.START)return;
  install.setVisibility(View.GONE);
  Runnable follow=new Runnable(){public void run(){if(!dialog.isShowing())return;
   long installed;try{installed=getPackageManager().getPackageInfo(getPackageName(),0).getLongVersionCode();}catch(Exception e){installed=-1;}
   String text=selfUpdateText(offer.offered,installed);if(!text.isEmpty())dialog.setMessage(text);content.postDelayed(this,500);}};
  content.postDelayed(follow,500);
 }
 /** The recorded attempt for this offered version only; an earlier attempt for another version says nothing. */
 String selfUpdateText(long offered,long installed){
  android.content.SharedPreferences p=getSharedPreferences("self-update",0);
  return p.getLong("target",-1)==offered?SelfUpdate.attemptText(offered,p.getString("state",""),installed):"";
 }
 /** {apps:[...]} → records, or null when the body is not that shape. Unknown fields (e.g. deviceId) are ignored. */
 static java.util.List<UpdateCheck.Record> parseApps(String body){
  try{org.json.JSONArray a=new org.json.JSONObject(body).optJSONArray("apps");if(a==null)return null;
   java.util.List<UpdateCheck.Record> out=new java.util.ArrayList<>();
   for(int i=0;i<a.length();i++){org.json.JSONObject o=a.optJSONObject(i);if(o==null)continue;
    out.add(new UpdateCheck.Record(str(o,"id"),str(o,"label"),str(o,"packageName"),o.opt("versionCode"),str(o,"sha256")));}
   return out;}catch(Exception e){return null;}
 }
 static String str(org.json.JSONObject o,String k){Object v=o.opt(k);return v instanceof String?(String)v:null;}

 /** Simple folded presence: overlapping translucent petals around an aperture. Opening follows the
  * session (closed when idle or muted, open when listening); Rose's output level articulates the edges.
  * Visual only; it never creates session state. */
 static final class Presence extends View {
  final Paint petal=new Paint(Paint.ANTI_ALIAS_FLAG),core=new Paint(Paint.ANTI_ALIAS_FLAG);final RectF oval=new RectF();
  float open,target,voice;ValueAnimator anim;
  Presence(android.content.Context c){super(c);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);core.setColor(0x33283e39);}
  void update(ConversationState s,float output,boolean reduced){
   float next=s.active()&&!s.muted?1f:s.phase==ConversationState.Phase.CONNECTING?.45f:0f;
   float v=reduced?0:Math.min(1f,output*3f);boolean voiceChanged=v!=voice;voice=v;
   if(next!=target){target=next;if(anim!=null)anim.cancel();
    if(reduced){open=target;invalidate();}
    else{anim=ValueAnimator.ofFloat(open,target).setDuration(420);anim.addUpdateListener(a->{open=(float)a.getAnimatedValue();invalidate();});anim.start();}}
   else if(voiceChanged)invalidate();
  }
  @Override protected void onDraw(Canvas c){
   float cx=getWidth()/2f,cy=getHeight()/2f,r=Math.min(cx,cy)*.62f;
   for(int i=0;i<5;i++){
    float a=i*72f+open*12f,spread=r*(.30f+.22f*open)+r*.06f*voice*(i%2==0?1:-1);
    petal.setColor(i%2==0?0x66b9d3c2:0x55e8e2cf);
    c.save();c.rotate(a,cx,cy);oval.set(cx-r*.42f,cy-r-spread*.2f,cx+r*.42f,cy+spread);c.drawOval(oval,petal);c.restore();
   }
   c.drawCircle(cx,cy,r*(.10f+.16f*open),core);
  }
 }
}
