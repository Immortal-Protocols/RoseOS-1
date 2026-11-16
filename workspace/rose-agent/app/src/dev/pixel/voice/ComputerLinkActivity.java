package dev.pixel.voice;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.*;
import android.graphics.drawable.ColorDrawable;
import android.hardware.camera2.*;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Base64;
import android.util.Size;
import android.view.*;
import android.widget.*;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/**
 * Computer Link (docs/COMPUTER-LINK.md): the screen the owner keeps open while Rose operates a computer.
 * The phone stands facing the computer's monitor. This screen owns the camera (live preview plus one
 * still per computer_screenshot), the Bluetooth keyboard/pointer registration, the owner's Allow and Stop,
 * and the one-time screen-corner calibration. It shows the last frame Rose saw and the last input sent.
 */
public final class ComputerLinkActivity extends Activity {
 private static final int PERMISSIONS=41;
 private static final String PREFS="computer-link",CORNERS="corners";
 private static final String[] CORNER_NAMES={"top-left","top-right","bottom-right","bottom-left"};

 private SharedPreferences prefs;
 private TextureView preview;
 private ImageView frameView;
 private CornerView cornerView;
 private TextView status;
 private Button allow;
 private ComputerBluetooth bluetooth;
 private String lastAction="";

 private HandlerThread cameraThread;
 private Handler cameraHandler;
 private CameraDevice camera;
 private CameraCaptureSession session;
 private ImageReader reader;
 private Size previewSize;
 private int sensorOrientation;
 private volatile CompletableFuture<byte[]> pendingStill;
 private boolean calibrating;
 /** A USB HDMI capture card that Android exposes as an external camera: its frame is exactly the screen. */
 private volatile boolean external;
 private static final float[] FULL_FRAME={0,0,1,0,1,1,0,1};

 @Override protected void onCreate(Bundle state){
  super.onCreate(state);
  getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
  prefs=getSharedPreferences(PREFS,0);
  setContentView(layout());
  List<String> missing=new ArrayList<>();
  for(String p:new String[]{Manifest.permission.CAMERA,Manifest.permission.BLUETOOTH_CONNECT,Manifest.permission.BLUETOOTH_ADVERTISE})
   if(!granted(p))missing.add(p);
  if(missing.isEmpty())startBluetooth();else requestPermissions(missing.toArray(new String[0]),PERMISSIONS);
 }

 /** Nearby-devices permissions exist from Android 12; earlier Bluetooth use needs no runtime grant. */
 private boolean granted(String permission){
  if(android.os.Build.VERSION.SDK_INT<31&&permission.startsWith("android.permission.BLUETOOTH_"))return true;
  return checkSelfPermission(permission)==PackageManager.PERMISSION_GRANTED;
 }

