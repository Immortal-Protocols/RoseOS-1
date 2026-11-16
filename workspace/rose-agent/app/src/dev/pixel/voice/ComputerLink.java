package dev.pixel.voice;

import android.app.KeyguardManager;
import android.content.Context;
import android.os.SystemClock;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Computer Link (docs/COMPUTER-LINK.md): the computer_* phone commands. The phone photographs the
 * computer's monitor with its camera and acts as that computer's Bluetooth keyboard and absolute pointer.
 *
 * Rules: nothing runs unless the Computer Link screen is open and in front, the owner tapped Allow on it,
 * and the phone is unlocked. Every input names the frameId of the latest frame; an input other than a
 * pointer move uses that frame up, so the next input needs a fresh screenshot. Every input ends with all
 * keys and buttons released. Commands run one at a time on their own thread, never the UI thread.
 */
final class ComputerLink {
 private ComputerLink(){}
 static final ExecutorService worker=Executors.newSingleThreadExecutor();
 static final long FRAME_VALID_MS=120000;
 static final int MAX_TEXT=500;

 /** The resumed Computer Link screen, or null. */
 static volatile ComputerLinkActivity screen;
 /** The owner's Allow on that screen; cleared by Stop and whenever the screen leaves the foreground. */
 static volatile boolean allowed;
 static volatile ComputerBluetooth bluetooth;

 private static String frameId;
 private static long frameAt;
 private static int frameWidth,frameHeight;

 static boolean handles(String action){return action!=null&&action.startsWith("computer_");}

 static synchronized void invalidateFrame(){frameId=null;}
 private static synchronized String registerFrame(int width,int height){
  frameId=UUID.randomUUID().toString();frameAt=SystemClock.elapsedRealtime();frameWidth=width;frameHeight=height;return frameId;
 }
 /** {width,height} of the named frame when it is the latest and still fresh; consumes it unless keep. */
 private static synchronized int[] frame(String id,boolean keep){
  if(frameId==null||!frameId.equals(id)||SystemClock.elapsedRealtime()-frameAt>FRAME_VALID_MS)return null;
  if(!keep)frameId=null;
  return new int[]{frameWidth,frameHeight};
 }

 static void stop(){
  allowed=false;invalidateFrame();
  ComputerBluetooth b=bluetooth;if(b!=null)b.releaseAll();
 }

