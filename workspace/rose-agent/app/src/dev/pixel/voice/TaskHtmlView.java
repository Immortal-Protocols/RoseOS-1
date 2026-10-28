package dev.pixel.voice;

import android.annotation.SuppressLint;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.webkit.*;
import android.widget.LinearLayout;
import java.io.ByteArrayInputStream;
import java.util.UUID;

/** Isolated result display: no native bridge, file access or network requests. */
final class TaskHtmlView extends WebView {
 private final MainActivity activity;
 private final String resizeKey="pixel-height-"+UUID.randomUUID()+":";
 private String documentHtml;
 private boolean disposed;
 /** Called after renderer loss has removed and destroyed this view. */
 Runnable onRendererGone;
 @SuppressLint("SetJavaScriptEnabled")
 TaskHtmlView(MainActivity activity,String html){
  super(activity);this.activity=activity;setBackgroundColor(Color.TRANSPARENT);
  getSettings().setJavaScriptEnabled(true);getSettings().setDomStorageEnabled(false);
  getSettings().setAllowFileAccess(false);getSettings().setAllowContentAccess(false);
  getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
  getSettings().setGeolocationEnabled(false);getSettings().setSupportMultipleWindows(false);
  setVerticalScrollBarEnabled(false);setHorizontalScrollBarEnabled(false);
  setLayoutParams(new LinearLayout.LayoutParams(-1,dp(120)));
  setWebViewClient(new WebViewClient(){
   @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
    Uri uri=request.getUrl();
    if("https".equals(uri.getScheme())&&"pixel-card.invalid".equals(uri.getHost())&&"/result.html".equals(uri.getPath())){
     return new WebResourceResponse("text/html","utf-8",new ByteArrayInputStream(documentHtml.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
    }
    if("https".equals(uri.getScheme())&&"pixel-card.invalid".equals(uri.getHost())&&"/nunito.ttf".equals(uri.getPath())){
     try{return new WebResourceResponse("font/ttf",null,activity.getAssets().open("fonts/nunito.ttf"));}catch(Exception ignored){}
    }
    return new WebResourceResponse("text/plain","utf-8",403,"Blocked",null,new ByteArrayInputStream(new byte[0]));
   }
   @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
    Uri uri=request.getUrl();
    if(request.isForMainFrame()&&request.hasGesture()&&("https".equals(uri.getScheme())||"http".equals(uri.getScheme()))){
     try{activity.startActivity(new Intent(Intent.ACTION_VIEW,uri));}catch(Exception ignored){}
    }
    return true;
   }
   // The app's shared renderer is gone: this card's view is unusable. Remove and destroy it; the owner shows the text result.
   @Override public boolean onRenderProcessGone(WebView view,RenderProcessGoneDetail detail){
    android.view.ViewParent parent=getParent();if(parent instanceof android.view.ViewGroup)((android.view.ViewGroup)parent).removeView(TaskHtmlView.this);
    dispose();if(onRendererGone!=null)onRendererGone.run();return true;
   }
   @Override public void onPageFinished(WebView view,String url){
    if(disposed)return;
    evaluateJavascript("(()=>{const report=()=>console.log('"+resizeKey+"'+Math.ceil(document.documentElement.scrollHeight));new ResizeObserver(report).observe(document.body);document.fonts.ready.then(report);report();})()",null);
   }
  });
  setWebChromeClient(new WebChromeClient(){
   @Override public void onPermissionRequest(PermissionRequest request){request.deny();}
   @Override public boolean onShowFileChooser(WebView view,android.webkit.ValueCallback<Uri[]> callback,FileChooserParams params){callback.onReceiveValue(null);return true;}
   @Override public boolean onConsoleMessage(ConsoleMessage message){
    if(disposed||!message.message().startsWith(resizeKey))return true;
    try{int height=Math.max(40,Math.min(1200,Integer.parseInt(message.message().substring(resizeKey.length()))));
     if(getLayoutParams().height!=dp(height)){getLayoutParams().height=dp(height);requestLayout();}
    }catch(Exception ignored){}
    return true;
   }
  });
  String ink=PixelAppearance.dark()?"#e6edf6":"#29385d",muted=PixelAppearance.dark()?"#adbdd2":"#576f90";
  String document="<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"+
   "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src https://pixel-card.invalid; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'\">"+
   "<style>@font-face{font-family:Pixel;src:url('/nunito.ttf')}*{box-sizing:border-box}html,body{margin:0;padding:0;background:transparent!important;overflow-x:hidden}body{font-family:Pixel,sans-serif;color:"+ink+";font-size:18px;line-height:1.45;font-weight:600} :root{--ink:"+ink+";--muted:"+muted+";--accent:#008d80;--surface:transparent}h1,h2,h3,p{margin:0 0 12px}h1{font-size:24px}h2{font-size:22px}h3{font-size:20px}a{color:#008d80}svg,img{max-width:100%;height:auto}button{font:inherit;color:inherit}small{color:var(--muted)}</style></head><body>"+html+"</body></html>";
  documentHtml=document;
  loadUrl("https://pixel-card.invalid/result.html");
 }
 private int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
 void dispose(){if(!disposed){disposed=true;stopLoading();destroy();}}
}
