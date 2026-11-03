package dev.pixel.voice;

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
 // #410 Oxblood (qa/oxblood/oxblood-contract.json "ink", "rose"): white and 78% white ink on the dark Rose field,
 // hairlines rgba(241,233,225,.22), the ruby x of Disconnect; the surface is dark in either system theme.
 static int c(int light,int dark){return dark;}
 static int paper(){return 0xff120a0d;} static int ink(){return OxbloodRose.INK;} static int ink2(){return OxbloodRose.INK_2;}
 static int control(){return 0x24f1e9e1;} static int line(){return OxbloodRose.LINE;}
 static int noticeBg(){return 0xf03a141b;} static int endInk(){return OxbloodRose.PillGlyph.X;}
 final Handler handler=new Handler(Looper.getMainLooper());
 final ConversationState.Notices notices=new ConversationState.Notices();
 ConversationState state;
 RosePearl pearl;View pearlView;TextView caption,prompt;FrameLayout work,connect,mic;OxbloodRose.WorkButton workGlyph;OxbloodRose.PillGlyph connectGlyph;TextView connectLabel;LinearLayout notice;TextView noticeIcon,noticeText,failure;
 LinearLayout content;ScrollView presentedScroll;TextView taskEyebrow,taskTitle,taskBody,taskNote;String lastTaskKey="",shownTaskId="";boolean shownTaskActive;
 // Watch/Stop for the selected task (APP-P5-001). Controller fields come from its get_state provider.
 LinearLayout taskActions;TextView watch,stopTask,stopNote;
 volatile TaskControl.ControllerRead ctl=TaskControl.ControllerRead.PENDING_READ;volatile long resumedAt;boolean workShown;volatile boolean ctlPolling;long ctlPolledAt;boolean watchOpening;
 String stopPendingFor,stopMessage="";boolean stopInFlight;
 int headTop;
 int noticeSerial;
 final Runnable tick=new Runnable(){public void run(){refresh();handler.postDelayed(this,150);}};

 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
 boolean reducedMotion(){return Settings.Global.getFloat(getContentResolver(),Settings.Global.ANIMATOR_DURATION_SCALE,1f)==0f;}

 @Override protected void onCreate(Bundle b){
  PixelAppearance.attach(this);super.onCreate(b);
  setVolumeControlStream(android.media.AudioManager.STREAM_VOICE_CALL);
  // #410: the Oxblood Rose field (OxbloodField.ROSE) in either system theme, with light bar icons over it.
  getWindow().setStatusBarColor(0xff120a0d);getWindow().setNavigationBarColor(0xff12060a);
  View decor=getWindow().getDecorView();decor.setSystemUiVisibility(decor.getSystemUiVisibility()&~(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR));
  float density=getResources().getDisplayMetrics().density;
  FrameLayout root=new FrameLayout(this);
  root.setBackground(new OxbloodField(OxbloodField.ROSE));
  root.setClipChildren(false); // the pearl's breath and the work entry's glow may draw past their small views
  headTop=Math.max(dp(49),statusInset()+dp(8));
  // Header (oxblood.css:391-398,504): the 16dp pearl and "Rose" at 23sp, 30dp in; the pearl draws the listening ring
  // and speaking breath from the real visual state; the title is the screen's heading.
  LinearLayout head=new LinearLayout(this);head.setGravity(Gravity.CENTER_VERTICAL);head.setClipChildren(false);
  pearl=new RosePearl(density,16);pearlView=new View(this);pearlView.setBackground(pearl);pearlView.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
  head.addView(pearlView,new LinearLayout.LayoutParams(dp(28),dp(28)));
  TextView title=presentedText(23,ink(),"sans-serif");title.setText("Rose");title.setAccessibilityHeading(true);title.setIncludeFontPadding(false);
  LinearLayout.LayoutParams tlp=new LinearLayout.LayoutParams(-2,-2);tlp.leftMargin=dp(7);head.addView(title,tlp);
  FrameLayout.LayoutParams hp=new FrameLayout.LayoutParams(-2,dp(44),Gravity.TOP|Gravity.START);hp.topMargin=headTop;hp.leftMargin=dp(24);root.addView(head,hp);
  // Upper right (oxblood.css:406-410,500-503): the Background Work entry, lit only while Rose's controller reports a
  // running workspace (ready or launching), never from a timer or a guess.
  work=new FrameLayout(this);workGlyph=new OxbloodRose.WorkButton(density);work.setBackground(workGlyph);
  work.setClickable(true);work.setFocusable(true);work.setContentDescription("Open Rose’s background work");work.setOnClickListener(v->openOverview());
  work.setStateListAnimator(HomeAppTile.pressAnimator(this,View.TRANSLATION_Y,0,dp(1),0));work.setForeground(HomeAppTile.focusRing(density,12));
  // 48dp targets around the reference's 44dp control (its square is drawn 3dp in from the 44, centred).
  work.setPadding(dp(2),dp(2),dp(2),dp(2));
  FrameLayout.LayoutParams wp=new FrameLayout.LayoutParams(dp(48),dp(48),Gravity.TOP|Gravity.END);wp.topMargin=headTop-dp(2);wp.rightMargin=dp(16);root.addView(work,wp);
  // App Core's More menu (Update Rose Agent, Settings) keeps its own place, just left of the work entry.
  TextView more=new TextView(this);more.setText("⋯");more.setTextSize(24);more.setTextColor(ink());more.setGravity(Gravity.CENTER);more.setContentDescription("More options");
  more.setOnClickListener(v->{PopupMenu m=new PopupMenu(this,v);m.getMenu().add("Update Rose Agent").setOnMenuItemClickListener(i->{checkForUpdate();return true;});
   m.getMenu().add("Settings").setOnMenuItemClickListener(i->{startActivity(new Intent(MainActivity.ACTION_SETTINGS).setClass(this,MainActivity.class));return true;});m.show();});
  FrameLayout.LayoutParams mp=new FrameLayout.LayoutParams(dp(48),dp(48),Gravity.TOP|Gravity.END);mp.topMargin=headTop-dp(2);mp.rightMargin=dp(16+48);root.addView(more,mp);
  // Caption (app.js:350): 15sp white, 88dp under the header's top; it is the visible state, the controls speak it.
  caption=presentedText(15,ink(),"sans-serif");caption.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  FrameLayout.LayoutParams capp=new FrameLayout.LayoutParams(-2,-2,Gravity.TOP|Gravity.START);capp.topMargin=headTop+dp(88);capp.leftMargin=dp(30);root.addView(caption,capp);
  // Output (oxblood.css:400-405): from 121dp under the header to 110dp above the bottom, 30dp in; the empty prompt or
  // the current task's presentation (eyebrow, title, body, note, its Watch/Stop actions).
  content=new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);
  prompt=presentedText(26,ink(),"sans-serif");prompt.setText("What would you like to do?");prompt.setLetterSpacing(-.005f);prompt.setLineSpacing(0,1.2f);
  LinearLayout.LayoutParams prp=new LinearLayout.LayoutParams(-1,-2);prp.topMargin=dp(8);content.addView(prompt,prp);
  presentedScroll=new ScrollView(this);presentedScroll.setVisibility(View.GONE);presentedScroll.setVerticalScrollBarEnabled(false);
  LinearLayout presented=new LinearLayout(this);presented.setOrientation(LinearLayout.VERTICAL);presented.setPadding(0,0,0,dp(16));
  taskEyebrow=presentedText(13,eyebrow(),"sans-serif-medium");presented.addView(taskEyebrow);
  taskTitle=presentedText(ConversationPresentation.TITLE_SP,ink(),"sans-serif");taskTitle.setLetterSpacing(-.02f);taskTitle.setLineSpacing(0,1.08f);taskTitle.setAccessibilityHeading(true);taskTitle.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
  LinearLayout.LayoutParams ttp=new LinearLayout.LayoutParams(-1,-2);ttp.topMargin=dp(6);presented.addView(taskTitle,ttp);
  taskBody=presentedText(ConversationPresentation.BODY_SP,ink2(),"sans-serif");taskBody.setLineSpacing(0,1.4f);taskBody.setLetterSpacing(-.01f);
  LinearLayout.LayoutParams tbp=new LinearLayout.LayoutParams(-1,-2);tbp.topMargin=dp(24);presented.addView(taskBody,tbp);
  taskNote=presentedText(13,eyebrow(),"sans-serif");LinearLayout.LayoutParams tnp=new LinearLayout.LayoutParams(-1,-2);tnp.topMargin=dp(12);presented.addView(taskNote,tnp);
  stopNote=presentedText(14,ink(),"sans-serif");stopNote.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
  LinearLayout.LayoutParams snp=new LinearLayout.LayoutParams(-1,-2);snp.topMargin=dp(12);presented.addView(stopNote,snp);
  taskActions=new LinearLayout(this);taskActions.setGravity(Gravity.START);
  watch=taskAction("View workspace",ink());watch.setContentDescription("View this task's background workspace");watch.setOnClickListener(v->openWorkspace());taskActions.addView(watch);
  stopTask=taskAction("Stop task",endInk());stopTask.setOnClickListener(v->confirmStop());
  LinearLayout.LayoutParams sp=new LinearLayout.LayoutParams(-2,-2);sp.leftMargin=dp(12);taskActions.addView(stopTask,sp);
  LinearLayout.LayoutParams tap=new LinearLayout.LayoutParams(-1,-2);tap.topMargin=dp(16);presented.addView(taskActions,tap);
  presentedScroll.addView(presented);content.addView(presentedScroll,new LinearLayout.LayoutParams(-1,0,1f));
  FrameLayout.LayoutParams cp=new FrameLayout.LayoutParams(-1,-1);cp.topMargin=headTop+dp(121);cp.bottomMargin=dp(110);cp.leftMargin=dp(30);cp.rightMargin=dp(30);root.addView(content,cp);

  notice=new LinearLayout(this);notice.setGravity(Gravity.CENTER_VERTICAL);notice.setPadding(dp(18),dp(12),dp(18),dp(12));notice.setMinimumHeight(dp(48));
  GradientDrawable nb=new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,new int[]{0xf03a141b,0xf0220b0f});nb.setCornerRadius(dp(26));nb.setStroke(Math.max(1,dp(1)),0x38ecd5c6);notice.setBackground(nb);notice.setElevation(dp(6));
  noticeIcon=new TextView(this);noticeIcon.setTextSize(18);noticeIcon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);notice.addView(noticeIcon);
  noticeText=new TextView(this);noticeText.setTextSize(15);noticeText.setTextColor(0xfff2e6d9);noticeText.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
  LinearLayout.LayoutParams tp=new LinearLayout.LayoutParams(-2,-2);tp.leftMargin=dp(12);notice.addView(noticeText,tp);
  notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);notice.setVisibility(View.GONE);
  FrameLayout.LayoutParams np=new FrameLayout.LayoutParams(-2,-2,Gravity.TOP|Gravity.END);np.rightMargin=dp(24);np.topMargin=headTop+dp(69);root.addView(notice,np);

  // Bottom (oxblood.css:260-267,411-414,505-508): the one Connect/Disconnect control centred 42dp above the bottom
  // (plus the navigation inset), its failure line above it. Connect starts Rose's call (talk, after the microphone
  // permission); Disconnect ends it or cancels connecting (end). Neither touches background work.
  failure=new TextView(this);failure.setTextSize(14);failure.setTextColor(ink2());failure.setGravity(Gravity.CENTER);
  failure.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
  FrameLayout.LayoutParams fp=new FrameLayout.LayoutParams(-1,dp(28),Gravity.BOTTOM);fp.bottomMargin=dp(42+44+6);fp.leftMargin=fp.rightMargin=dp(30);root.addView(failure,fp);
  connect=new FrameLayout(this);connect.setBackground(new OxbloodRose.Pill(density));connect.setClickable(true);connect.setFocusable(true);
  connect.setMinimumWidth(dp(154));connect.setPadding(dp(26),0,dp(26),0);
  LinearLayout connectRow=new LinearLayout(this);connectRow.setGravity(Gravity.CENTER);connectRow.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
  connectGlyph=new OxbloodRose.PillGlyph(density);View glyph=new View(this);glyph.setBackground(connectGlyph);connectRow.addView(glyph,new LinearLayout.LayoutParams(dp(15),dp(15)));
  connectLabel=presentedText(15,ink(),"sans-serif");LinearLayout.LayoutParams clp=new LinearLayout.LayoutParams(-2,-2);clp.leftMargin=dp(11);connectRow.addView(connectLabel,clp);
  connect.addView(connectRow,new FrameLayout.LayoutParams(-2,-1,Gravity.CENTER));
  connect.setOnClickListener(v->connectTapped());
  connect.setStateListAnimator(HomeAppTile.pressAnimator(this,View.TRANSLATION_Y,0,dp(1),320));connect.setForeground(HomeAppTile.focusRing(density,10));
  // Centred, and never under Rose's mic control at the left: equal side margins that clear it, one-line label.
  connectLabel.setSingleLine(true);connectLabel.setEllipsize(android.text.TextUtils.TruncateAt.END);
  FrameLayout.LayoutParams pp=new FrameLayout.LayoutParams(-2,dp(48),Gravity.BOTTOM|Gravity.CENTER_HORIZONTAL);pp.bottomMargin=dp(40);pp.leftMargin=pp.rightMargin=dp(30+48+8);root.addView(connect,pp);
  // Rose's own call microphone (not an OS-wide control, #410): shown only while the call is live, named as Rose's.
  mic=new FrameLayout(this);mic.setBackground(new OxbloodEdge.Well(density));mic.setClickable(true);mic.setFocusable(true);
  TextView micGlyph=presentedText(18,ink(),"sans-serif");micGlyph.setGravity(Gravity.CENTER);micGlyph.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);micGlyph.setTag("mic-glyph");
  mic.addView(micGlyph,new FrameLayout.LayoutParams(-1,-1));mic.setOnClickListener(v->{if(state!=null&&state.active())send(state.muted?"tray_unmute":"tray_mute");});
  mic.setForeground(HomeAppTile.focusRing(density,OxbloodEdge.Well.RADIUS_DP));
  FrameLayout.LayoutParams mcp=new FrameLayout.LayoutParams(dp(48),dp(48),Gravity.BOTTOM|Gravity.START);mcp.bottomMargin=dp(40);mcp.leftMargin=dp(30);root.addView(mic,mcp);
  root.setOnApplyWindowInsetsListener((v,i)->{int nav=i.getSystemWindowInsetBottom();
   // Side insets (a landscape navigation bar, a cutout): every control lays out inside them; the field stays full-bleed.
   int left=i.getSystemWindowInsetLeft(),right=i.getSystemWindowInsetRight();
   if(i.getDisplayCutout()!=null){left=Math.max(left,i.getDisplayCutout().getSafeInsetLeft());right=Math.max(right,i.getDisplayCutout().getSafeInsetRight());}
   if(v.getPaddingLeft()!=left||v.getPaddingRight()!=right)v.setPadding(left,0,right,0);
   for(View c:new View[]{connect,mic,failure}){FrameLayout.LayoutParams l=(FrameLayout.LayoutParams)c.getLayoutParams();int base=c==failure?dp(42+44+6):dp(40);if(l.bottomMargin!=base+nav){l.bottomMargin=base+nav;c.setLayoutParams(l);}}
   FrameLayout.LayoutParams l=(FrameLayout.LayoutParams)content.getLayoutParams();if(l.bottomMargin!=dp(110)+nav){l.bottomMargin=dp(110)+nav;content.setLayoutParams(l);}
   return i;});
  overview=new WorkspaceOverviewPanel(this,new OxbloodField(OxbloodField.ROSE),statusInset());
  overview.bind(overviewSource(),overviewActions());overview.setIcons(this::appIcon);overview.setLabels(this::appLabel);
  root.addView(overview,new FrameLayout.LayoutParams(-1,-1));
  setContentView(root);
 }
 TextView taskAction(String text,int color){TextView t=presentedText(15,color,"sans-serif-medium");t.setText(text);t.setGravity(Gravity.CENTER);t.setMinHeight(dp(44));
  t.setPadding(dp(16),0,dp(16),0);GradientDrawable g=new GradientDrawable();g.setColor(0x8c0e080b);g.setCornerRadius(dp(10));g.setStroke(Math.max(1,dp(1)),0x38f1e9e1);t.setBackground(g);t.setClickable(true);t.setFocusable(true);return t;}
 static int eyebrow(){return OxbloodRose.INK_2;}
 TextView presentedText(int size,int color,String family){TextView t=new TextView(this);t.setTextSize(size);t.setTextColor(color);t.setTypeface(Typeface.create(family,Typeface.NORMAL));return t;}
 int statusInset(){int id=getResources().getIdentifier("status_bar_height","dimen","android");return id>0?getResources().getDimensionPixelSize(id):dp(24);}

 /** True between onResume and onPause: the presence animates only while this screen is visible. */
 boolean resumed;
 @Override protected void onResume(){super.onResume();resumed=true;resumedAt=SystemClock.uptimeMillis();ctlPolledAt=0;notices.reset();handler.removeCallbacks(tick);handler.post(tick);if(overview!=null)overview.resume();}
 @Override protected void onPause(){resumed=false;handler.removeCallbacks(tick);stopBreath();if(overview!=null){if(isFinishing())overview.teardown();else overview.pause();}clearNotice();super.onPause();}

 void refresh(){
  VoiceService s=VoiceService.instance;
  state=s==null?ConversationState.of(false,false,false,false,false,null,null,null)
   :ConversationState.of(true,s.live,s.connected,s.muted,s.communicating,s.lastEndReason,s.lastStartError,s.backendReachable);
  String n=notices.observe(state);
  if(ConversationState.CLEAR.equals(n))clearNotice();else if(n!=null)showNotice(n);
  boolean live=state.active()||state.phase==ConversationState.Phase.CONNECTING;
  connectGlyph.set(live);
  String label=live?"Disconnect":"Connect";if(!label.contentEquals(connectLabel.getText()))connectLabel.setText(label);
  connect.setContentDescription(live?(state.phase==ConversationState.Phase.CONNECTING?"Cancel connecting to Rose":"Disconnect from Rose"):"Connect to Rose");
  mic.setVisibility(state.active()?View.VISIBLE:View.GONE);
  ((TextView)mic.findViewWithTag("mic-glyph")).setText(state.muted?"🔇":"🎙");
  mic.setContentDescription(state.muted?"Rose’s microphone is muted. Unmute":"Mute Rose’s microphone");
  failure.setText(state.failureText());
  // Core's RoseVisualState maps the session and VoiceService's own measured levels (stale or absent levels are
  // NaN, never invented) into the presence's three independent axes; this screen only draws them.
  long now=SystemClock.elapsedRealtime(),seen=s==null?0L:s.trayAudioObservedAt;
  RoseVisualState visual=RoseVisualState.from(state,s==null?Float.NaN:RoseVisualState.fresh(s.trayInputLevel,seen,now),
   s==null?Float.NaN:RoseVisualState.fresh(s.trayOutputLevel,seen,now),reducedMotion(),resumed);
  showPearl(visual);
  String cap=OxbloodRose.caption(state,visual);if(!cap.contentEquals(caption.getText()))caption.setText(cap);
  // The pearl is the screen's spoken state (the caption is its visible twin, hidden so it isn't read twice).
  String said=RosePresenceModel.describe(visual);if(!said.contentEquals(pearlView.getContentDescription()==null?"":pearlView.getContentDescription()))pearlView.setContentDescription(said);
  // Working while at least one of Rose's sessions, in any slot, is authoritatively running (status exactly ready; #410
  // rose.workButton.working), the same definition as Background Work's running edge, from a read begun since this
  // screen last resumed: never while launching, waiting or holding a failed close, and never from a stale read.
  // Until the first read begun since the resume arrives, what was shown is held briefly (TaskControl.WORK_HOLD_MS).
  workShown=TaskControl.workShown(ctl,resumedAt,SystemClock.uptimeMillis(),workShown);workGlyph.set(workShown,reducedMotion());
  renderTask(s);
  if(overview!=null&&overview.isOpen()){pollApps();overview.refresh();}
 }
 /** The current task from VoiceService's copy of the server's task projection, which outlives this
  * screen, voice sessions and navigation; returning here shows the same task. Read-only: no Stop. */
 void renderTask(VoiceService s){
  String raw=s==null?null:s.taskStateJson;
  boolean available=s!=null&&s.taskStateAvailable&&(s.taskStateStreamConnected||SystemClock.elapsedRealtime()-s.taskStateReceivedAt<5000);
  pollController();
  String key=raw+"|"+available+"|"+ctl.key()+"|"+stopPendingFor+"|"+stopMessage+"|"+stopInFlight;if(key.equals(lastTaskKey))return;lastTaskKey=key;
  TaskPresentation t=TaskPresentation.NONE;String state="";
  if(raw!=null)try{org.json.JSONObject cur=new org.json.JSONObject(raw).optJSONObject("current");
   if(cur!=null){state=cur.optString("state");t=TaskPresentation.of(true,cur.optString("id"),state,cur.optString("title"),cur.optString("activity"),cur.optString("progress"),cur.optString("result"),available);}
  }catch(org.json.JSONException e){t=TaskPresentation.NONE;}
  // A pending Stop ends only on that task's own authoritative state, or when the task is gone/replaced.
  if(stopPendingFor!=null&&TaskControl.settled(stopPendingFor,t.visible?t.id:null,state)){stopPendingFor=null;stopMessage="";}
  if(!t.visible||!t.id.equals(shownTaskId))if(stopPendingFor==null&&!stopInFlight)stopMessage="";
  android.content.res.Configuration cfg=getResources().getConfiguration();
  ConversationPresentation lay=ConversationPresentation.of(cfg.screenHeightDp-Math.round(statusInset()/getResources().getDisplayMetrics().density),t.visible,cfg.fontScale);
  taskTitle.setTextSize(lay.titleSp);taskBody.setTextSize(lay.bodySp);
  prompt.setVisibility(t.visible?View.GONE:View.VISIBLE);
  presentedScroll.setVisibility(t.visible?View.VISIBLE:View.GONE);
  if(!t.visible){shownTaskId="";shownTaskActive=false;return;}
  if(!t.id.equals(shownTaskId)){shownTaskId=t.id;presentedScroll.scrollTo(0,0);}
  taskEyebrow.setText(t.eyebrow);taskTitle.setText(t.title);
  taskBody.setVisibility(t.body.isEmpty()?View.GONE:View.VISIBLE);if(!t.body.isEmpty())TaskText.set(taskBody,t.body);
  taskNote.setVisibility(t.note.isEmpty()?View.GONE:View.VISIBLE);taskNote.setText(t.note);
  boolean running=available&&(state.equals("running")||state.equals("checking"));
  shownTaskActive=t.active();
  TaskControl.WatchState ws=TaskControl.watchState(t.id,t.active(),ctl);boolean canWatch=ws.canWatch;
  boolean canStop=running&&stopPendingFor==null&&!stopInFlight;
  watch.setVisibility(canWatch?View.VISIBLE:View.GONE);stopTask.setVisibility(canStop?View.VISIBLE:View.GONE);
  taskActions.setVisibility(canWatch||canStop?View.VISIBLE:View.GONE);
  stopNote.setText(stopInFlight?"Sending Stop…":stopMessage);stopNote.setVisibility(stopInFlight||!stopMessage.isEmpty()?View.VISIBLE:View.GONE);
 }
 /** Reads the background controller's session (same-signer provider) about once a second, off the UI thread. */
 void pollController(){
  if(ctlPolling||SystemClock.uptimeMillis()-ctlPolledAt<1000)return;ctlPolling=true;ctlPolledAt=SystemClock.uptimeMillis();
  new Thread(()->{ctl=controllerState();ctlPolling=false;},"rose-controller-state").start();
 }
 static final String CONTROLLER_AUTHORITY="dev.pixel.backgroundcontrol.agent";
 /** One get_state read of the controller (same-signer provider), classified by TaskControl.readController: a
  * validated status (idle = no workspace running) or a fixed failure category (absent provider, denied, malformed
  * answer, other failure). No exception text is kept. Read-only: never starts a task or workspace. Off the UI thread. */
 TaskControl.ControllerRead controllerState(){
  final long startedAt=SystemClock.uptimeMillis();final java.util.List<String[]> slots=new java.util.ArrayList<>();final boolean[] summarized={false};
  return TaskControl.readController(()->{
   android.content.ContentProviderClient client=getContentResolver().acquireUnstableContentProviderClient(CONTROLLER_AUTHORITY);
   if(client==null)throw new TaskControl.NoProvider();
   try{Bundle reply=client.call("get_state",null,null);
    String raw=reply==null?null:reply.getString("result");if(raw==null)return null;
    org.json.JSONObject c;try{c=new org.json.JSONObject(raw);}catch(org.json.JSONException e){throw new TaskControl.InvalidReply();}
    String[] f={stateField(c,"status"),stateField(c,"sessionId"),stateField(c,"taskId")};
    // The overview card names the workspace's app; targetPackage is optional and never makes the read malformed.
    Object pkg=c.opt("targetPackage");TaskControl.ControllerRead r=TaskControl.ControllerRead.of(f[0],f[1],f[2]);
    ctlPackage=new String[]{r.key(),pkg instanceof String?(String)pkg:""};
    // The same reply's routed sessions summary (every slot, read together by the controller): only each slot's session
    // id and status, for the work button's "any session ready". Absent on an older single-session controller.
    org.json.JSONArray a=c.optJSONArray("sessions");
    if(a!=null){summarized[0]=true;for(int i=0;i<a.length()&&i<8;i++){org.json.JSONObject x=a.optJSONObject(i);if(x!=null)slots.add(new String[]{jsonString(x,"sessionId"),jsonString(x,"status")});}}
    return f;
   }finally{client.close();}
  }).withSessions(summarized[0]?slots:null,startedAt);
 }
 /** A get_state field as read: null when absent or JSON null; any non-string value makes the answer malformed. */
 static String stateField(org.json.JSONObject c,String key)throws TaskControl.InvalidReply{
  if(!c.has(key)||c.isNull(key))return null;Object v=c.opt(key);
  if(!(v instanceof String))throw new TaskControl.InvalidReply();return (String)v;
 }
 /** Opens the existing viewer bound to the selected task: the task id is captured at the tap, the controller
  * is read fresh, and the viewer receives that task id with its own session id (TaskControl.watchBinding), so
  * it can refuse to show a session that is gone or now carries another task. Opening never stops anything. */
 void openWorkspace(){
  final String selected=shownTaskId;final boolean active=shownTaskActive;if(watchOpening)return;watchOpening=true;
  new Thread(()->{final TaskControl.ControllerRead c=controllerState();runOnUiThread(()->{
   watchOpening=false;ctl=c;lastTaskKey="";
   if(isFinishing()||isDestroyed())return;
   // Never a silent tap: from the fresh read, say why Watch can't open (App Core's fixed reasons). When it can, the
   // bound route below is unchanged and still re-checks that the tapped task is the one shown.
   TaskControl.WatchState ws=TaskControl.watchState(selected,active,c);
   if(!ws.canWatch){Toast.makeText(this,ws.reason,Toast.LENGTH_LONG).show();return;}
   String[] binding=TaskControl.watchBinding(selected,selected.equals(shownTaskId),c);
   if(binding==null){stopMessage="That task’s workspace is no longer available.";return;}
   if(!launchBoundViewer(binding[0],binding[1]))stopMessage="The workspace viewer couldn’t open.";
  });},"rose-watch-open").start();
 }
 /** The one viewer launch on this screen, always bound: the viewer attaches only while the controller's task id
  * and session id both still equal these extras. Returns false when it couldn't open. */
 boolean launchBoundViewer(String controllerTaskId,String sessionId){
  try{startActivity(new Intent().setComponent(new android.content.ComponentName("dev.pixel.backgroundcontrol","dev.pixel.backgroundcontrol.AgentWorkspaceActivity"))
    .putExtra(TaskControl.EXTRA_WATCH_TASK_ID,controllerTaskId).putExtra(TaskControl.EXTRA_WATCH_SESSION_ID,sessionId).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));return true;}
  catch(Exception e){return false;}
 }

 // ---------- Managed workspace overview (MANAGED-WORKSPACE-OVERVIEW-001) ----------
 WorkspaceOverviewPanel overview;
 /** {read key, targetPackage} of the latest controller read, published together: the package is used only for the
  * read whose key it was taken with. */
 volatile String[] ctlPackage={"",""};
 /** Re-entry starts from a fresh read: the last visit's reply is dropped first, so nothing it listed comes back. */
 void openOverview(){appsEpoch++;appsSnapshot=null;appsPolledAt=0;if(overview!=null)overview.open();}
 /** Back closes the overview first; the work, its selection and the human's foreground app are unchanged. */
 @Override public void onBackPressed(){if(overview!=null&&overview.isOpen()){overview.close();return;}super.onBackPressed();}
 /** Live: the local Rose connection is up; its tasks come only from App Core's onLocalTasks list (PR321). */
 boolean localLive(){VoiceService s=VoiceService.instance;return s!=null&&s.connected&&s.live;}
 WorkspaceOverview.Workspace overviewWorkspace(TaskControl.ControllerRead r,WorkspaceOverview.Scope scope){
  if(r==null)return WorkspaceOverview.Workspace.UNREADABLE;
  if(r.failure==TaskControl.ReadFailure.PENDING)return WorkspaceOverview.Workspace.CHECKING;
  if(r.failure!=TaskControl.ReadFailure.NONE)return WorkspaceOverview.Workspace.UNREADABLE;
  String[] p=ctlPackage;String pkg=r.key().equals(p[0])?p[1]:"";
  return WorkspaceOverview.Workspace.read(r.status,r.sessionId,r.taskId,pkg,appLabel(pkg),scope);
 }
 /** The app's visible label for the card; empty (shown as "an app") when it can't be read. Never logged. Cached for
  * the last package, so the 150 ms refresh asks the package manager once per workspace app. */
 String[] labelFor={"",""};
 String appLabel(String pkg){
  if(pkg.isEmpty())return "";
  String[] l=labelFor;if(pkg.equals(l[0]))return l[1];
  String label;try{label=String.valueOf(getPackageManager().getApplicationLabel(getPackageManager().getApplicationInfo(pkg,0)));}catch(Exception e){label="";}
  labelFor=new String[]{pkg,label};return label;
 }
 /** The workspace app's real launcher icon for its window card (null when it can't be read); cached with its package. */
 Object[] iconFor={"",null};
 android.graphics.drawable.Drawable appIcon(String pkg){
  Object[] l=iconFor;if(pkg.equals(l[0]))return (android.graphics.drawable.Drawable)l[1];
  android.graphics.drawable.Drawable d;try{d=getPackageManager().getApplicationIcon(pkg);}catch(Exception e){d=null;}
  iconFor=new Object[]{pkg,d};return d;
 }
 /** One read-only controller preview (#225, PR340 Controller.preview) for exactly the request's session and controller
  * task, on this phone only. Off the UI thread. The JPEG is decoded only when within PR340's bounds and its declared size;
  * nothing is stored, logged or sent anywhere, and any failure is an answer with no image (the card keeps its text). */
 WorkspaceOverviewPreview.Response readPreview(WorkspaceOverviewPreview.Request r){
  try{
   android.content.ContentProviderClient client=getContentResolver().acquireUnstableContentProviderClient(CONTROLLER_AUTHORITY);
   if(client==null)return WorkspaceOverviewPreview.Response.failed();
   try{
    Bundle extras=new Bundle();extras.putString("params",new org.json.JSONObject().put("sessionId",r.sessionId()).put("taskId",r.taskId()).toString());
    Bundle reply=client.call("preview",null,extras);String raw=reply==null?null:reply.getString("result");
    if(raw==null||raw.length()>WorkspaceOverviewPreview.MAX_BASE64+4096)return WorkspaceOverviewPreview.Response.failed();
    org.json.JSONObject o=new org.json.JSONObject(raw);String status=o.optString("status","");
    if(!"ok".equals(status))return new WorkspaceOverviewPreview.Response(status,null,null,null,0,0,0,null,0,null);
    String b64=o.optString("imageBase64","");int w=o.optInt("width"),h=o.optInt("height");android.graphics.Bitmap image=null;int bytes=0;
    if(!b64.isEmpty()&&b64.length()<=WorkspaceOverviewPreview.MAX_BASE64){
     byte[] jpeg=android.util.Base64.decode(b64,android.util.Base64.NO_WRAP);bytes=jpeg.length;
     android.graphics.BitmapFactory.Options bounds=new android.graphics.BitmapFactory.Options();bounds.inJustDecodeBounds=true;
     android.graphics.BitmapFactory.decodeByteArray(jpeg,0,jpeg.length,bounds);
     if(bounds.outWidth==w&&bounds.outHeight==h&&w>0&&h>0&&w<=WorkspaceOverviewPreview.MAX_WIDTH&&h<=WorkspaceOverviewPreview.MAX_HEIGHT)
      image=android.graphics.BitmapFactory.decodeByteArray(jpeg,0,jpeg.length);
    }
    return new WorkspaceOverviewPreview.Response(status,o.optString("sessionId",""),o.optString("taskId",""),o.optString("targetPackage",""),
     o.optLong("capturedAt",0),w,h,o.optString("mimeType",""),bytes,image);
   }finally{client.close();}
  }catch(Exception e){return WorkspaceOverviewPreview.Response.failed();}
 }
 /** #225/#156: the apps currently under Rose's control, from the controller's phone-local call("current_apps")
  * (App Core PR355), read with the controller state about once a second while the overview is open. Each read is one
  * complete report and its own registration (reads are serial), so a controller restart or a late reply never brings
  * old apps back. Only an attested complete reply is shown as membership (CurrentApps.fromReply); anything else is the
  * explicit unavailable state. Task history is never consulted. */
 volatile CurrentApps.Snapshot appsSnapshot;long appsReads;volatile boolean appsPolling;long appsPolledAt;volatile int appsEpoch;
 /** One read in flight, about once a second, only while the overview is open (never otherwise). */
 void pollApps(){
  if(appsPolling||SystemClock.uptimeMillis()-appsPolledAt<1000)return;appsPolling=true;appsPolledAt=SystemClock.uptimeMillis();
  final int epoch=appsEpoch;
  new Thread(()->{CurrentApps.Snapshot s=readCurrentApps();if(epoch==appsEpoch)appsSnapshot=s;appsPolling=false;},"rose-current-apps").start();
 }
 CurrentApps.Snapshot currentApps(){
  CurrentApps.Snapshot s=appsSnapshot;
  return s!=null?s:CurrentApps.Snapshot.unavailable("pending",0,CurrentApps.NOT_REPORTED);
 }
 /** One current_apps read (off the UI thread). Failures are fixed words; nothing is logged. */
 CurrentApps.Snapshot readCurrentApps(){
  String reg="current_apps-"+(++appsReads);
  try{
   android.content.ContentProviderClient client=getContentResolver().acquireUnstableContentProviderClient(CONTROLLER_AUTHORITY);
   if(client==null)return CurrentApps.Snapshot.unavailable(reg,0,CurrentApps.NOT_INSTALLED);
   try{
    Bundle extras=new Bundle();extras.putString("params","{}");
    Bundle reply=client.call("current_apps",null,extras);String raw=reply==null?null:reply.getString("result");
    if(raw==null||raw.length()>65536)return CurrentApps.Snapshot.unavailable(reg,0,CurrentApps.UNREADABLE);
    org.json.JSONObject o=new org.json.JSONObject(raw);
    java.util.List<CurrentApps.ReplyApp> apps=null;org.json.JSONArray a=o.optJSONArray("apps");
    if(a!=null){apps=new java.util.ArrayList<>();for(int i=0;i<a.length()&&i<64;i++){org.json.JSONObject x=a.optJSONObject(i);
     apps.add(x==null?null:new CurrentApps.ReplyApp(x.optString("packageName",""),x.optBoolean("front"),x.optBoolean("target"),jsonString(x,"sessionId")));}}
    // The agreed two-app contract: owned_devices with sessions[] bindings (slot/bound/frontKnown). Membership is the
    // snapshot; bindings only give each app's state, live view and Stop.
    java.util.List<CurrentApps.Binding> bindings=bindings(o.optJSONArray("sessions"));
    if(CurrentApps.SOURCE_OWNED_DEVICES.equals(jsonString(o,"source"))&&bindings!=null)
     return CurrentApps.fromBindings(reg,jsonString(o,"status"),o.has("generation")?o.optLong("generation"):null,apps,bindings,jsonString(o,"controllerEpoch"),
      o.has("complete")&&!o.isNull("complete")?o.optBoolean("complete"):null);
    return CurrentApps.fromReply(reg,o.isNull("status")?null:o.optString("status",null),o.isNull("sessionId")?null:o.optString("sessionId",null),
     o.isNull("taskId")?null:o.optString("taskId",null),o.has("generation")?o.optLong("generation"):null,apps,
     o.isNull("instance")?null:o.optString("instance",null),o.isNull("sessionStatus")?null:o.optString("sessionStatus",null),
     o.has("complete")&&!o.isNull("complete")?o.optBoolean("complete"):null,o.isNull("source")?null:o.optString("source",null));
   }finally{client.close();}
  }catch(Exception e){return CurrentApps.Snapshot.unavailable(reg,0,CurrentApps.UNREADABLE);}
 }
 /** A JSON string field as read: null when absent, JSON null or not a string. */
 static String jsonString(org.json.JSONObject o,String key){Object v=o.opt(key);return v instanceof String?(String)v:null;}
 /** sessions[] as bindings ({slot, sessionId, taskId, targetPackage, sessionStatus, bound, frontKnown}); null when the
  * reply has no binding-shaped sessions (an older controller). */
 static java.util.List<CurrentApps.Binding> bindings(org.json.JSONArray a){
  if(a==null)return null;java.util.List<CurrentApps.Binding> out=new java.util.ArrayList<>();
  for(int i=0;i<a.length()&&i<8;i++){org.json.JSONObject x=a.optJSONObject(i);if(x==null||!x.has("bound")||!x.has("slot"))return null;
   out.add(new CurrentApps.Binding(x.optInt("slot",-1),jsonString(x,"sessionId"),jsonString(x,"taskId"),jsonString(x,"targetPackage"),jsonString(x,"sessionStatus"),
    x.optBoolean("bound"),x.optBoolean("frontKnown")));}
  return out;
 }
 /** owner_stop {sessionId, taskId} for exactly the captured pair (phone-local; the Mac's app_command refuses it). Its
  * confirmed flag is read as given; failures are no confirmation, never stopped. Nothing is logged. */
 Boolean ownerStopCall(String sessionId,String taskId){
  try{
   android.content.ContentProviderClient client=getContentResolver().acquireUnstableContentProviderClient(CONTROLLER_AUTHORITY);
   if(client==null)return null;
   try{
    Bundle extras=new Bundle();org.json.JSONObject p=new org.json.JSONObject().put("sessionId",sessionId).put("taskId",taskId==null||taskId.isEmpty()?org.json.JSONObject.NULL:taskId);
    extras.putString("params",p.toString());
    Bundle reply=client.call("owner_stop",null,extras);String raw=reply==null?null:reply.getString("result");if(raw==null||raw.length()>65536)return null;
    org.json.JSONObject o=new org.json.JSONObject(raw);return o.has("confirmed")&&!o.isNull("confirmed")?o.optBoolean("confirmed"):null;
   }finally{client.close();}
  }catch(Exception e){return null;}
 }
 /** The routed sessions summary (a get_state with no session): each slot's status, stopReason and stopped pair. */
 java.util.List<String[]> stopSummary(){
  java.util.List<String[]> out=new java.util.ArrayList<>();
  try{
   android.content.ContentProviderClient client=getContentResolver().acquireUnstableContentProviderClient(CONTROLLER_AUTHORITY);
   if(client==null)return out;
   try{
    Bundle extras=new Bundle();extras.putString("params","{}");
    Bundle reply=client.call("get_state",null,extras);String raw=reply==null?null:reply.getString("result");if(raw==null||raw.length()>65536)return out;
    org.json.JSONArray a=new org.json.JSONObject(raw).optJSONArray("sessions");
    if(a!=null)for(int i=0;i<a.length()&&i<8;i++){org.json.JSONObject x=a.optJSONObject(i);if(x==null)continue;
     out.add(new String[]{jsonString(x,"status"),jsonString(x,"stopReason"),jsonString(x,"stoppedSessionId"),jsonString(x,"stoppedTaskId")});}
   }finally{client.close();}
  }catch(Exception e){}
  return out;
 }
 WorkspaceOverviewPanel.Source overviewSource(){
  return new WorkspaceOverviewPanel.Source(){
   public boolean connected(){return localLive();}
   public CurrentApps.Snapshot currentApps(){return RoseConversationActivity.this.currentApps();}
   public boolean muted(){return state!=null&&state.active()&&state.muted;}
  };
 }
 /** Watch re-reads the controller after the tap and opens the existing viewer, bound by its extras, only while the
  * live session is still exactly the app's handle (session, controller task and package). Nothing is stopped. */
 WorkspaceOverviewPanel.Actions overviewActions(){
  return new WorkspaceOverviewPanel.Actions(){
   public void endCall(){send("end");}
   public boolean stopRoute(){return true;}
   public void ownerStop(final String sessionId,final String taskId){
    new Thread(()->{final Boolean confirmed=ownerStopCall(sessionId,taskId);
     runOnUiThread(()->{if(!isFinishing()&&!isDestroyed()&&overview!=null)overview.stopAnswer(sessionId,taskId,confirmed);});},"rose-owner-stop").start();
   }
   public void checkStops(){
    new Thread(()->{final java.util.List<String[]> entries=stopSummary();
     runOnUiThread(()->{if(isFinishing()||isDestroyed()||overview==null)return;for(String[] e:entries)overview.stopSummary(e[0],e[1],e[2],e[3]);});},"rose-stop-check").start();
   }
   public void requestPreview(final WorkspaceOverviewPreview.Request r){
    new Thread(()->{final WorkspaceOverviewPreview.Response a=readPreview(r);
     runOnUiThread(()->{if(!isFinishing()&&!isDestroyed()&&overview!=null)overview.previewAnswer(r,a);});},"rose-overview-preview").start();
   }
   public void watch(final CurrentApps.App app,final WorkspaceOverview.Binding atTap){
    if(watchOpening)return;watchOpening=true;
    new Thread(()->{final TaskControl.ControllerRead c=controllerState();runOnUiThread(()->{
     watchOpening=false;ctl=c;
     if(isFinishing()||isDestroyed())return;
     String[] p=ctlPackage;String pkg=c.key().equals(p[0])?p[1]:"";
     boolean still=c.failure==TaskControl.ReadFailure.NONE&&("ready".equals(c.status)||"blocked".equals(c.status))
      &&atTap.sessionId.equals(c.sessionId)&&atTap.controllerTaskId.equals(c.taskId)&&atTap.targetPackage.equals(pkg);
     if(!still){Toast.makeText(RoseConversationActivity.this,WorkspaceOverview.WATCH_MOVED_ON,Toast.LENGTH_LONG).show();overview.refresh();return;}
     if(!launchBoundViewer(atTap.controllerTaskId,atTap.sessionId))Toast.makeText(RoseConversationActivity.this,"The workspace viewer couldn’t open.",Toast.LENGTH_LONG).show();
    });},"rose-overview-watch").start();
   }
  };
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
 /** The single Connect/Disconnect control: Connect starts Rose's call (talk, after the microphone permission);
  * Disconnect ends a live call or cancels connecting (end). It never mutes, and ending never stops background work. */
 void connectTapped(){
  if(state==null)return;
  boolean live=state.active()||state.phase==ConversationState.Phase.CONNECTING;
  if(live){send("end");return;}
  if(checkSelfPermission("android.permission.RECORD_AUDIO")!=PackageManager.PERMISSION_GRANTED){
   requestPermissions(new String[]{"android.permission.RECORD_AUDIO","android.permission.POST_NOTIFICATIONS"},1);return;}
  send("talk");
 }
 /** The header pearl from the real visual state: silver while Rose's call is live and muted; the listening ring; the
  * speaking breath (1600ms ease-in-out, only while visible with animations on; otherwise its static ring). */
 void showPearl(RoseVisualState v){
  boolean silver=state!=null&&state.active()&&state.muted;
  boolean speaking=v.surround==RoseVisualState.Surround.SPEAKING,listening=v.surround==RoseVisualState.Surround.LISTENING;
  boolean breathing=speaking&&v.animate;
  pearl.breathe(breathing);
  if(!breathing)pearl.set(silver,listening,speaking?0f:-1f);else if(pearl.silver!=silver)pearl.set(silver,false,pearl.breath<0?0f:pearl.breath);
 }
 void stopBreath(){if(pearl!=null)pearl.breathe(false);}
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

 /** "Update Rose Agent": checks public pixel-ota GitHub releases directly (DirectUpdate), with no PixelVoice catalog,
  * pairing or credential. A compatible newer version can be installed only by tapping Install (SelfUpdate admission,
  * then the service's verified delivery); checking alone never downloads the APK or installs. */
 void checkForUpdate(){
  TextView title=new TextView(this);title.setPadding(dp(24),dp(20),dp(24),0);title.setTextSize(20);title.setText("Update Rose Agent · "+UpdateAttempts.channel(this).label+" ▾");title.setContentDescription("Update channel "+UpdateAttempts.channel(this).label+". Change channel");
  android.app.AlertDialog dialog=new android.app.AlertDialog.Builder(this).setCustomTitle(title).setMessage("Checking for updates…")
   .setNegativeButton("Close",null).setNeutralButton("Check again",null).setPositiveButton("Install",null).create();
  dialog.setOnShowListener(d->{Button again=dialog.getButton(android.app.AlertDialog.BUTTON_NEUTRAL),install=dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE);
   install.setVisibility(View.GONE);again.setOnClickListener(v->runUpdateCheck(dialog,again,install));runUpdateCheck(dialog,again,install);
   title.setOnClickListener(v->chooseUpdateChannel(title,()->runUpdateCheck(dialog,again,install)));});
  dialog.show();
 }
 /** The owner's update channel (UpdateAttempts): Alpha/Beta/Prod, kept as chosen; DirectUpdate reports an unpublished one
  * as unavailable without fetching it. */
 void chooseUpdateChannel(TextView title,Runnable recheck){
  DirectUpdate.Channel[] all=DirectUpdate.Channel.values();String[] labels=new String[all.length];
  for(int i=0;i<all.length;i++)labels[i]=all[i].label;
  new android.app.AlertDialog.Builder(this).setTitle("Update channel").setSingleChoiceItems(labels,UpdateAttempts.channel(this).ordinal(),(d,which)->{
   UpdateAttempts.setChannel(this,all[which]);title.setText("Update Rose Agent · "+all[which].label+" ▾");title.setContentDescription("Update channel "+all[which].label+". Change channel");d.dismiss();recheck.run();}).show();
 }
 void runUpdateCheck(android.app.AlertDialog dialog,Button again,Button install){
  dialog.setMessage("Checking for updates…");again.setEnabled(false);install.setVisibility(View.GONE);
  new Thread(()->{
   DirectUpdate.Device device=Delivery.localDevice(this);DirectUpdate.Result result;DirectUpdate.Channel channel=UpdateAttempts.channel(this);
   UpdateAttempts.recover(this,device.installed); // an interrupted attempt's own session and download
   try{result=DirectUpdate.check(new Delivery.PublicHttp(),device,channel);}
   catch(Exception e){result=new DirectUpdate.Result(DirectUpdate.State.ERROR,"Couldn’t check for updates ("+e.getClass().getSimpleName()+").",null);}
   final DirectUpdate.Result r=result;
   runOnUiThread(()->{if(!dialog.isShowing())return;
    boolean busy=state!=null&&(state.active()||state.phase==ConversationState.Phase.CONNECTING);
    String attempt=r.offer==null?"":selfUpdateText(r.offer.versionCode,device.installed);
    dialog.setMessage(r.message+(r.state==DirectUpdate.State.AVAILABLE&&busy?" Install after this call ends; nothing will interrupt it.":"")+(attempt.isEmpty()?"":"\n\n"+attempt));again.setEnabled(true);
    if(r.state==DirectUpdate.State.AVAILABLE&&r.offer!=null){install.setVisibility(View.VISIBLE);install.setOnClickListener(v->startSelfUpdate(dialog,install,r.offer));}});
  },"rose-update-check").start();
 }
 /** The owner's explicit Install: admitted by the service (voice idle, newer, not already installing, install
  * capability present), then followed from its recorded state until the dialog closes. */
 void startSelfUpdate(android.app.AlertDialog dialog,Button install,DirectUpdate.Offer offer){
  VoiceService s=VoiceService.instance;if(s==null){dialog.setMessage("Rose Agent’s service isn’t running, so nothing was installed.");return;}
  SelfUpdate.Admission a=s.installSelf(offer);
  dialog.setMessage(SelfUpdate.admissionText(a));if(a!=SelfUpdate.Admission.START)return;
  install.setVisibility(View.GONE);
  Runnable follow=new Runnable(){public void run(){if(!dialog.isShowing())return;
   long installed;try{installed=getPackageManager().getPackageInfo(getPackageName(),0).getLongVersionCode();}catch(Exception e){installed=-1;}
   String text=selfUpdateText(offer.versionCode,installed);if(!text.isEmpty())dialog.setMessage(text);content.postDelayed(this,500);}};
  content.postDelayed(follow,500);
 }
 /** The recorded attempt for this offered version only; an earlier attempt for another version says nothing. */
 String selfUpdateText(long offered,long installed){
  return UpdateAttempts.text(this,offered,installed);
 }

}