 static JSONObject execute(Context context,JSONObject command)throws Exception{
  String action=command.getString("action");
  ComputerLinkActivity s=screen;ComputerBluetooth b=bluetooth;
  if(action.equals("computer_status")){
   return new JSONObject().put("status","ok").put("screenOpen",s!=null).put("allowed",s!=null&&allowed)
    .put("calibrated",s!=null&&s.calibrated()).put("bluetoothConnected",b!=null&&b.connected())
    .put("bluetooth",b==null?"Computer Link screen is not open":b.describe());
  }
  if(action.equals("computer_open")){
   // Only brings the screen forward. Rose still needs the owner's Allow on it.
   if(((KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked())return refusal("locked","The phone is locked.");
   context.startActivity(new android.content.Intent(context,ComputerLinkActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
   return new JSONObject().put("status","launch_requested").put("note","The owner must tap Allow Rose on the phone before any input is sent.");
  }
  if(s==null)return refusal("not_open","Ask the owner to open Computer Link on the phone, or use computer_open.");
  if(!allowed)return refusal("not_allowed","Ask the owner to tap Allow Rose on the Computer Link screen.");
  if(((KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked())return refusal("locked","The phone is locked.");
  if(action.equals("computer_screenshot")){
   if(!s.calibrated())return refusal("not_calibrated","Ask the owner to set the screen corners on the Computer Link screen.");
   JSONObject shot=s.captureFrame();
   if("ok".equals(shot.optString("status")))shot.put("frameId",registerFrame(shot.getInt("width"),shot.getInt("height"))).put("validForMs",FRAME_VALID_MS);
   return shot;
  }
  if(b==null||!b.connected())return refusal("no_connection","The phone is not connected to the computer over Bluetooth.");
  boolean keep=action.equals("computer_move");
  int[] size=frame(command.optString("frameId"),keep);
  if(size==null)return refusal("stale_frame","Take a new computer_screenshot and choose the target from it.");
  try{
   return perform(action,command,size,b);
  }finally{
   b.releaseAll();
   s.showAction(action);
  }
 }

 private static JSONObject perform(String action,JSONObject command,int[] size,ComputerBluetooth b)throws Exception{
  switch(action){
   case "computer_move":{
    int[] p=point(command,"x","y",size);if(p==null)return outside();
    return sent(b.pointer(0,p[0],p[1],0,0));
   }
   case "computer_click":{
    int[] p=point(command,"x","y",size);if(p==null)return outside();
    String name=command.optString("button","left");
    int button=name.equals("right")?ComputerHid.BUTTON_RIGHT:name.equals("middle")?ComputerHid.BUTTON_MIDDLE:ComputerHid.BUTTON_LEFT;
    int count=Math.max(1,Math.min(3,command.optInt("count",1)));
    boolean ok=b.pointer(0,p[0],p[1],0,0);pause(40);
    for(int i=0;i<count&&ok;i++){ok=b.pointer(button,p[0],p[1],0,0);pause(30);ok&=b.pointer(0,p[0],p[1],0,0);pause(60);}
    return sent(ok);
   }
   case "computer_drag":{
    JSONArray pts=command.getJSONArray("points");
    int[] from=point(pts.getInt(0),pts.getInt(1),size),to=point(pts.getInt(2),pts.getInt(3),size);
    if(from==null||to==null)return outside();
    int duration=Math.max(200,Math.min(3000,command.optInt("durationMs",600))),steps=Math.max(8,duration/16);
    boolean ok=b.pointer(0,from[0],from[1],0,0);pause(40);
    ok&=b.pointer(ComputerHid.BUTTON_LEFT,from[0],from[1],0,0);pause(60);
    for(int i=1;i<=steps&&ok;i++){
     ok=b.pointer(ComputerHid.BUTTON_LEFT,from[0]+(to[0]-from[0])*i/steps,from[1]+(to[1]-from[1])*i/steps,0,0);pause(duration/steps);
    }
    pause(60);ok&=b.pointer(0,to[0],to[1],0,0);
    return sent(ok);
   }
   case "computer_scroll":{
    int[] p=point(command,"x","y",size);if(p==null)return outside();
    String direction=command.getString("direction");int amount=Math.max(1,Math.min(20,command.optInt("amount",3)));
    int wheel=direction.equals("up")?1:direction.equals("down")?-1:0,pan=direction.equals("right")?1:direction.equals("left")?-1:0;
    if(wheel==0&&pan==0)throw new IllegalArgumentException("Unsupported scroll direction");
    boolean ok=b.pointer(0,p[0],p[1],0,0);pause(40);
    for(int i=0;i<amount&&ok;i++){ok=b.pointer(0,p[0],p[1],wheel,pan);pause(25);}
    return sent(ok);
   }
   case "computer_type":{
    String text=command.getString("text");
    if(text.length()>MAX_TEXT)return refusal("too_long","Type at most "+MAX_TEXT+" characters at a time.");
    int bad=ComputerHid.untypable(text);
    if(bad>=0)return refusal("unsupported_character","Character "+bad+" cannot be typed on a US keyboard layout; nothing was typed.").put("index",bad);
    boolean ok=true;
    for(int i=0;i<text.length()&&ok;i++){
     int[] k=ComputerHid.character(text.charAt(i));
     ok=b.send(ComputerHid.KEYBOARD_ID,ComputerHid.keyboard(k[0],k[1]));pause(6);
     ok&=b.send(ComputerHid.KEYBOARD_ID,ComputerHid.keyboard(0));pause(6);
    }
    return sent(ok).put("characters",text.length());
   }
   case "computer_key":{
    JSONArray keys=command.getJSONArray("keys");List<String> names=new ArrayList<>();
    for(int i=0;i<keys.length();i++)names.add(keys.getString(i));
    byte[] pressed=ComputerHid.chord(names);
    boolean ok=b.send(ComputerHid.KEYBOARD_ID,pressed);pause(50);
    ok&=b.send(ComputerHid.KEYBOARD_ID,ComputerHid.keyboard(0));
    return sent(ok);
   }
   default:throw new IllegalArgumentException("Unsupported Computer Link action");
  }
 }

 private static int[] point(JSONObject c,String xKey,String yKey,int[] size)throws Exception{return point(c.getInt(xKey),c.getInt(yKey),size);}
 private static int[] point(int x,int y,int[] size){
  if(x<0||y<0||x>=size[0]||y>=size[1])return null;
  return new int[]{ComputerHid.axis(x,size[0]),ComputerHid.axis(y,size[1])};
 }
 private static JSONObject outside()throws Exception{return refusal("out_of_frame","The point is outside the frame; nothing was sent.");}
 private static JSONObject sent(boolean ok)throws Exception{
  return ok?new JSONObject().put("status","action_accepted").put("note","Sent to the computer. Take a new screenshot to see the result.")
   :new JSONObject().put("status","send_failed").put("reason","Bluetooth did not accept the input; part of it may have been sent. Take a new screenshot before anything else.");
 }
 static JSONObject refusal(String status,String reason)throws Exception{return new JSONObject().put("status",status).put("actionExecuted",false).put("reason",reason);}
 private static void pause(long ms){SystemClock.sleep(ms);}
}
