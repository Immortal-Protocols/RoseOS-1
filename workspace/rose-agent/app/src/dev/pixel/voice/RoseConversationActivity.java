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
 * muting or ending voice never touches tasks. Notices are exactly Connected, Muted, Unmuted, Call ended. */
public final class RoseConversationActivity extends Activity {
 // Light ivory/celadon Fold treatment, with a matching dark palette so system-bar icons stay legible.
 static int c(int light,int dark){return PixelAppearance.dark()?dark:light;}
 static int paper(){return c(0xfff4f1e8,0xff142019);} static int ink(){return c(0xff23342c,0xffe6eee8);}
 static int celadon(){return c(0xffb9d3c2,0xff2f4a3c);} static int leaf(){return c(0xff2f4a3c,0xffb9d3c2);}
 static final int NOTICE=0xff1f3329;
 final Handler handler=new Handler(Looper.getMainLooper());
 final ConversationState.Notices notices=new ConversationState.Notices();
 ConversationState state;
 Presence presence;TextView notice,micLabel;FrameLayout mic;TextView micGlyph;TextView end;
 final Runnable tick=new Runnable(){public void run(){refresh();handler.postDelayed(this,150);}};
 final Runnable hideNotice=()->{if(reducedMotion())notice.setVisibility(View.GONE);else notice.animate().translationY(-dp(96)).alpha(0).setDuration(260).withEndAction(()->notice.setVisibility(View.GONE)).start();};

 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
 boolean reducedMotion(){return Settings.Global.getFloat(getContentResolver(),Settings.Global.ANIMATOR_DURATION_SCALE,1f)==0f;}

