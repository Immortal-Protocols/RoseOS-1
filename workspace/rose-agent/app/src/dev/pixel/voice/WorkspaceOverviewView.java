package dev.pixel.voice;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Rose's apps (Background Work) in the committed Oxblood HiFi (#410; main f6503a0, oxblood.css:279-319,421-439;
 * qa/oxblood/oxblood-contract.json "manager"), drawn natively. No visible heading (screen-reader only); a two-column
 * grid of 276dp cards, 18dp apart across and 20dp down. Each card is a dark 13dp-radius window: its body fills the card
 * (the app's verified live frame, or its name and the plain statement that live view isn't available), a frosted
 * identity chip with the app's real icon at the top left, a frosted state chip at the bottom left (hidden while it
 * runs), and the compact frosted Stop at the bottom right. A card whose app's session is authoritatively running
 * (CurrentApps.App.authoritativelyRunning: reported exactly ready) gets the lit ruby edge (static under reduced motion);
 * every other card keeps its truthful state chip; a Stop in flight dims it.
 *
 * #225/#156 (Nathan's phone acceptance): exactly the apps CURRENTLY running under Rose's control (CurrentApps), one
 * window card each, and nothing else: no task history or result cards. A card's body is that app's verified live
 * frame, or the plain statement that live view isn't available: never an icon, canned or stale picture standing in for
 * it. It only renders and reports taps; it starts, stops or opens nothing itself. */
final class WorkspaceOverviewView extends LinearLayout {
 interface Listener {
  /** An app card was tapped (entryId of the app as reported). */
  void app(String entryId);
  /** A Stop that is not available was tapped: say why instead of doing nothing. */
  void stopUnavailable(String reason);
  /** An available Stop was tapped (the app's bound session, both ids): the panel asks before stopping. */
  default void stop(CurrentApps.App app){}
 }
 /** The app's real launcher icon, or null (the card then shows no icon rather than a made-up one). */
 interface Icons { Drawable icon(String pkg); }
 /** The app's visible name, or "" (the card then says Rose's app rather than a guessed name). */
 interface Labels { String label(String pkg); }
 static final int INK=0xff141922,INK2=0xff5b6680,INK3=0xff8a93a6,OK=0xff4fb36a,ATTN=0xffe6a23a,STOP=0xffd9534a,STOPPED=0xff8e98a6,DONE=0xff5b8def,IDLE=0xffaeb7c4,
  CARD=0xff1a0a0d,BODY=0xff1a0a0d,RING=0x29ecd5c6,ROSE=0xffc56f78;
 static final int CARD_DP=276,GAP_DP=18,ROW_GAP_DP=20,RADIUS_DP=13;
 /** Oxblood ink on the dark field (contract "ink"). */
 static final int OX_INK=0xfff2e6d9,OX_INK2=0xffd2b9ab;
 /** No live content for this app yet: said plainly, never an icon or picture standing in for it. */
 static final String LIVE_UNAVAILABLE="Live view isn’t available for this app yet.";
 static final String STOP_APP_UNAVAILABLE="Stopping an app from here isn’t available yet.";
 private Listener listener;private Icons icons;private Labels labels;private String lastKey="";
 private final TextView notice;
 /** Each previewed card's live image view (by entry) while a verified frame is shown, and the frame it holds. */
 private final java.util.Map<String,ImageView> previewViews=new java.util.HashMap<>();
 private final java.util.Map<String,android.graphics.Bitmap> shownFrames=new java.util.HashMap<>();
 /** Each previewed card's overlay blur source (FrameBlur), by entry; cleared on every rebuild. */
 private final java.util.Map<String,FrameBlur> frameBlurs=new java.util.HashMap<>();
 /** Host-test switch only: false draws the overlays without backdrop blur (the frosted-fill fallback), for before/after. */
 static boolean overlayBlur=true;
 /** Stop state per bound session (sessionId to SENDING/STOPPING/STOPPED/UNKNOWN), and whether Home has a Stop route. */
 private java.util.Map<String,CurrentApps.SessionStop.Phase> stops=java.util.Collections.emptyMap();private boolean stopRoute;