 @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] results){
  if(code!=PERMISSIONS)return;
  startBluetooth();
  if(preview.isAvailable())openCamera();
 }

 private void startBluetooth(){
  if(bluetooth!=null||!granted(Manifest.permission.BLUETOOTH_CONNECT)){refresh();return;}
  bluetooth=new ComputerBluetooth(this,()->runOnUiThread(this::refresh));
  ComputerLink.bluetooth=bluetooth;
  bluetooth.start();
 }

 @Override protected void onResume(){
  super.onResume();
  ComputerLink.screen=this;
  cameraThread=new HandlerThread("computer-link-camera");cameraThread.start();cameraHandler=new Handler(cameraThread.getLooper());
  if(preview.isAvailable())openCamera();
  refresh();
 }

 @Override protected void onPause(){
  // Leaving the screen withdraws Rose's permission: the owner taps Allow again on return.
  ComputerLink.screen=null;
  ComputerLink.stop();
  closeCamera();
  cameraThread.quitSafely();cameraThread=null;cameraHandler=null;
  super.onPause();
 }

 @Override protected void onDestroy(){
  if(bluetooth!=null){bluetooth.stop();if(ComputerLink.bluetooth==bluetooth)ComputerLink.bluetooth=null;}
  super.onDestroy();
 }

 // ---- Layout ----

 private View layout(){
  LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(0xFF101014);
  LinearLayout bar=new LinearLayout(this);bar.setOrientation(LinearLayout.HORIZONTAL);bar.setGravity(Gravity.CENTER_VERTICAL);bar.setPadding(16,8,16,8);
  allow=button("Allow Rose",v->toggleAllow());
  bar.addView(allow);
  bar.addView(button("Stop",v->{ComputerLink.stop();refresh();}));
  bar.addView(button("Set screen corners",v->startCalibration()));
  bar.addView(button("Make discoverable",v->discoverable()));
  status=new TextView(this);status.setTextColor(0xFFE8E8EC);status.setTextSize(13);status.setPadding(16,0,0,0);
  bar.addView(status,new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1));
  root.addView(bar);

  FrameLayout stage=new FrameLayout(this);
  LinearLayout panes=new LinearLayout(this);panes.setOrientation(LinearLayout.HORIZONTAL);
  preview=new TextureView(this);
  preview.setSurfaceTextureListener(new TextureView.SurfaceTextureListener(){
   public void onSurfaceTextureAvailable(SurfaceTexture t,int w,int h){openCamera();}
   public void onSurfaceTextureSizeChanged(SurfaceTexture t,int w,int h){transformPreview(w,h);}
   public boolean onSurfaceTextureDestroyed(SurfaceTexture t){return true;}
   public void onSurfaceTextureUpdated(SurfaceTexture t){}
  });
  frameView=new ImageView(this);frameView.setScaleType(ImageView.ScaleType.FIT_CENTER);frameView.setBackgroundColor(0xFF000000);
  LinearLayout.LayoutParams half=new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.MATCH_PARENT,1);half.setMargins(4,4,4,4);
  panes.addView(preview,half);
  panes.addView(frameView,new LinearLayout.LayoutParams(half));
  stage.addView(panes);
  cornerView=new CornerView(this);cornerView.setVisibility(View.GONE);cornerView.setBackgroundColor(0xFF000000);
  stage.addView(cornerView);
  root.addView(stage,new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,0,1));
  return root;
 }

 private Button button(String label,View.OnClickListener click){
  Button b=new Button(this);b.setText(label);b.setAllCaps(false);b.setOnClickListener(click);return b;
 }

 private void refresh(){
  if(status==null)return;
  boolean allowed=ComputerLink.allowed&&ComputerLink.screen==this;
  allow.setText(allowed?"Rose allowed":"Allow Rose");
  allow.setBackground(new ColorDrawable(allowed?0xFF2E7D4F:0xFF3A3A44));allow.setTextColor(0xFFFFFFFF);
  String corners=external?"Capture card":calibrated()?"Camera, screen corners set":"Set the screen corners first";
  String bt=bluetooth==null?"Allow Nearby devices for Rose Agent":bluetooth.describe();
  status.setText((calibrating?"Tap the monitor's "+CORNER_NAMES[cornerView.count()]+" corner. ":"")+corners+" · "+bt+(lastAction.isEmpty()?"":" · Last: "+lastAction));
 }

 private void toggleAllow(){
  if(ComputerLink.allowed){ComputerLink.stop();refresh();return;}
  if(!calibrated()){Toast.makeText(this,"Set the screen corners first",Toast.LENGTH_SHORT).show();return;}
  ComputerLink.allowed=true;refresh();
 }

 private void discoverable(){
  try{startActivity(new Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE).putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION,120));}
  catch(RuntimeException e){Toast.makeText(this,"Bluetooth settings unavailable",Toast.LENGTH_SHORT).show();}
 }

 void showAction(String action){runOnUiThread(()->{lastAction=action.replace("computer_","");refresh();});}

 // ---- Calibration ----

 boolean calibrated(){return external||corners()!=null;}

 /** Normalized corners (TL, TR, BR, BL) in the camera's unrotated still, or null. */
 private float[] corners(){
  if(external)return FULL_FRAME.clone();
  String saved=prefs.getString(CORNERS,null);if(saved==null)return null;
  String[] parts=saved.split(",");if(parts.length!=8)return null;
  float[] out=new float[8];
  try{for(int i=0;i<8;i++)out[i]=Float.parseFloat(parts[i]);}catch(NumberFormatException e){return null;}
  return out;
 }

 private void startCalibration(){
  if(external){Toast.makeText(this,"A capture card is connected: its picture is already the whole screen",Toast.LENGTH_LONG).show();return;}
  ComputerLink.stop();
  calibrating=true;cornerView.reset();refresh();
  ComputerLink.worker.execute(()->{
   try{
    byte[] jpeg=still();
    BitmapFactory.Options o=new BitmapFactory.Options();o.inSampleSize=2;
    Bitmap raw=BitmapFactory.decodeByteArray(jpeg,0,jpeg.length,o);
    if(raw==null)throw new IllegalStateException("Photo unavailable");
    runOnUiThread(()->{cornerView.show(raw,uprightRotation());cornerView.setVisibility(View.VISIBLE);refresh();});
   }catch(Exception e){
    runOnUiThread(()->{calibrating=false;Toast.makeText(this,"Could not take a photo: "+e.getMessage(),Toast.LENGTH_LONG).show();refresh();});
   }
  });
 }

 private void finishCalibration(float[] normalized,int width,int height){
  float[] px=new float[8];
  for(int i=0;i<4;i++){px[2*i]=normalized[2*i]*width;px[2*i+1]=normalized[2*i+1]*height;}
  try{ComputerGeometry.frameSize(px);}
  catch(IllegalArgumentException e){Toast.makeText(this,e.getMessage()+". Try again.",Toast.LENGTH_LONG).show();cornerView.reset();refresh();return;}
  StringBuilder s=new StringBuilder();for(int i=0;i<8;i++){if(i>0)s.append(',');s.append(normalized[i]);}
  prefs.edit().putString(CORNERS,s.toString()).apply();
  ComputerLink.invalidateFrame();
  calibrating=false;cornerView.setVisibility(View.GONE);cornerView.release();refresh();
 }

 /** Shows one still upright and collects four taps, mapped back to the unrotated still. */
 private final class CornerView extends View {
  private Bitmap bitmap;private final Matrix matrix=new Matrix(),inverse=new Matrix();private int rotation;
  private final float[] taps=new float[8];private int count;
  private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG|Paint.FILTER_BITMAP_FLAG);
  CornerView(Context c){super(c);}
  int count(){return Math.min(count,3);}
  void reset(){count=0;invalidate();}
  void release(){if(bitmap!=null){bitmap.recycle();bitmap=null;}}
  void show(Bitmap b,int rotation){release();bitmap=b;this.rotation=rotation;count=0;layoutMatrix();invalidate();}
  private void layoutMatrix(){
   if(bitmap==null||getWidth()==0)return;
   matrix.reset();matrix.postRotate(rotation);
   RectF r=new RectF(0,0,bitmap.getWidth(),bitmap.getHeight());matrix.mapRect(r);
   matrix.postTranslate(-r.left,-r.top);
   float scale=Math.min(getWidth()/r.width(),getHeight()/r.height());
   matrix.postScale(scale,scale);
   matrix.postTranslate((getWidth()-r.width()*scale)/2,(getHeight()-r.height()*scale)/2);
   matrix.invert(inverse);
  }
  @Override protected void onSizeChanged(int w,int h,int ow,int oh){layoutMatrix();}
  @Override protected void onDraw(Canvas canvas){
   if(bitmap==null)return;
   canvas.drawBitmap(bitmap,matrix,paint);
   paint.setColor(0xFFFF4D8D);paint.setStrokeWidth(4);
   float[] shown=new float[count*2];
   for(int i=0;i<count;i++){shown[2*i]=taps[2*i]*bitmap.getWidth();shown[2*i+1]=taps[2*i+1]*bitmap.getHeight();}
   matrix.mapPoints(shown);
   for(int i=0;i<count;i++){
    canvas.drawCircle(shown[2*i],shown[2*i+1],14,paint);
    if(i>0)canvas.drawLine(shown[2*i-2],shown[2*i-1],shown[2*i],shown[2*i+1],paint);
   }
  }
  @Override public boolean onTouchEvent(MotionEvent e){
   if(bitmap==null||e.getAction()!=MotionEvent.ACTION_UP)return true;
   float[] p={e.getX(),e.getY()};inverse.mapPoints(p);
   taps[2*count]=Math.max(0,Math.min(1,p[0]/bitmap.getWidth()));
   taps[2*count+1]=Math.max(0,Math.min(1,p[1]/bitmap.getHeight()));
   count++;invalidate();
   if(count==4)finishCalibration(Arrays.copyOf(taps,8),bitmap.getWidth(),bitmap.getHeight());else refresh();
   return true;
  }
 }

 // ---- Capture ----

 /** One straightened frame of the monitor, as computer_screenshot returns it. Runs off the UI thread. */
 JSONObject captureFrame()throws Exception{
  float[] corners=corners();
  if(corners==null)return ComputerLink.refusal("not_calibrated","Set the screen corners on the phone.");
  byte[] jpeg;
  try{jpeg=still();}catch(Exception e){return new JSONObject().put("status","capture_failed").put("reason",String.valueOf(e.getMessage()));}
  Bitmap raw=BitmapFactory.decodeByteArray(jpeg,0,jpeg.length);
  if(raw==null)return new JSONObject().put("status","capture_failed").put("reason","Photo could not be decoded");
  Bitmap out=null;
  try{
   float[] px=new float[8];
   for(int i=0;i<4;i++){px[2*i]=corners[2*i]*raw.getWidth();px[2*i+1]=corners[2*i+1]*raw.getHeight();}
   int[] size=ComputerGeometry.frameSize(px);
   out=Bitmap.createBitmap(size[0],size[1],Bitmap.Config.ARGB_8888);
   Matrix warp=new Matrix();
   if(!warp.setPolyToPoly(px,0,new float[]{0,0,size[0],0,size[0],size[1],0,size[1]},0,4))throw new IllegalStateException("Corners cannot be straightened");
   new Canvas(out).drawBitmap(raw,warp,new Paint(Paint.FILTER_BITMAP_FLAG));
   ByteArrayOutputStream bytes=new ByteArrayOutputStream();
   for(int quality=88;;quality-=10){
    bytes.reset();out.compress(Bitmap.CompressFormat.JPEG,quality,bytes);
    if(bytes.size()<=800000)break;
    if(quality<=58)throw new IllegalStateException("Frame exceeds size limit");
   }
   final Bitmap shown=out;out=null;
   runOnUiThread(()->frameView.setImageBitmap(shown));
   return new JSONObject().put("status","ok").put("capturedAt",System.currentTimeMillis()).put("width",size[0]).put("height",size[1])
    .put("mimeType","image/jpeg").put("imageBase64",Base64.encodeToString(bytes.toByteArray(),Base64.NO_WRAP))
    .put("source",external?"capture_card":"camera").put("note",external?"The computer's video output from the capture card.":"A camera photo of the monitor, straightened to the calibrated corners. Small text may be soft; enlarging the Mac's text helps.");
  }finally{
   raw.recycle();if(out!=null)out.recycle();
  }
 }

 /** One full-resolution JPEG from the back camera, in sensor orientation. */
 private byte[] still()throws Exception{
  Handler handler=cameraHandler;CameraCaptureSession s=session;CameraDevice c=camera;ImageReader r=reader;
  if(handler==null||s==null||c==null||r==null)throw new IllegalStateException("Camera is not ready");
  CompletableFuture<byte[]> result=new CompletableFuture<>();pendingStill=result;
  handler.post(()->{
   try{
    CaptureRequest.Builder b=c.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);
    b.addTarget(r.getSurface());
    b.set(CaptureRequest.CONTROL_AF_MODE,CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
    b.set(CaptureRequest.JPEG_QUALITY,(byte)95);
    s.capture(b.build(),null,handler);
   }catch(Exception e){result.completeExceptionally(e);}
  });
  try{return result.get(6,TimeUnit.SECONDS);}finally{pendingStill=null;}
 }

 private int displayDegrees(){
  switch(getWindowManager().getDefaultDisplay().getRotation()){case Surface.ROTATION_90:return 90;case Surface.ROTATION_180:return 180;case Surface.ROTATION_270:return 270;default:return 0;}
 }
 private int uprightRotation(){return (sensorOrientation-displayDegrees()+360)%360;}

 private void openCamera(){
  if(camera!=null||cameraHandler==null||checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)return;
  try{
   CameraManager manager=getSystemService(CameraManager.class);
   // Prefer a USB capture card (an external camera) over the back camera pointed at the monitor.
   String chosen=null;CameraCharacteristics info=null;boolean card=false;
   for(String id:manager.getCameraIdList()){
    CameraCharacteristics c=manager.getCameraCharacteristics(id);
    Integer facing=c.get(CameraCharacteristics.LENS_FACING);
    if(facing==null)continue;
    if(facing==CameraCharacteristics.LENS_FACING_EXTERNAL){chosen=id;info=c;card=true;break;}
    if(facing==CameraCharacteristics.LENS_FACING_BACK&&chosen==null){chosen=id;info=c;}
   }
   if(chosen==null){status.setText("No camera");return;}
   if(card!=external){external=card;ComputerLink.invalidateFrame();runOnUiThread(this::refresh);}
   Integer orientation=info.get(CameraCharacteristics.SENSOR_ORIENTATION);sensorOrientation=orientation==null?(card?0:90):orientation;
   StreamConfigurationMap map=info.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
   Size jpeg=largest(map.getOutputSizes(ImageFormat.JPEG),4032);
   previewSize=closestAspect(map.getOutputSizes(SurfaceTexture.class),jpeg,1920);
   reader=ImageReader.newInstance(jpeg.getWidth(),jpeg.getHeight(),ImageFormat.JPEG,2);
   reader.setOnImageAvailableListener(r->{
    try(Image image=r.acquireLatestImage()){
     if(image==null)return;
     ByteBuffer buffer=image.getPlanes()[0].getBuffer();byte[] bytes=new byte[buffer.remaining()];buffer.get(bytes);
     CompletableFuture<byte[]> waiting=pendingStill;if(waiting!=null)waiting.complete(bytes);
    }
   },cameraHandler);
   manager.openCamera(chosen,new CameraDevice.StateCallback(){
    public void onOpened(CameraDevice device){camera=device;startPreview();}
    public void onDisconnected(CameraDevice device){device.close();camera=null;}
    public void onError(CameraDevice device,int error){device.close();camera=null;runOnUiThread(()->status.setText("Camera error "+error));}
   },cameraHandler);
  }catch(CameraAccessException|SecurityException e){status.setText("Camera unavailable");}
 }

 private void startPreview(){
  try{
   SurfaceTexture texture=preview.getSurfaceTexture();if(texture==null||camera==null)return;
   texture.setDefaultBufferSize(previewSize.getWidth(),previewSize.getHeight());
   Surface surface=new Surface(texture);
   runOnUiThread(()->transformPreview(preview.getWidth(),preview.getHeight()));
   camera.createCaptureSession(Arrays.asList(surface,reader.getSurface()),new CameraCaptureSession.StateCallback(){
    public void onConfigured(CameraCaptureSession s){
     if(camera==null)return;
     session=s;
     try{
      CaptureRequest.Builder b=camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
      b.addTarget(surface);
      b.set(CaptureRequest.CONTROL_AF_MODE,CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
      s.setRepeatingRequest(b.build(),null,cameraHandler);
     }catch(CameraAccessException|IllegalStateException ignored){}
    }
    public void onConfigureFailed(CameraCaptureSession s){runOnUiThread(()->status.setText("Camera could not start"));}
   },cameraHandler);
  }catch(CameraAccessException|IllegalStateException e){runOnUiThread(()->status.setText("Camera could not start"));}
 }

 private void closeCamera(){
  CompletableFuture<byte[]> waiting=pendingStill;if(waiting!=null)waiting.completeExceptionally(new IllegalStateException("Computer Link closed"));
  if(session!=null){try{session.close();}catch(RuntimeException ignored){}session=null;}
  if(camera!=null){camera.close();camera=null;}
  if(reader!=null){reader.close();reader=null;}
 }

 private void transformPreview(int w,int h){
  if(previewSize==null||w==0||h==0)return;
  int rotation=getWindowManager().getDefaultDisplay().getRotation();
  Matrix m=new Matrix();RectF view=new RectF(0,0,w,h),buffer=new RectF(0,0,previewSize.getHeight(),previewSize.getWidth());
  float cx=view.centerX(),cy=view.centerY();
  if(rotation==Surface.ROTATION_90||rotation==Surface.ROTATION_270){
   buffer.offset(cx-buffer.centerX(),cy-buffer.centerY());
   m.setRectToRect(view,buffer,Matrix.ScaleToFit.FILL);
   float scale=Math.min((float)h/previewSize.getHeight(),(float)w/previewSize.getWidth());
   m.postScale(scale,scale,cx,cy);
   m.postRotate(90*(rotation-2),cx,cy);
  }else if(rotation==Surface.ROTATION_180)m.postRotate(180,cx,cy);
  preview.setTransform(m);
 }

 private static Size largest(Size[] sizes,int maxEdge){
  Size best=null;
  for(Size s:sizes){
   if(Math.max(s.getWidth(),s.getHeight())>maxEdge)continue;
   if(best==null||(long)s.getWidth()*s.getHeight()>(long)best.getWidth()*best.getHeight())best=s;
  }
  return best!=null?best:sizes[0];
 }
 private static Size closestAspect(Size[] sizes,Size target,int maxEdge){
  double aspect=(double)target.getWidth()/target.getHeight();Size best=null;
  for(Size s:sizes){
   if(Math.max(s.getWidth(),s.getHeight())>maxEdge)continue;
   if(Math.abs((double)s.getWidth()/s.getHeight()-aspect)>0.02)continue;
   if(best==null||(long)s.getWidth()*s.getHeight()>(long)best.getWidth()*best.getHeight())best=s;
  }
  return best!=null?best:largest(sizes,maxEdge);
 }
}