 @Override protected void onCreate(Bundle b){
  PixelAppearance.attach(this);super.onCreate(b);PixelAppearance.bars(this);
  setVolumeControlStream(android.media.AudioManager.STREAM_VOICE_CALL);
  int paper=paper();getWindow().setStatusBarColor(paper);getWindow().setNavigationBarColor(paper);
  FrameLayout root=new FrameLayout(this);root.setBackgroundColor(paper);
  presence=new Presence(this);root.addView(presence,new FrameLayout.LayoutParams(dp(240),dp(240),Gravity.CENTER));

  notice=new TextView(this);notice.setTextSize(15);notice.setTextColor(0xfff4f1e8);notice.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
  notice.setPadding(dp(20),dp(10),dp(20),dp(10));notice.setBackground(pill(NOTICE));notice.setVisibility(View.GONE);
  notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
  FrameLayout.LayoutParams np=new FrameLayout.LayoutParams(-2,-2,Gravity.TOP|Gravity.CENTER_HORIZONTAL);np.topMargin=dp(56);root.addView(notice,np);

  LinearLayout controls=new LinearLayout(this);controls.setOrientation(LinearLayout.VERTICAL);controls.setGravity(Gravity.CENTER_HORIZONTAL);
  mic=new FrameLayout(this);mic.setBackground(circle(celadon()));mic.setElevation(dp(3));mic.setClickable(true);mic.setFocusable(true);
  micGlyph=new TextView(this);micGlyph.setTextSize(30);micGlyph.setTextColor(ink());micGlyph.setGravity(Gravity.CENTER);
  micGlyph.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);mic.addView(micGlyph,new FrameLayout.LayoutParams(-1,-1));
  mic.setOnClickListener(v->primary());controls.addView(mic,new LinearLayout.LayoutParams(dp(84),dp(84)));
  micLabel=new TextView(this);micLabel.setTextSize(13);micLabel.setTextColor(leaf());micLabel.setGravity(Gravity.CENTER);
  micLabel.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-2,dp(28));lp.topMargin=dp(8);controls.addView(micLabel,lp);
  end=new TextView(this);end.setText("End");end.setTextSize(15);end.setTextColor(leaf());end.setGravity(Gravity.CENTER);
  end.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));end.setBackground(pill(0x00000000));end.setPadding(dp(28),0,dp(28),0);
  end.setContentDescription("End voice conversation");end.setOnClickListener(v->send("end"));
  LinearLayout.LayoutParams ep=new LinearLayout.LayoutParams(-2,dp(48));ep.topMargin=dp(4);controls.addView(end,ep);
  FrameLayout.LayoutParams cp=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.CENTER_HORIZONTAL);cp.bottomMargin=dp(36);root.addView(controls,cp);
  setContentView(root);
 }
 GradientDrawable pill(int color){GradientDrawable g=new GradientDrawable();g.setColor(color);g.setCornerRadius(dp(24));if(color==0)g.setStroke(dp(1),celadon());return g;}
 GradientDrawable circle(int color){GradientDrawable g=new GradientDrawable();g.setShape(GradientDrawable.OVAL);g.setColor(color);return g;}

 @Override protected void onResume(){super.onResume();notices.reset();handler.removeCallbacks(tick);handler.post(tick);}
 @Override protected void onPause(){handler.removeCallbacks(tick);handler.removeCallbacks(hideNotice);notice.animate().cancel();notice.setVisibility(View.GONE);super.onPause();}

 void refresh(){
  VoiceService s=VoiceService.instance;
  state=s==null?ConversationState.of(false,false,false,false,false,null):ConversationState.of(true,s.live,s.connected,s.muted,s.communicating,s.status);
  String n=notices.observe(state);if(n!=null)showNotice(n);
  boolean active=state.active();
  micGlyph.setText(active&&state.muted?"🔇":"🎙");
  micLabel.setText(state.phase==ConversationState.Phase.ERROR?"Couldn’t connect":active&&state.muted?"Muted":"");
  mic.setContentDescription(active?(state.muted?"Unmute microphone":"Mute microphone"):state.phase==ConversationState.Phase.CONNECTING?"Starting voice conversation":"Start voice conversation");
  mic.setEnabled(state.primaryCommand()!=null);mic.setAlpha(state.primaryCommand()==null?.6f:1f);
  end.setVisibility(state.canEnd()?View.VISIBLE:View.INVISIBLE);
  presence.update(state,s==null||!active?0f:s.trayOutputLevel,reducedMotion());
 }
 void showNotice(String text){
  handler.removeCallbacks(hideNotice);notice.animate().cancel();notice.setText(text);notice.setVisibility(View.VISIBLE);
  if(reducedMotion()){notice.setTranslationY(0);notice.setAlpha(1);}
  else{notice.setTranslationY(-dp(96));notice.setAlpha(0);notice.animate().translationY(0).alpha(1).setDuration(280).start();}
  handler.postDelayed(hideNotice,1800);
 }
 void primary(){
  String c=state==null?null:state.primaryCommand();if(c==null)return;
  if("talk".equals(c)&&checkSelfPermission("android.permission.RECORD_AUDIO")!=PackageManager.PERMISSION_GRANTED){
   requestPermissions(new String[]{"android.permission.RECORD_AUDIO","android.permission.POST_NOTIFICATIONS"},1);return;}
  send(c);
 }
 @Override public void onRequestPermissionsResult(int r,String[] p,int[] g){super.onRequestPermissionsResult(r,p,g);
  if(r==1&&checkSelfPermission("android.permission.RECORD_AUDIO")==PackageManager.PERMISSION_GRANTED)send("talk");}
 /** Same delivery as MainActivity.command and the tray: VoiceService performs it or reports failure through its state. */
 void send(String action){startForegroundService(new Intent(this,VoiceService.class).setAction(action));}

 /** Simple folded presence: overlapping translucent petals around an aperture. Opening follows the
  * session (closed when idle or muted, open when listening); Rose's output level articulates the edges.
  * Visual only; it never creates session state. */
 static final class Presence extends View {
  final Paint petal=new Paint(Paint.ANTI_ALIAS_FLAG),core=new Paint(Paint.ANTI_ALIAS_FLAG);
  float open,target,voice;ValueAnimator anim;
  Presence(android.content.Context c){super(c);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);core.setColor(0x332f4a3c);}
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
    c.save();c.rotate(a,cx,cy);c.drawOval(new RectF(cx-r*.42f,cy-r-spread*.2f,cx+r*.42f,cy+spread),petal);c.restore();
   }
   c.drawCircle(cx,cy,r*(.10f+.16f*open),core);
  }
 }
}
