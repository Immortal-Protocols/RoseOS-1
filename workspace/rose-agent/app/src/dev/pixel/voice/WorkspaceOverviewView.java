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

/** The selected HiFi's Rose's apps surface (prototypes/rose-hifi at ce126f56: views.js renderAgents/windowCard,
 * style.css .agents-surface/.agrid/.wcard/.wbody/.sdot/.stop-btn/.a-empty), drawn natively. No visible heading (the
 * HiFi h2 is screen-reader only); a two-column grid of 250dp cards with 12dp gaps; each card is a 22dp-radius
 * white(.86) card with a 24dp icon and label header, a #f3f6fa body and a footer of status dot, short state and the
 * round 44dp Stop.
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
 }
 /** The app's real launcher icon, or null (the card then shows no icon rather than a made-up one). */
 interface Icons { Drawable icon(String pkg); }
 /** The app's visible name, or "" (the card then says Rose's app rather than a guessed name). */
 interface Labels { String label(String pkg); }
 static final int INK=0xff141922,INK2=0xff5b6680,INK3=0xff8a93a6,OK=0xff4fb36a,ATTN=0xffe6a23a,STOP=0xffd9534a,STOPPED=0xff8e98a6,DONE=0xff5b8def,IDLE=0xffaeb7c4,
  CARD=0xdbffffff,BODY=0xfff3f6fa,RING=0x2e788caa,ROSE=0xffc56f78;
 static final int CARD_DP=250,GAP_DP=12;
 /** No live content for this app yet: said plainly, never an icon or picture standing in for it. */
 static final String LIVE_UNAVAILABLE="Live view isn’t available for this app yet.";
 static final String STOP_APP_UNAVAILABLE="Stopping an app from here isn’t available yet.";
 private Listener listener;private Icons icons;private Labels labels;private String lastKey="";
 private final TextView notice;
 /** The previewed card's live image view while a verified frame is shown, and the frame it holds. */
 private ImageView previewView;private android.graphics.Bitmap shown,preview;

 WorkspaceOverviewView(Context c){
  super(c);setOrientation(VERTICAL);
  notice=text("",13,INK3,false);notice.setGravity(Gravity.CENTER);notice.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
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
  android.graphics.Bitmap image=frame!=null&&frame.image instanceof android.graphics.Bitmap&&previewEntry!=null?(android.graphics.Bitmap)frame.image:null;
  StringBuilder k=new StringBuilder().append(state).append('|').append(reason).append('|').append(note).append('|').append(image!=null?previewEntry:"-");
  for(CurrentApps.App a:apps)k.append('|').append(a.entryId).append(':').append(a.packageName).append(':').append(a.state);
  String key=k.toString();
  if(key.equals(lastKey)){if(image!=null&&previewView!=null&&shown!=image){previewView.setImageBitmap(image);shown=image;fitTop(previewView);}return;}
  lastKey=key;previewView=null;shown=null;this.preview=image;
  removeAllViews();
  if(state!=CurrentApps.State.AVAILABLE){
   // HiFi .a-empty: nothing running under control, or why it can't be known; never stale cards.
   TextView e=text(state==CurrentApps.State.EMPTY?WorkspaceOverview.EMPTY:CurrentApps.text(reason),15,INK2,false);e.setGravity(Gravity.CENTER);
   e.setTag(state==CurrentApps.State.EMPTY?"empty":"unavailable");
   LayoutParams ep=new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT);ep.topMargin=dp(220);addView(e,ep);
   return;
  }
  java.util.List<View> cards=new java.util.ArrayList<>();
  for(CurrentApps.App a:apps)cards.add(appCard(a,image!=null&&a.entryId.equals(previewEntry)));
  for(int i=0;i<cards.size();i+=2){
   LinearLayout row=new LinearLayout(getContext());row.setOrientation(HORIZONTAL);
   LayoutParams rp=new LayoutParams(LayoutParams.MATCH_PARENT,dp(CARD_DP));if(i>0)rp.topMargin=dp(GAP_DP);
   LayoutParams a=new LayoutParams(0,LayoutParams.MATCH_PARENT,1f);row.addView(cards.get(i),a);
   LayoutParams b=new LayoutParams(0,LayoutParams.MATCH_PARENT,1f);b.leftMargin=dp(GAP_DP);
   if(i+1<cards.size())row.addView(cards.get(i+1),b);else row.addView(new View(getContext()),b);
   addView(row,rp);
  }
  notice.setText(note);
  if(!note.isEmpty()){if(notice.getParent()!=null)((ViewGroup)notice.getParent()).removeView(notice);
   LayoutParams np=new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT);np.topMargin=dp(14);addView(notice,np);}
 }

 /** One app running under Rose's control: its real icon and name, its live frame when verified (otherwise the plain
  * statement that live view isn't available), its state, and a Stop that says why it isn't available. */
 View appCard(final CurrentApps.App a,boolean live){
  Drawable icon=icons==null?null:icons.icon(a.packageName);
  String label=labels==null?"":labels.label(a.packageName);if(label==null||label.isEmpty())label=WorkspaceOverview.WORKSPACE;
  WorkspaceOverview.Dot dot=CurrentApps.STATE_WAITING.equals(a.state)?WorkspaceOverview.Dot.ATTENTION:WorkspaceOverview.Dot.RUNNING;
  String st=CurrentApps.STATE_WAITING.equals(a.state)?"Waiting":CurrentApps.STATE_STARTING.equals(a.state)?"Starting":"Running";
  View card=card(icon==null?null:new ImageView(getContext()),icon,label,"",LIVE_UNAVAILABLE,dot,st,false,false);
  if(live)showPreview((LinearLayout)card,label);
  card.setContentDescription(label+", "+st+(live?". Live view":". "+LIVE_UNAVAILABLE));
  card.setOnClickListener(v->{if(listener!=null)listener.app(a.entryId);});
  card.setTag("app:"+a.entryId);
  return card;
 }
 /** Replaces the window card's text body with its live frame (HiFi .wbody content), top-aligned and cropped to the
  * body like the HiFi's window content; the text stays as the spoken description. */
 void showPreview(LinearLayout card,String app){
  View body=card.getChildAt(1);LayoutParams lp=(LayoutParams)body.getLayoutParams();
  FrameLayout frame=new FrameLayout(getContext());GradientDrawable bb=new GradientDrawable();bb.setColor(BODY);bb.setCornerRadius(dp(14));frame.setBackground(bb);frame.setClipToOutline(true);
  ImageView img=new ImageView(getContext());img.setScaleType(ImageView.ScaleType.MATRIX);img.setImageBitmap(preview);
  img.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->fitTop(img));
  img.setContentDescription("Live view of Rose’s workspace in "+app);img.setTag("preview");
  frame.addView(img,new FrameLayout.LayoutParams(-1,-1));
  card.removeViewAt(1);card.addView(frame,1,lp);previewView=img;shown=preview;
 }
 /** Scales the frame to the body's width and pins its top (the app's top bar), cropping only the bottom. */
 static void fitTop(ImageView img){
  android.graphics.drawable.Drawable d=img.getDrawable();if(d==null||img.getWidth()==0||d.getIntrinsicWidth()<=0)return;
  float s=Math.max(img.getWidth()/(float)d.getIntrinsicWidth(),img.getHeight()/(float)d.getIntrinsicHeight());
  android.graphics.Matrix m=new android.graphics.Matrix();m.setScale(s,s);m.postTranslate((img.getWidth()-d.getIntrinsicWidth()*s)/2f,0);img.setImageMatrix(m);
 }
 View card(View iconView,Drawable icon,String label,String headline,String body,WorkspaceOverview.Dot dot,String state,boolean task,boolean muted){
  LinearLayout card=new LinearLayout(getContext());card.setOrientation(VERTICAL);card.setClickable(true);card.setFocusable(true);
  GradientDrawable bg=new GradientDrawable();bg.setColor(CARD);bg.setCornerRadius(dp(22));bg.setStroke(Math.max(1,dp(1)),RING);card.setBackground(bg);
  card.setElevation(dp(6));card.setClipToOutline(true);
  // header: 24dp icon + label (.wcard header: 12/14/8 padding, 13px 550)
  LinearLayout head=new LinearLayout(getContext());head.setGravity(Gravity.CENTER_VERTICAL);head.setPadding(dp(14),dp(12),dp(14),dp(8));
  if(iconView!=null){if(iconView instanceof ImageView)((ImageView)iconView).setImageDrawable(icon);head.addView(iconView,new LayoutParams(dp(24),dp(24)));}
  TextView l=text(label,13,INK,true);l.setSingleLine(true);l.setEllipsize(TextUtils.TruncateAt.END);l.setPadding(iconView==null?0:dp(8),0,0,0);head.addView(l,new LayoutParams(0,LayoutParams.WRAP_CONTENT,1f));
  card.addView(head);
  // body (.wbody: 0 10dp margin, 14dp radius, #f3f6fa)
  LinearLayout b=new LinearLayout(getContext());b.setOrientation(VERTICAL);b.setPadding(dp(12),dp(12),dp(12),dp(12));
  GradientDrawable bb=new GradientDrawable();bb.setColor(BODY);bb.setCornerRadius(dp(14));b.setBackground(bb);
  if(!headline.isEmpty()){TextView h=text(headline,14,INK,true);h.setMaxLines(3);h.setEllipsize(TextUtils.TruncateAt.END);b.addView(h);}
  if(!body.isEmpty()){TextView t=text(body,12.5f,INK2,false);t.setMaxLines(task?7:5);t.setEllipsize(TextUtils.TruncateAt.END);if(!headline.isEmpty())t.setPadding(0,dp(6),0,0);b.addView(t);}
  b.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
  LayoutParams bp=new LayoutParams(LayoutParams.MATCH_PARENT,0,1f);bp.leftMargin=bp.rightMargin=dp(10);card.addView(b,bp);
  if(muted)b.setAlpha(.6f);
  // footer: dot, short state, round Stop (.wcard footer 8/8/8/14)
  LinearLayout foot=new LinearLayout(getContext());foot.setGravity(Gravity.CENTER_VERTICAL);foot.setPadding(dp(14),dp(8),dp(8),dp(8));
  foot.addView(dot(dot),new LayoutParams(dp(9),dp(9)));
  TextView st=text(state,11.5f,INK2,false);st.setSingleLine(true);st.setEllipsize(TextUtils.TruncateAt.END);st.setPadding(dp(7),0,dp(4),0);
  foot.addView(st,new LayoutParams(0,LayoutParams.WRAP_CONTENT,1f));
  foot.addView(stopButton(label),new LayoutParams(dp(44),dp(44)));
  card.addView(foot);
  if(muted)card.setAlpha(.82f);
  return card;
 }

 /** .stop-btn: a 44dp round button with a red rounded square, greyed (.stop-btn:disabled): no route releases an app's
  * control from here yet, so a tap says why instead of doing nothing (never a Stop that can't be confirmed). */
 View stopButton(String label){
  Glyph g=new Glyph(getContext(),Glyph.STOP,0xffb8c0cb);
  GradientDrawable bg=new GradientDrawable();bg.setColor(0xfff1f4f8);
  bg.setShape(GradientDrawable.OVAL);bg.setStroke(Math.max(1,dp(1)),0x2e788caa);g.setBackground(bg);
  g.setClickable(true);g.setFocusable(true);
  g.setContentDescription("Stop "+label+", unavailable. "+STOP_APP_UNAVAILABLE);
  g.setOnClickListener(v->{if(listener!=null)listener.stopUnavailable(STOP_APP_UNAVAILABLE);});
  g.setTag("stop-unavailable");
  return g;
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
