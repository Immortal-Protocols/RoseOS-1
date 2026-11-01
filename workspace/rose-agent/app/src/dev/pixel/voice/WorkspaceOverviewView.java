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
 * style.css .agents-surface/.agrid/.wcard/.wbody/.sdot/.stop-btn/.a-empty), drawn natively from a
 * WorkspaceOverview.Model. No visible heading (the HiFi h2 is screen-reader only); a two-column grid of 250dp cards
 * with 12dp gaps; each card is a 22dp-radius white(.86) card with a 24dp icon and label header, a #f3f6fa body and
 * a footer of status dot, short state and the round 44dp Stop.
 *
 * Truth over decoration: the controller's ONE workspace is the only window card (its real app icon and name; its body
 * says what the task it is verifiably linked to is doing, or why it is not linked). Logical tasks are task cards
 * (Rose's mark, the real goal, progress or result), never shown as extra app windows. Finished cards are muted like
 * the HiFi's done/stopped windows. It only renders and reports taps; it starts, stops or opens nothing itself. */
final class WorkspaceOverviewView extends LinearLayout {
 interface Listener {
  void select(WorkspaceOverview.Identity task);
  void watch(WorkspaceOverview.Identity task);
  void stop(WorkspaceOverview.Identity task);
  /** The workspace card was tapped. */
  void workspace();
  /** A Stop that is not available was tapped: say why instead of doing nothing. */
  void stopUnavailable(String reason);
 }
 /** The workspace app's real launcher icon, or null (the card then shows no icon rather than a made-up one). */
 interface Icons { Drawable icon(String pkg); }
 static final int INK=0xff141922,INK2=0xff5b6680,INK3=0xff8a93a6,OK=0xff4fb36a,ATTN=0xffe6a23a,STOP=0xffd9534a,STOPPED=0xff8e98a6,DONE=0xff5b8def,IDLE=0xffaeb7c4,
  CARD=0xdbffffff,BODY=0xfff3f6fa,RING=0x2e788caa,ROSE=0xffc56f78;
 static final int CARD_DP=250,GAP_DP=12;
 private Listener listener;private Icons icons;private String lastKey="";
 private final TextView notice;
 /** The workspace card's live image view while a verified frame is shown, and the frame it holds. */
 private ImageView previewView;private android.graphics.Bitmap shown,preview;

 WorkspaceOverviewView(Context c){
  super(c);setOrientation(VERTICAL);
  notice=text("",13,INK3,false);notice.setGravity(Gravity.CENTER);notice.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
 }
 void setListener(Listener l){listener=l;}
 void setIcons(Icons i){icons=i;lastKey="";}

 /** Rebuilds only when the model's key changes, so repeated reads and re-entry redraw nothing. */
 void show(WorkspaceOverview.Model m){show(m,"",null);}
 void show(WorkspaceOverview.Model m,String note){show(m,note,null);}
 /** note: the HiFi observe line under the grid (why the last tap opened nothing); empty hides it. preview: the
  * workspace card's accepted live frame (WorkspaceOverviewPreview, already checked against the card's verified
  * binding), or null for the text body. A new frame for the same card only swaps the image. */
 void show(WorkspaceOverview.Model m,String note,WorkspaceOverviewPreview.Frame preview){
  if(m==null)return;
  android.graphics.Bitmap image=preview!=null&&preview.image instanceof android.graphics.Bitmap&&m.watchRow!=null?(android.graphics.Bitmap)preview.image:null;
  String key=m.key()+"\n"+note+"\n"+(image!=null);
  if(key.equals(lastKey)){if(image!=null&&previewView!=null&&shown!=image){previewView.setImageBitmap(image);shown=image;fitTop(previewView);}return;}
  lastKey=key;previewView=null;shown=null;this.preview=image;
  removeAllViews();
  if(!m.empty.isEmpty()){
   TextView e=text(m.empty,15,INK2,false);e.setGravity(Gravity.CENTER);
   LayoutParams ep=new LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT);ep.topMargin=dp(220);addView(e,ep);
   return;
  }
  java.util.List<View> cards=new java.util.ArrayList<>();
  if(m.card.shown())cards.add(workspaceCard(m));
  for(WorkspaceOverview.Row r:m.rows)cards.add(taskCard(r));
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

 /** The one real workspace: its app's icon and name, what its linked task is doing (or why none is linked), state. */
 View workspaceCard(WorkspaceOverview.Model m){
  WorkspaceOverview.Card c=m.card;
  Drawable icon=c.pkg.isEmpty()||icons==null?null:icons.icon(c.pkg);
  WorkspaceOverview.Row linked=m.watchRow;
  String headline=linked!=null?linked.title:c.label,body=linked!=null?(linked.detail.isEmpty()?linked.state:linked.detail):c.detail;
  View card=card(icon==null?null:new ImageView(getContext()),icon,c.app,headline,body,c.dot,c.shortState,null,false,c.dot==WorkspaceOverview.Dot.STOPPED);
  if(preview!=null&&linked!=null)showPreview((LinearLayout)card,c.app);
  card.setContentDescription("Rose’s workspace: "+c.app+", "+c.shortState+". "+(linked!=null?WorkspaceOverview.WATCH_OK:m.cardReason()));
  card.setOnClickListener(v->{if(listener!=null)listener.workspace();});
  card.setTag("workspace");
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
 /** A logical task: Rose's mark, its real goal, progress or result, state and its Stop (never an app window). */
 View taskCard(final WorkspaceOverview.Row r){
  String body=!r.detail.isEmpty()?r.detail:!r.result.isEmpty()?r.result:r.state;
  String state=r.stopNote.isEmpty()||r.stopNote.equals(WorkspaceOverview.STOP_NO_ROUTE)?r.state:r.stopNote;
  boolean muted=r.dot==WorkspaceOverview.Dot.DONE||r.dot==WorkspaceOverview.Dot.STOPPED;
  View card=card(new Glyph(getContext(),Glyph.ROSE,ROSE),null,r.title,"",body,r.dot,state,r,true,muted);
  card.setContentDescription(r.description());
  card.setOnClickListener(v->{if(listener!=null)listener.select(r.identity);});
  card.setTag("task:"+r.identity.id);
  return card;
 }

 View card(View iconView,Drawable icon,String label,String headline,String body,WorkspaceOverview.Dot dot,String state,final WorkspaceOverview.Row row,boolean task,boolean muted){
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
  foot.addView(stopButton(row),new LayoutParams(dp(44),dp(44)));
  card.addView(foot);
  if(muted)card.setAlpha(.82f);
  return card;
 }

 /** .stop-btn: a 44dp round button with a red rounded square; greyed (.stop-btn:disabled) when Stop isn't available,
  * yet a tap then says why. The workspace card's Stop is always unavailable: Stop belongs to a task. */
 View stopButton(final WorkspaceOverview.Row r){
  boolean can=r!=null&&r.canStop;
  Glyph g=new Glyph(getContext(),Glyph.STOP,can?STOP:0xffb8c0cb);
  GradientDrawable bg=new GradientDrawable(GradientDrawable.Orientation.TL_BR,can?new int[]{0xffffffff,0xfffbe9e6}:new int[]{0xfff1f4f8,0xfff1f4f8});
  bg.setShape(GradientDrawable.OVAL);bg.setStroke(Math.max(1,dp(1)),can?0x38d9534a:0x2e788caa);g.setBackground(bg);
  g.setClickable(true);g.setFocusable(true);
  String title=r==null?"this workspace":r.title;
  String why=r==null?"Stop belongs to a task, not to the workspace.":r.stopNote.isEmpty()?(r.dot==WorkspaceOverview.Dot.RUNNING?WorkspaceOverview.STOP_NO_ROUTE:"This task isn’t running."):r.stopNote;
  g.setContentDescription(can?"Stop "+title:"Stop "+title+", unavailable. "+why);
  g.setOnClickListener(v->{if(listener==null)return;if(can)listener.stop(r.identity);else listener.stopUnavailable(why);});
  g.setTag(can?"stop":"stop-unavailable");
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
