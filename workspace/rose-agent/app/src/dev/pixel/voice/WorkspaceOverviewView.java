package dev.pixel.voice;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Draws a WorkspaceOverview.Model natively: the one real workspace card, then the logical task rows. Styling
 * follows the selected HiFi (prototypes/rose-hifi at ce126f56, style.css): .wcard (22dp radius, white at .86),
 * .sdot state colours, .task-strip and the round .stop-btn; ink #141922 / #5b6680 on the ivory surface. It only
 * renders and reports taps to its Listener; it never starts, stops or foregrounds anything itself. */
final class WorkspaceOverviewView extends LinearLayout {
 interface Listener {
  void select(WorkspaceOverview.Identity task);
  void watch(WorkspaceOverview.Identity task);
  void stop(WorkspaceOverview.Identity task);
 }
 static final int INK=0xff141922,INK2=0xff5b6680,INK3=0xff8a93a6,OK=0xff4fb36a,ATTN=0xffe6a23a,STOP=0xffd9534a,STOPPED=0xff8e98a6,DONE=0xff5b8def,IDLE=0xffaeb7c4,
  CARD=0xdbffffff,SELECTED=0xffffffff,LINE=0x1a1e3250;
 private Listener listener;private String lastKey="";

 WorkspaceOverviewView(Context c){super(c);setOrientation(VERTICAL);}
 void setListener(Listener l){listener=l;}

 /** Rebuilds only when the model's key changes, so repeated reads and re-entry redraw nothing. */
 void show(WorkspaceOverview.Model m){
  if(m==null)return;String key=m.key();if(key.equals(lastKey))return;lastKey=key;
  removeAllViews();
  TextView head=text(WorkspaceOverview.TITLE,22,INK,true);head.setPadding(0,0,0,dp(12));addView(head);
  addView(card(m.card));
  if(m.rows.isEmpty()){TextView e=text(m.empty,14,INK2,false);e.setPadding(dp(4),dp(16),dp(4),0);addView(e);return;}
  TextView sub=text("Tasks",13,INK2,true);sub.setPadding(dp(4),dp(18),0,dp(6));addView(sub);
  for(WorkspaceOverview.Row r:m.rows)addView(row(r));
 }

 View card(WorkspaceOverview.Card c){
  LinearLayout box=new LinearLayout(getContext());box.setOrientation(VERTICAL);box.setPadding(dp(16),dp(14),dp(16),dp(14));
  box.setBackground(round(CARD,22,true));
  LinearLayout top=new LinearLayout(getContext());top.setGravity(Gravity.CENTER_VERTICAL);
  top.addView(dot(c.dot));TextView l=text(c.label,16,INK,true);l.setPadding(dp(10),0,0,0);top.addView(l);
  box.addView(top);
  TextView d=text(c.detail,13,INK2,false);d.setPadding(dp(19),dp(4),0,0);box.addView(d);
  box.setContentDescription("Rose’s workspace: "+c.label+". "+c.detail);
  return box;
 }