 WorkspaceOverviewView(Context c){
  super(c);setOrientation(VERTICAL);
  notice=text("",13,OX_INK2,false);notice.setGravity(Gravity.CENTER);notice.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
 }
 void setListener(Listener l){listener=l;}
 void setIcons(Icons i){icons=i;lastKey="";}
 void setLabels(Labels l){labels=l;lastKey="";}

 /** The apps currently running under Rose's control (CurrentApps), one HiFi window card each, and nothing else: no
  * task history or results. state/reason/apps from CurrentApps.Tracker. note: the HiFi observe line (why the last tap
  * opened nothing). previewEntry/frame: the one card whose verified live frame WorkspaceOverviewPreview accepted (null:
  * every card says live view isn't available). Rebuilds only when what is shown changes; a new frame for the same card
  * only swaps the image. */
 void show(CurrentApps.State state,String reason,java.util.List<CurrentApps.App> apps,String note,String previewEntry,WorkspaceOverviewPreview.Frame frame){
  java.util.Map<String,WorkspaceOverviewPreview.Frame> f=new java.util.HashMap<>();if(previewEntry!=null&&frame!=null)f.put(previewEntry,frame);
  show(state,reason,apps,note,f,java.util.Collections.<String,CurrentApps.SessionStop.Phase>emptyMap(),false);
 }
 /** frames: the verified live frame per entry (one per previewed session at most); stops: each bound session's Stop
  * state; stopRoute: the host can send an owner Stop. */
 void show(CurrentApps.State state,String reason,java.util.List<CurrentApps.App> apps,String note,java.util.Map<String,WorkspaceOverviewPreview.Frame> frames,
   java.util.Map<String,CurrentApps.SessionStop.Phase> stops,boolean stopRoute){
  java.util.Map<String,android.graphics.Bitmap> images=new java.util.HashMap<>();
  for(java.util.Map.Entry<String,WorkspaceOverviewPreview.Frame> e:frames.entrySet())if(e.getValue()!=null&&e.getValue().image instanceof android.graphics.Bitmap)images.put(e.getKey(),(android.graphics.Bitmap)e.getValue().image);
  StringBuilder k=new StringBuilder().append(state).append('|').append(reason).append('|').append(note).append('|').append(new java.util.TreeSet<>(images.keySet())).append('|').append(new java.util.TreeMap<>(stops)).append('|').append(stopRoute);
  for(CurrentApps.App a:apps)k.append('|').append(a.entryId).append(':').append(a.packageName).append(':').append(a.state).append(':').append(a.sessionId).append(':').append(a.stop!=null).append(':').append(a.sessionStatus);
  String key=k.toString();
  if(key.equals(lastKey)){
   for(java.util.Map.Entry<String,android.graphics.Bitmap> e:images.entrySet()){ImageView v=previewViews.get(e.getKey());
    if(v!=null&&shownFrames.get(e.getKey())!=e.getValue()){v.setImageBitmap(e.getValue());shownFrames.put(e.getKey(),e.getValue());fitTop(v);
     FrameBlur fb=frameBlurs.get(e.getKey());if(fb!=null)fb.set(e.getValue());}}
   return;
  }
  lastKey=key;previewViews.clear();shownFrames.clear();frameBlurs.clear();this.stops=stops;this.stopRoute=stopRoute;
  removeAllViews();
  if(state!=CurrentApps.State.AVAILABLE){
   // HiFi .a-empty: nothing running under control, or why it can't be known; never stale cards.
   TextView e=text(state==CurrentApps.State.EMPTY?WorkspaceOverview.EMPTY:CurrentApps.text(reason),15,OX_INK2,false);e.setGravity(Gravity.CENTER);
   e.setTag(state==CurrentApps.State.EMPTY?"empty":"unavailable");
   LayoutParams ep=new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT);ep.topMargin=dp(220);addView(e,ep);
   return;
  }
  java.util.List<View> cards=new java.util.ArrayList<>();
  for(CurrentApps.App a:apps)cards.add(appCard(a,images.get(a.entryId)));
  for(int i=0;i<cards.size();i+=2){
   LinearLayout row=new LinearLayout(getContext());row.setOrientation(HORIZONTAL);
   LayoutParams rp=new LayoutParams(LayoutParams.MATCH_PARENT,dp(CARD_DP));if(i>0)rp.topMargin=dp(ROW_GAP_DP);
   LayoutParams a=new LayoutParams(0,LayoutParams.MATCH_PARENT,1f);row.addView(cards.get(i),a);
   LayoutParams b=new LayoutParams(0,LayoutParams.MATCH_PARENT,1f);b.leftMargin=dp(GAP_DP);
   if(i+1<cards.size())row.addView(cards.get(i+1),b);else row.addView(new View(getContext()),b);
   addView(row,rp);
  }
  notice.setText(note);
  if(!note.isEmpty()){if(notice.getParent()!=null)((ViewGroup)notice.getParent()).removeView(notice);
   LayoutParams np=new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT);np.topMargin=dp(14);addView(notice,np);}
 }

 /** One app running under Rose's control: its real icon and name, its live frame when verified (otherwise its name and
  * the plain statement that live view isn't available), its state, and a Stop that says why it isn't available. */
 View appCard(final CurrentApps.App a,android.graphics.Bitmap frame){
  boolean live=frame!=null;
  CurrentApps.SessionStop.Phase stopping=a.sessionId.isEmpty()?null:stops.get(a.sessionId);
  boolean inFlight=stopping==CurrentApps.SessionStop.Phase.SENDING||stopping==CurrentApps.SessionStop.Phase.STOPPING;
  Drawable icon=icons==null?null:icons.icon(a.packageName);
  String label=labels==null?"":labels.label(a.packageName);if(label==null||label.isEmpty())label=WorkspaceOverview.WORKSPACE;
  String st=inFlight?WorkspaceOverview.STOP_STOPPING:CurrentApps.cardState(a);
  // The lit edge only while the app's session is authoritatively running (reported exactly ready) and no Stop is in
  // flight: never for starting, scheduled, waiting, consent-pending, cleanup-failed, unbound or lingering apps.
  boolean running=!inFlight&&a.authoritativelyRunning();
  FrameLayout card=card(icon,label,st,running,stopButton(a,label,inFlight));
  if(inFlight)dim(card,.6f,.7f);
  if(live)showPreview(card,label,a.entryId,frame);
  card.setContentDescription(label+", "+st+(live?". Live view":". "+LIVE_UNAVAILABLE));
  card.setOnClickListener(v->{if(listener!=null)listener.app(a.entryId);});
  card.setTag("app:"+a.entryId);
  return card;
 }
 /** Stopping (contract "manager.dimmed"): the card's saturation and brightness drop, as one layer. */
 static void dim(View card,float saturation,float brightness){
  android.graphics.ColorMatrix m=new android.graphics.ColorMatrix();m.setSaturation(saturation);
  android.graphics.ColorMatrix b=new android.graphics.ColorMatrix();b.setScale(brightness,brightness,brightness,1);m.postConcat(b);
  android.graphics.Paint p=new android.graphics.Paint();p.setColorFilter(new android.graphics.ColorMatrixColorFilter(m));card.setLayerType(LAYER_TYPE_HARDWARE,p);
 }
 /** Replaces the card's text body with its live frame, filling the whole card, top-aligned and cropped like the
  * reference's full-card preview; the text stays as the spoken description. */
 void showPreview(FrameLayout card,String app,String entryId,android.graphics.Bitmap preview){
  ImageView img=new ImageView(getContext());img.setScaleType(ImageView.ScaleType.MATRIX);img.setImageBitmap(preview);
  img.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->fitTop(img));
  img.setContentDescription("Live view of Rose’s workspace in "+app);img.setTag("preview");
  card.removeViewAt(0);card.addView(img,0,new FrameLayout.LayoutParams(-1,-1));previewViews.put(entryId,img);shownFrames.put(entryId,preview);
  // The overlays' backdrop blur samples exactly this verified frame (BlurUnder, made in card()).
  FrameBlur fb=new FrameBlur(img,preview);frameBlurs.put(entryId,fb);
  for(int i=1;i<card.getChildCount();i++){View o=card.getChildAt(i);
   if(o.getBackground() instanceof android.graphics.drawable.LayerDrawable){android.graphics.drawable.Drawable under=((android.graphics.drawable.LayerDrawable)o.getBackground()).getDrawable(0);
    if(under instanceof BlurUnder){((BlurUnder)under).blur=fb;fb.overlays.add(o);o.invalidate();}}}
 }
 /** Scales the frame to the card's width and pins its top (the app's top bar), cropping only the bottom. */
 static void fitTop(ImageView img){
  android.graphics.drawable.Drawable d=img.getDrawable();if(d==null||img.getWidth()==0||d.getIntrinsicWidth()<=0)return;
  float s=Math.max(img.getWidth()/(float)d.getIntrinsicWidth(),img.getHeight()/(float)d.getIntrinsicHeight());
  android.graphics.Matrix m=new android.graphics.Matrix();m.setScale(s,s);m.postTranslate((img.getWidth()-d.getIntrinsicWidth()*s)/2f,0);img.setImageMatrix(m);
 }
 /** One window card: body (index 0), identity chip, state chip and Stop, and the running edge as its foreground. */
 FrameLayout card(Drawable icon,String label,String state,boolean running,View stopView){
  FrameLayout card=new FrameLayout(getContext());card.setClickable(true);card.setFocusable(true);
  GradientDrawable bg=new GradientDrawable();bg.setColor(CARD);bg.setCornerRadius(dp(RADIUS_DP));bg.setStroke(Math.max(1,dp(1)),RING);card.setBackground(bg);
  card.setClipToOutline(true);
  // Body: no live frame yet, so the app's name and the plain statement (below the chip), never a stand-in picture.
  LinearLayout b=new LinearLayout(getContext());b.setOrientation(VERTICAL);b.setPadding(dp(14),dp(58),dp(14),dp(56));
  TextView name=text(label,15,OX_INK,true);name.setMaxLines(2);name.setEllipsize(TextUtils.TruncateAt.END);b.addView(name);
  TextView t=text(LIVE_UNAVAILABLE,12.5f,OX_INK2,false);t.setMaxLines(5);t.setEllipsize(TextUtils.TruncateAt.END);t.setPadding(0,dp(8),0,0);b.addView(t);
  b.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
  card.addView(b,new FrameLayout.LayoutParams(-1,-1));
  // Identity chip (oxblood.css:424-425): 9dp in, 7dp padding, radius 10, frosted, the app's real 24dp icon.
  if(icon!=null){
   FrameLayout chip=new FrameLayout(getContext());chip.setPadding(dp(7),dp(7),dp(7),dp(7));
   GradientDrawable cg=new GradientDrawable();cg.setColor(0x61281e1a);cg.setCornerRadius(dp(10));cg.setStroke(Math.max(1,dp(1)),0x8cecded6);
   chip.setBackground(new android.graphics.drawable.LayerDrawable(new Drawable[]{new BlurUnder(chip,14,0,dp(10),1.3f,density()),cg}));chip.setTag("identity-chip");
   ImageView iv=new ImageView(getContext());iv.setImageDrawable(icon);iv.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);chip.addView(iv,new FrameLayout.LayoutParams(dp(24),dp(24)));
   FrameLayout.LayoutParams clp=new FrameLayout.LayoutParams(-2,-2,Gravity.TOP|Gravity.START);clp.leftMargin=clp.topMargin=dp(9);card.addView(chip,clp);
  }
  // State chip (oxblood.css:302-306): bottom left, frosted; hidden while the app is simply running.
  TextView chip=text(state,11.5f,OX_INK,false);chip.setSingleLine(true);chip.setEllipsize(TextUtils.TruncateAt.END);chip.setPadding(dp(9),dp(4),dp(9),dp(4));
  GradientDrawable sg=new GradientDrawable();sg.setColor(0x8c1e0e10);sg.setCornerRadius(dp(9));sg.setStroke(Math.max(1,dp(1)),0x38ffece0);
  chip.setBackground(new android.graphics.drawable.LayerDrawable(new Drawable[]{new BlurUnder(chip,10,0,dp(9),1f,density()),sg}));chip.setTag("state-chip");
  if(running)chip.setVisibility(INVISIBLE);
  FrameLayout.LayoutParams sp=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.START);sp.leftMargin=dp(8);sp.bottomMargin=dp(8)+dp(11);sp.rightMargin=dp(8+44);card.addView(chip,sp);
  // Stop (oxblood.css:429-433): a 44dp target whose 34dp frosted plate sits 8dp in from the bottom right.
  FrameLayout.LayoutParams stp=new FrameLayout.LayoutParams(dp(44),dp(44),Gravity.BOTTOM|Gravity.END);stp.rightMargin=stp.bottomMargin=dp(3);card.addView(stopView,stp);
  if(running)card.setForeground(new RunningEdge(getResources().getDisplayMetrics().density));
  return card;
 }

 /** The compact frosted Stop: available only for a bound session with both ids, while the host has an owner-Stop route
  * and no Stop of that session is waiting for its confirmation (it stops the whole session, every app listed under
  * it); otherwise greyed, and a tap says why instead of doing nothing (never a Stop that can't be confirmed). */
 View stopButton(final CurrentApps.App a,String label,boolean inFlight){
  final boolean can=a!=null&&a.stop!=null&&stopRoute&&!inFlight;
  View g=new View(getContext());
  // The 34dp plate sits 5dp inside the 44dp target; its backdrop blur is clipped to the plate (oxblood.css:430).
  g.setBackground(new android.graphics.drawable.LayerDrawable(new Drawable[]{new BlurUnder(g,12,dp(5),dp(8),1f,density()),new FrostedStop(density(),can)}));
  g.setClickable(true);g.setFocusable(true);
  final String why=inFlight?WorkspaceOverview.STOP_STOPPING:STOP_APP_UNAVAILABLE;
  g.setContentDescription(can?"Stop Rose’s session for "+label:"Stop "+label+", unavailable. "+why);
  g.setOnClickListener(v->{if(listener==null)return;if(can)listener.stop(a);else listener.stopUnavailable(why);});
  g.setStateListAnimator(HomeAppTile.pressAnimator(getContext(),View.SCALE_X,1f,.95f,0,View.SCALE_Y));
  g.setTag(can?"stop":"stop-unavailable");
  return g;
 }
 float density(){return getResources().getDisplayMetrics().density;}

 /** The verified frame a previewed card shows, as the source of its overlays' backdrop blur (#410 gap closure;
  * oxblood.css:305,424,430 backdrop-filter blur 10-14px): reduced copies blurred at quarter scale with
  * OxbloodField.boxBlur, built lazily per sigma and dropped when the frame changes. live() is true only while the card's
  * ImageView still shows exactly this bitmap, so no overlay ever samples a stale frame or another session's. */
 static final class FrameBlur {
  final ImageView img;android.graphics.Bitmap frame;final java.util.Map<Integer,android.graphics.Bitmap> blurred=new java.util.HashMap<>();
  final java.util.List<View> overlays=new java.util.ArrayList<>();
  FrameBlur(ImageView img,android.graphics.Bitmap frame){this.img=img;this.frame=frame;}
  /** A new verified frame for the same card: the old copies are dropped (left to the GC, never recycled while a frame
   * that drew them may still be on screen) and the overlays redraw from the new one. */
  void set(android.graphics.Bitmap next){if(next==frame)return;frame=next;blurred.clear();for(View o:overlays)o.invalidate();}
  boolean live(){
   android.graphics.drawable.Drawable d=img.getDrawable();
   return frame!=null&&!frame.isRecycled()&&d instanceof android.graphics.drawable.BitmapDrawable&&((android.graphics.drawable.BitmapDrawable)d).getBitmap()==frame;
  }
  /** The frame at quarter scale blurred by sigma quarter-pixels (rounded, at least 1), or null (memory short, an
   * unreadable bitmap): the overlay then shows its frosted fill alone. */
  android.graphics.Bitmap blurred(int sigma){
   android.graphics.Bitmap b=blurred.get(sigma);if(b!=null)return b;
   try{
    int w=Math.max(1,frame.getWidth()/OxbloodField.BLUR_SCALE),h=Math.max(1,frame.getHeight()/OxbloodField.BLUR_SCALE);
    android.graphics.Bitmap small=android.graphics.Bitmap.createScaledBitmap(frame,w,h,true);
    if(small==frame)small=frame.copy(android.graphics.Bitmap.Config.ARGB_8888,true);
    else if(!small.isMutable())small=small.copy(android.graphics.Bitmap.Config.ARGB_8888,true);
    int[] px=new int[w*h];small.getPixels(px,0,w,0,0,w,h);OxbloodField.boxBlur(px,w,h,sigma);small.setPixels(px,0,w,0,0,w,h);
    blurred.put(sigma,small);return small;
   }catch(RuntimeException|OutOfMemoryError unavailable){return null;}
  }
 }
 /** An overlay's backdrop: the part of its card's verified frame that lies under it, blurred, clipped to its rounded
  * plate (inset from the view's bounds), optionally saturated; drawn under the overlay's own frosted fill. Nothing at
  * all without a live FrameBlur (no preview: the card body is flat, so the frosted fill is already what a blur of it
  * would show). */
 static final class BlurUnder extends Drawable {
  final View overlay;final float sigmaDp,inset,radius,saturation,density;FrameBlur blur;
  final android.graphics.Paint paint=new android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG|android.graphics.Paint.ANTI_ALIAS_FLAG);
  final android.graphics.Matrix inverse=new android.graphics.Matrix();final RectF area=new RectF(),src=new RectF();final android.graphics.Path clip=new android.graphics.Path();
  BlurUnder(View overlay,float sigmaDp,float inset,float radius,float saturation,float density){
   this.overlay=overlay;this.sigmaDp=sigmaDp;this.inset=inset;this.radius=radius;this.saturation=saturation;this.density=density;
   if(saturation!=1f){android.graphics.ColorMatrix m=new android.graphics.ColorMatrix();m.setSaturation(saturation);paint.setColorFilter(new android.graphics.ColorMatrixColorFilter(m));}
  }
  @Override public void draw(Canvas c){
   FrameBlur fb=blur;if(!overlayBlur||fb==null||!fb.live())return;
   android.graphics.Rect b=getBounds();if(b.isEmpty())return;
   RectF plate=new RectF(b.left+inset,b.top+inset,b.right-inset,b.bottom-inset);
   // The plate in the card (the ImageView fills the card from its origin), then back through the image matrix to frame pixels.
   area.set(plate);area.offset(overlay.getLeft(),overlay.getTop());
   if(!fb.img.getImageMatrix().invert(inverse))return;
   inverse.mapRect(src,area);
   float frameScale=area.width()/Math.max(1e-3f,src.width());
   int sigma=Math.max(1,Math.round(sigmaDp*density/Math.max(1e-3f,frameScale)/OxbloodField.BLUR_SCALE));
   android.graphics.Bitmap bl=fb.blurred(sigma);if(bl==null)return;
   float q=bl.getWidth()/(float)fb.frame.getWidth();
   android.graphics.Rect from=new android.graphics.Rect(Math.round(src.left*q),Math.round(src.top*q),Math.round(src.right*q),Math.round(src.bottom*q));
   int save=c.save();clip.reset();clip.addRoundRect(plate,radius,radius,android.graphics.Path.Direction.CW);c.clipPath(clip);
   c.drawBitmap(bl,from,plate,paint);c.restoreToCount(save);
  }
  @Override public void setAlpha(int alpha){}
  @Override public void setColorFilter(android.graphics.ColorFilter f){}
  @Override public int getOpacity(){return android.graphics.PixelFormat.TRANSLUCENT;}
 }
 /** The frosted Stop plate: 34dp, radius 8, rgba(52,34,28,.62), ring 1.25dp rgba(236,222,214,.78); the red square
  * (#f9304a, about 12.8dp, radius 1.2), grey (#8a7a76) when unavailable. */
 static final class FrostedStop extends Drawable {
  final float d;final boolean can;final android.graphics.Paint p=new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
  FrostedStop(float density,boolean can){d=density;this.can=can;}
  @Override public void draw(Canvas c){
   android.graphics.Rect b=getBounds();float cx=b.exactCenterX(),cy=b.exactCenterY(),h=17*d,r=8*d,w=1.25f*d;
   p.reset();p.setAntiAlias(true);p.setColor(0x9e34221c);c.drawRoundRect(cx-h,cy-h,cx+h,cy+h,r,r,p);
   p.setStyle(android.graphics.Paint.Style.STROKE);p.setStrokeWidth(w);p.setColor(0xc7ecded6);c.drawRoundRect(cx-h+w/2,cy-h+w/2,cx+h-w/2,cy+h-w/2,r-w/2,r-w/2,p);
   p.setStyle(android.graphics.Paint.Style.FILL);p.setColor(can?0xfff9304a:0xff8a7a76);float q=6.4f*d;c.drawRoundRect(cx-q,cy-q,cx+q,cy+q,1.2f*d,1.2f*d,p);
  }
  @Override public void setAlpha(int alpha){}
  @Override public void setColorFilter(android.graphics.ColorFilter f){}
  @Override public int getOpacity(){return android.graphics.PixelFormat.TRANSLUCENT;}
 }
 /** The lit ruby edge of a card whose app runs under Rose (oxblood.css:311-319,434-435): a 2dp ring with a constant
  * platinum-to-ruby base (top #f1cfc8, #c44a5e, #9a3848, bottom #c4574e), a short platinum-to-rose segment travelling
  * round it every 6s, and a soft ruby glow. With animations off (the system animator scale 0) the segment rests at 40
  * degrees; it runs only while the card is visible. */
 static final class RunningEdge extends Drawable {
  final float d;final android.graphics.Paint p=new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
  android.animation.ValueAnimator turn;float angle=40;
  RunningEdge(float density){d=density;}
  @Override public boolean setVisible(boolean visible,boolean restart){
   boolean changed=super.setVisible(visible,restart);
   if(visible&&android.animation.ValueAnimator.areAnimatorsEnabled()){
    if(turn==null){turn=android.animation.ValueAnimator.ofFloat(0f,360f);turn.setDuration(6000);turn.setRepeatCount(android.animation.ValueAnimator.INFINITE);
     turn.setInterpolator(new android.view.animation.LinearInterpolator());turn.addUpdateListener(a->{angle=(float)a.getAnimatedValue();invalidateSelf();});turn.start();}
   }else if(turn!=null){turn.cancel();turn=null;angle=40;invalidateSelf();}
   return changed;
  }
  @Override public void draw(Canvas c){
   android.graphics.Rect b=getBounds();if(b.isEmpty())return;
   float w=2*d,r=RADIUS_DP*d;android.graphics.RectF ring=new android.graphics.RectF(b.left+w/2,b.top+w/2,b.right-w/2,b.bottom-w/2);
   p.reset();p.setAntiAlias(true);p.setStyle(android.graphics.Paint.Style.STROKE);
   // Glow: 0 0 18px rgba(200,40,70,.22), inside the card here (the card clips to its outline).
   p.setStrokeWidth(6*d);p.setColor(0x38c82846);p.setMaskFilter(new android.graphics.BlurMaskFilter(Math.max(.1f,(9*d-.5f)/.57735f),android.graphics.BlurMaskFilter.Blur.NORMAL));
   c.drawRoundRect(ring,r,r,p);p.setMaskFilter(null);
   p.setStrokeWidth(w);
   p.setShader(new android.graphics.LinearGradient(0,b.top,0,b.bottom,new int[]{0xfff1cfc8,0xffc44a5e,0xff9a3848,0xffc4574e},new float[]{0,.14f,.55f,1},android.graphics.Shader.TileMode.CLAMP));
   c.drawRoundRect(ring,r,r,p);
   // conic-gradient(from angle): transparent to 300deg, platinum at 330, rose at 348, transparent at 360.
   android.graphics.SweepGradient sweep=new android.graphics.SweepGradient(b.exactCenterX(),b.exactCenterY(),new int[]{0x00ffffff,0x00ffffff,0xe6ffece4,0xffff6a80,0x00ff6a80},new float[]{0,300f/360,330f/360,348f/360,1});
   android.graphics.Matrix m=new android.graphics.Matrix();m.setRotate(angle-90,b.exactCenterX(),b.exactCenterY());sweep.setLocalMatrix(m);
   p.setShader(sweep);c.drawRoundRect(ring,r,r,p);p.setShader(null);
  }
  @Override public void setAlpha(int alpha){}
  @Override public void setColorFilter(android.graphics.ColorFilter f){}
  @Override public int getOpacity(){return android.graphics.PixelFormat.TRANSLUCENT;}
 }

 View dot(WorkspaceOverview.Dot d){
  View v=new View(getContext());GradientDrawable g=new GradientDrawable();g.setShape(GradientDrawable.OVAL);
  switch(d){
   case RUNNING:g.setColor(OK);break;case ATTENTION:g.setColor(ATTN);break;case DONE:g.setColor(DONE);break;case STOPPED:g.setColor(STOPPED);break;
   case STOPPING:g.setColor(Color.TRANSPARENT);g.setStroke(dp(2),STOP);break;default:g.setColor(IDLE);
  }
  v.setBackground(g);v.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);return v;
 }
 TextView text(String s,float sp,int color,boolean bold){
  TextView t=new TextView(getContext());t.setText(s);t.setTextSize(TypedValue.COMPLEX_UNIT_SP,sp);t.setTextColor(color);t.setIncludeFontPadding(false);
  t.setLineSpacing(0,1.15f);if(bold)t.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));return t;
 }
 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}

 /** HiFi icons.js line glyphs on a 24-unit grid: back chevron, end ✕, grid (four rounded squares), the Stop square
  * and Rose's mark (a rose-tinted sphere, as the HiFi's small Rose). */
 static final class Glyph extends View {
  static final int BACK=1,END=2,GRID=3,STOP=4,ROSE=5;
  final int kind,color;final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
  Glyph(Context c,int kind,int color){super(c);this.kind=kind;this.color=color;setImportantForAccessibility(kind==STOP?IMPORTANT_FOR_ACCESSIBILITY_YES:IMPORTANT_FOR_ACCESSIBILITY_NO);}
  @Override protected void onDraw(Canvas c){
   float size=kind==STOP?20:kind==ROSE?24:28,d=getResources().getDisplayMetrics().density,s=size*d/24f;
   c.save();c.translate((getWidth()-size*d)/2f,(getHeight()-size*d)/2f);c.scale(s,s);
   p.setColor(color);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);
   switch(kind){
    case BACK:{p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2.2f);Path a=new Path();a.moveTo(15,5);a.lineTo(8,12);a.lineTo(15,19);c.drawPath(a,p);break;}
    case END:p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2.2f);c.drawLine(6,6,18,18,p);c.drawLine(18,6,6,18,p);break;
    case GRID:p.setStyle(Paint.Style.FILL);for(float[] r:new float[][]{{3.5f,3.5f},{13,3.5f},{3.5f,13},{13,13}})c.drawRoundRect(new RectF(r[0],r[1],r[0]+7.5f,r[1]+7.5f),2.2f,2.2f,p);break;
    case STOP:p.setStyle(Paint.Style.FILL);c.drawRoundRect(new RectF(6.5f,6.5f,17.5f,17.5f),2.4f,2.4f,p);break;
    default:{p.setStyle(Paint.Style.FILL);p.setShader(new android.graphics.RadialGradient(9,8,13,new int[]{0xfffbe3e7,color,0xff8e4c5d},new float[]{0f,.55f,1f},android.graphics.Shader.TileMode.CLAMP));
     c.drawCircle(12,12,10,p);p.setShader(null);}
   }
   c.restore();
  }
 }
}
