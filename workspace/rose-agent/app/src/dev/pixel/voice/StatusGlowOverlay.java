package dev.pixel.voice;

import android.app.KeyguardManager;
import android.content.*;
import android.graphics.*;
import android.hardware.display.DisplayManager;
import android.os.*;
import android.provider.Settings;
import android.view.*;

/**
 * Stock-Android stand-in for the OS agent tray glow (os/overlays/.../AgentTrayPrototype.java).
 * Display-only: never touchable, never above native status/notification windows, and inert
 * while the platform SystemUI tray is registered or "Display over other apps" is not granted.
 */
final class StatusGlowOverlay implements DisplayManager.DisplayListener {
 private static final String TAG="AgentStatusWave";
 private final Context context;
 private final WindowManager windows;
 private final Handler handler=new Handler(Looper.getMainLooper());
 private boolean screenOff,started;
 private String shownState="";
 private Waves view;
 private final BroadcastReceiver screen=new BroadcastReceiver(){@Override public void onReceive(Context c,Intent intent){
  if(Intent.ACTION_SCREEN_OFF.equals(intent.getAction()))screenOff=true;
  else screenOff=false;
  reconcile();
 }};
 private final Runnable recheck=new Runnable(){public void run(){reconcile();handler.postDelayed(this,250);}};

 StatusGlowOverlay(Context source){
  context=source.createWindowContext(source.getSystemService(DisplayManager.class).getDisplay(Display.DEFAULT_DISPLAY),WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,null);
  windows=context.getSystemService(WindowManager.class);
 }

 static boolean supported(){return Build.VERSION.SDK_INT>=33;}

 void start(){
  if(started)return;started=true;
  screenOff=!context.getSystemService(PowerManager.class).isInteractive();
  context.getSystemService(DisplayManager.class).registerDisplayListener(this,handler);
  IntentFilter filter=new IntentFilter();filter.addAction(Intent.ACTION_SCREEN_OFF);filter.addAction(Intent.ACTION_SCREEN_ON);filter.addAction(Intent.ACTION_USER_PRESENT);
  context.registerReceiver(screen,filter);
  // Overlay permission, keyguard, platform-tray registration and voice state have no app callbacks.
  handler.post(recheck);
 }

 void stop(){
  if(!started)return;started=false;
  handler.removeCallbacksAndMessages(null);
  context.getSystemService(DisplayManager.class).unregisterDisplayListener(this);
  try{context.unregisterReceiver(screen);}catch(IllegalArgumentException ignored){}
  remove();
 }

 private void remove(){if(view!=null){windows.removeViewImmediate(view);view=null;}}

 private int dp(float value){return Math.round(value*context.getResources().getDisplayMetrics().density);}

 private void reconcile(){
  boolean visible=started&&!screenOff&&Settings.canDrawOverlays(context)&&!AgentTrayService.systemTrayRegistered()
   &&!context.getSystemService(KeyguardManager.class).isKeyguardLocked();
  if(!visible){remove();return;}
  WindowMetrics metrics=windows.getMaximumWindowMetrics();
  int height=metrics.getWindowInsets().getInsetsIgnoringVisibility(WindowInsets.Type.statusBars()).top;
  if(metrics.getWindowInsets().getDisplayCutout()!=null)height=Math.max(height,metrics.getWindowInsets().getDisplayCutout().getSafeInsetTop());
  WindowManager.LayoutParams params=new WindowManager.LayoutParams(WindowManager.LayoutParams.MATCH_PARENT,Math.max(dp(24),height),
   WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
   WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE|WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE|WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
   PixelFormat.TRANSLUCENT);
  // Application overlays sit below native status/notification windows, so privacy chips and
  // system dialogs always cover the glow. Alpha stays at the platform's untrusted-touch limit
  // so touches on apps drawn under the status bar still pass through.
  params.alpha=Math.min(1f,context.getSystemService(android.hardware.input.InputManager.class).getMaximumObscuringOpacityForTouch());
  params.gravity=Gravity.TOP;
  params.layoutInDisplayCutoutMode=WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
  params.setFitInsetsTypes(0);
  params.setTitle("Pixel agent status waves");
  if(view==null){view=new Waves();windows.addView(view,params);shownState="";android.util.Log.i(TAG,"App status waves height="+params.height);}
  else if(view.getLayoutParams().height!=params.height){windows.updateViewLayout(view,params);shownState="";}
  // While connected the view animates itself; this only wakes it on state changes.
  Bundle state=AgentTrayService.state();
  String next=state.getBoolean("connected")+":"+state.getBoolean("listening")+":"+state.getBoolean("muted")+":"+state.getString("status");
  if(!next.equals(shownState)){shownState=next;view.invalidate();}
 }

 private final class Waves extends View {
  final StatusVoiceGlow agentGlow=new StatusVoiceGlow(),micGlow=new StatusVoiceGlow();
  long rendered;
  Waves(){super(context);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);}
  @Override protected void onDraw(Canvas canvas){
   Bundle state=AgentTrayService.state();
   boolean listening=state.getBoolean("listening"),muted=state.getBoolean("muted"),connected=state.getBoolean("connected");
   boolean active=connected||listening||"Connecting".equals(state.getString("status"));
   long now=SystemClock.elapsedRealtime();
   float elapsed=rendered==0?16:Math.min(100,now-rendered);rendered=now;
   boolean fresh=now-state.getLong("audioObservedAt")<1200;
   float center=getWidth()/2f,holeLeft=center-dp(12),holeRight=center+dp(12);
   WindowInsets insets=getRootWindowInsets();
   // Only a centred top cutout splits the glows; off-centre cameras (Fold inner display) use the default gap.
   if(insets!=null&&insets.getDisplayCutout()!=null)for(Rect rect:insets.getDisplayCutout().getBoundingRects()){
    if(rect.top<getHeight()&&rect.centerX()>getWidth()/4&&rect.centerX()<getWidth()*3/4){holeLeft=rect.left;holeRight=rect.right;}
   }
   float rightStart=holeRight+dp(6),glowWidth=Math.min(holeLeft-dp(6),getWidth()-rightStart),leftStart=0;
   if(active)agentGlow.draw(canvas,leftStart,0,glowWidth,getHeight(),fresh?state.getFloat("outputLevel"):0,now/1000f,elapsed,true);
   else agentGlow.clear(); // #224: nothing is drawn at rest (no grey rest line), as in the OS tray (PR231)
   if(active&&listening&&!muted)micGlow.draw(canvas,rightStart,0,glowWidth,getHeight(),fresh?state.getFloat("inputLevel"):0,now/1000f+1.7f,elapsed,false);
   else micGlow.clear();
   if(connected||listening)postInvalidateOnAnimation();
  }
 }

 @Override public void onDisplayAdded(int id){}
 @Override public void onDisplayRemoved(int id){}
 // Fold/unfold swaps the default display's size and cutout; re-measure the strip.
 @Override public void onDisplayChanged(int id){if(id==Display.DEFAULT_DISPLAY){shownState="";reconcile();}}
}