 View row(final WorkspaceOverview.Row r){
  LinearLayout box=new LinearLayout(getContext());box.setOrientation(VERTICAL);box.setPadding(dp(14),dp(12),dp(12),dp(12));
  box.setBackground(round(r.selected?SELECTED:CARD,20,r.selected));
  LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT);lp.topMargin=dp(8);box.setLayoutParams(lp);
  LinearLayout top=new LinearLayout(getContext());top.setGravity(Gravity.CENTER_VERTICAL);
  top.addView(dot(r.dot));
  LinearLayout words=new LinearLayout(getContext());words.setOrientation(VERTICAL);words.setPadding(dp(10),0,dp(8),0);
  TextView t=text(r.title,15,INK,true);t.setMaxLines(2);t.setEllipsize(TextUtils.TruncateAt.END);words.addView(t);
  words.addView(text(r.state+(r.detail.isEmpty()?"":" · "+r.detail),12.5f,INK2,false));
  top.addView(words,new LinearLayout.LayoutParams(0,LayoutParams.WRAP_CONTENT,1f));
  if(r.canStop){
   Button stop=new Button(getContext());stop.setText("Stop");stop.setAllCaps(false);stop.setTextColor(STOP);stop.setTextSize(TypedValue.COMPLEX_UNIT_SP,13);
   stop.setBackground(round(0xfffde9e6,20,false));stop.setMinWidth(0);stop.setMinimumWidth(0);stop.setMinHeight(dp(44));stop.setMinimumHeight(dp(44));
   stop.setPadding(dp(14),0,dp(14),0);stop.setContentDescription("Stop "+r.title);
   stop.setOnClickListener(v->{if(listener!=null)listener.stop(r.identity);});top.addView(stop);
  }
  box.addView(top);
  if(!r.result.isEmpty()){TextView res=text(r.result,14,INK,false);res.setPadding(dp(19),dp(6),0,0);res.setMaxLines(4);res.setEllipsize(TextUtils.TruncateAt.END);box.addView(res);}
  if(!r.stopNote.isEmpty()){TextView n=text(r.stopNote,12.5f,r.stopNote.equals(WorkspaceOverview.STOP_UNKNOWN)||r.stopNote.equals(WorkspaceOverview.STOP_REFUSED)?STOP:INK2,false);n.setPadding(dp(19),dp(6),0,0);box.addView(n);}
  if(r.selected){
   if(r.canWatch){
    Button w=new Button(getContext());w.setText("Watch");w.setAllCaps(false);w.setTextColor(INK);w.setTextSize(TypedValue.COMPLEX_UNIT_SP,14);
    w.setBackground(round(0xffeef2f7,22,false));w.setMinHeight(dp(44));w.setMinimumHeight(dp(44));w.setContentDescription(WorkspaceOverview.WATCH_OK);
    w.setOnClickListener(v->{if(listener!=null)listener.watch(r.identity);});
    LinearLayout.LayoutParams wp=new LinearLayout.LayoutParams(LayoutParams.WRAP_CONTENT,LayoutParams.WRAP_CONTENT);wp.topMargin=dp(10);wp.leftMargin=dp(19);box.addView(w,wp);
   }else{TextView why=text(r.watchReason,12.5f,INK3,false);why.setPadding(dp(19),dp(8),0,0);box.addView(why);}
  }
  box.setClickable(true);box.setFocusable(true);box.setContentDescription(r.description());
  box.setOnClickListener(v->{if(listener!=null)listener.select(r.identity);});
  return box;
 }

 View dot(WorkspaceOverview.Dot d){
  View v=new View(getContext());GradientDrawable g=new GradientDrawable();g.setShape(GradientDrawable.OVAL);
  switch(d){
   case RUNNING:g.setColor(OK);break;case ATTENTION:g.setColor(ATTN);break;case DONE:g.setColor(DONE);break;case STOPPED:g.setColor(STOPPED);break;
   case STOPPING:g.setColor(Color.TRANSPARENT);g.setStroke(dp(2),STOP);break;default:g.setColor(IDLE);
  }
  v.setBackground(g);v.setLayoutParams(new LinearLayout.LayoutParams(dp(9),dp(9)));v.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);return v;
 }
 TextView text(String s,float sp,int color,boolean bold){
  TextView t=new TextView(getContext());t.setText(s);t.setTextSize(TypedValue.COMPLEX_UNIT_SP,sp);t.setTextColor(color);
  if(bold)t.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));return t;
 }
 GradientDrawable round(int fill,int radiusDp,boolean ring){
  GradientDrawable g=new GradientDrawable();g.setColor(fill);g.setCornerRadius(dp(radiusDp));if(ring)g.setStroke(Math.max(1,dp(1)/2),LINE);return g;
 }
 int dp(float v){return Math.round(v*getResources().getDisplayMetrics().density);}
}
