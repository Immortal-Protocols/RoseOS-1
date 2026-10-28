package dev.pixel.voice;
import android.app.*;import android.os.*;import android.webkit.*;import android.net.*;import android.content.*;
import org.json.*;import java.io.*;import java.net.*;import javax.net.ssl.*;import java.util.concurrent.*;
public final class MacScreenPanel extends android.widget.FrameLayout {
 final Activity activity;final Runnable expand,close; boolean compact;
 public MacScreenPanel(Activity activity,boolean compact,Runnable expand,Runnable close){super(activity);this.activity=activity;this.compact=compact;this.expand=expand;this.close=close;initialize();}
 WebView web;JSONObject config;boolean ready=false,resumed=false;double videoAspect=16.0/9.0;ExecutorService io=Executors.newSingleThreadExecutor();
 final Handler retryHandler=new Handler(Looper.getMainLooper());int retryCount;boolean requestPending;
 final java.util.concurrent.atomic.AtomicBoolean statsPending=new java.util.concurrent.atomic.AtomicBoolean();
 final Runnable retry=()->start();
 void retryLater(){if(!resumed||io.isShutdown())return;retryHandler.removeCallbacks(retry);retryHandler.postDelayed(retry,Math.min(20000,1000L<<Math.min(retryCount++,4)));}
 @Override protected void onMeasure(int widthSpec,int heightSpec){
  if(compact&&MeasureSpec.getMode(widthSpec)!=MeasureSpec.UNSPECIFIED){int width=MeasureSpec.getSize(widthSpec);super.onMeasure(MeasureSpec.makeMeasureSpec(width,MeasureSpec.EXACTLY),MeasureSpec.makeMeasureSpec((int)Math.round(width/videoAspect),MeasureSpec.EXACTLY));}
  else super.onMeasure(widthSpec,heightSpec);
 }
 String read(InputStream s)throws Exception{ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[8192];for(int n;(n=s.read(b))!=-1;)out.write(b,0,n);return out.toString("UTF-8");}
 void initialize(){
  try{config=new JSONObject(read(activity.getAssets().open("mac-screen-connection.json")));}catch(Exception e){throw new RuntimeException(e);}
  createWeb();
 }
 final RendererRecovery renderer=new RendererRecovery(3,60000);
 /** The panel's WebView, bridge and settings as at startup. */
 void createWeb(){
  web=new WebView(activity);web.setBackgroundColor(0xffb9dff3);web.getSettings().setJavaScriptEnabled(true);web.getSettings().setMediaPlaybackRequiresUserGesture(false);web.getSettings().setAllowFileAccess(false);web.getSettings().setAllowContentAccess(false);web.getSettings().setDomStorageEnabled(false);
  web.setWebViewClient(new WebViewClient(){
   public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest req){
    if(!"macscreen.androidplatform.net".equals(req.getUrl().getHost()))return null;
    String p=req.getUrl().getPath();if(!p.equals("/mac-screen.html")&&!p.equals("/mac-screen.js"))return new WebResourceResponse("text/plain","utf-8",new ByteArrayInputStream(new byte[0]));
    try{return new WebResourceResponse(p.endsWith(".js")?"application/javascript":"text/html","utf-8",activity.getAssets().open(p.substring(1)));}catch(Exception e){return null;}
   }
   public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest req){return true;}
   public void onPageFinished(WebView v,String u){if(v!=web)return;ready=true;setCompact(compact);if(resumed)start();}
   // The shared renderer is gone: drop this dead view and reload a fresh one, or say the screen is unavailable.
   @Override public boolean onRenderProcessGone(WebView v,RenderProcessGoneDetail d){rendererGone(v);return true;}
  });
  web.setWebChromeClient(new WebChromeClient(){public void onPermissionRequest(PermissionRequest r){r.deny();}});
  web.addJavascriptInterface(new Bridge(),"Native");addView(web,new android.widget.FrameLayout.LayoutParams(-1,-1));web.loadUrl("https://macscreen.androidplatform.net/mac-screen.html");
 }
 void rendererGone(WebView dead){
  removeView(dead);dead.destroy();if(dead!=web)return;
  web=null;ready=false;requestPending=false;retryHandler.removeCallbacks(retry);
  if(renderer.allow(SystemClock.elapsedRealtime())){createWeb();return;}
  android.widget.TextView gone=new android.widget.TextView(activity);gone.setText("Mac screen unavailable. Close it and open it again.");gone.setGravity(android.view.Gravity.CENTER);
  addView(gone,new android.widget.FrameLayout.LayoutParams(-1,-1));
 }
 String request(String route,String body)throws Exception{
  HttpsURLConnection c=(HttpsURLConnection)new URL(config.getString("endpoint")+route).openConnection();int timeout="/stats".equals(route)?2000:12000;c.setConnectTimeout(timeout);c.setReadTimeout(timeout);c.setSSLSocketFactory(new DeviceIdentity(activity).ssl(config.getString("pin")));c.setHostnameVerifier((h,session)->true);
  if(body!=null){c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");c.getOutputStream().write(body.getBytes("UTF-8"));}
  try{return read(c.getInputStream());}finally{c.disconnect();}
 }
 void start(){if(!ready||!resumed||io.isShutdown()||requestPending)return;requestPending=true;io.execute(()->{try{String session=request("/session",null);activity.runOnUiThread(()->{requestPending=false;if(resumed&&web!=null)web.evaluateJavascript("window.startScreen("+session+")",null);});}catch(Exception e){activity.runOnUiThread(()->{requestPending=false;if(resumed&&web!=null)web.evaluateJavascript("window.showError("+JSONObject.quote(e.toString())+")",null);});}});}
 public class Bridge {
  @JavascriptInterface public void reconnect(){activity.runOnUiThread(()->retryLater());}
  @JavascriptInterface public void connected(){activity.runOnUiThread(()->{retryCount=0;retryHandler.removeCallbacks(retry);});}
  @JavascriptInterface public void expand(){activity.runOnUiThread(expand);}
  @JavascriptInterface public void close(){activity.runOnUiThread(close);}
  @JavascriptInterface public void stats(String value){
   try{JSONObject data=new JSONObject(value);int w=data.optInt("width"),h=data.optInt("height");if(w>=16&&h>=16){double ratio=(double)w/h;activity.runOnUiThread(()->{if(Math.abs(videoAspect-ratio)>.001){videoAspect=ratio;if(compact)requestLayout();}});}}catch(JSONException ignored){}
   if(io.isShutdown()||!statsPending.compareAndSet(false,true))return;final String report=value;io.execute(()->{try{request("/stats",report);}catch(Exception e){android.util.Log.w("MacScreen","Stats not delivered");}finally{statsPending.set(false);}});
  }
  @JavascriptInterface public String network(){ConnectivityManager cm=(ConnectivityManager)activity.getSystemService(Context.CONNECTIVITY_SERVICE);NetworkCapabilities c=cm.getNetworkCapabilities(cm.getActiveNetwork());return c!=null&&c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)?"Cellular":c!=null&&c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)?"Wi-Fi":"Network";}
 }
 public void resume(){resumed=true;start();}
 public void pause(){resumed=false;retryHandler.removeCallbacks(retry);if(web!=null)web.evaluateJavascript("window.stopScreen()",null);}
 public void destroy(){pause();io.shutdownNow();if(web!=null)web.destroy();web=null;}
 public void setCompact(boolean value){compact=value;requestLayout();if(ready&&web!=null)web.evaluateJavascript("window.setCompact("+value+")",null);}
}
