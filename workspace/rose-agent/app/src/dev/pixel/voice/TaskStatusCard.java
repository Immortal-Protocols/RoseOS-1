package dev.pixel.voice;

import android.view.*;
import android.widget.*;
import org.json.*;

/** Presentation only: never submits, retries, stops or evaluates a task. */
final class TaskStatusCard extends LinearLayout {
 private final MainActivity activity;
 private JSONObject current;
 private String lastRender="",observedId="",observedState="",observedPresentation="";
 private boolean available,expanded,detailsTouched;
 private TaskHtmlView htmlView;
 private final int ink=PixelAppearance.color(0xff29385d),muted=PixelAppearance.color(0xff576f90);
 TaskStatusCard(MainActivity activity){
  super(activity);this.activity=activity;setOrientation(VERTICAL);setPadding(dp(20),dp(18),dp(20),dp(22));
  setBackground(PlayfulTheme.surface(PixelAppearance.color(0xfffff7e5),PixelAppearance.color(0xff789ab5),getResources().getDisplayMetrics().density,24));
  setOnClickListener(v->toggleDetails());setVisibility(GONE);
 }
 private void toggleDetails(){if(current!=null){detailsTouched=true;expanded=!expanded;draw();}}
 private boolean richMode(){return activity.getSharedPreferences("task-card",0).getBoolean("rich",true);}
 private String presentation(JSONObject task){return !richMode()||!task.optString("state").equals("completed")?"text":task.optJSONObject("widget")!=null||task.optJSONObject("visualization")!=null?"ready":task.optString("widgetStatus").equals("unavailable")?"unavailable":"preparing";}
 void dispose(){if(htmlView!=null){htmlView.dispose();htmlView=null;}}
 private int dp(float n){return Math.round(n*getResources().getDisplayMetrics().density);}
 private TextView label(String value,int size,int weight,int color){TextView t=new TextView(activity);t.setText(value);t.setTextSize(size);t.setTextColor(color);t.setTypeface(activity.chatFont(weight));t.setIncludeFontPadding(false);t.setLineSpacing(dp(3),1);return t;}
 private void addSpaced(View view,int top){LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.topMargin=dp(top);addView(view,p);}
 void render(String raw,boolean available,boolean canAutoOpen){
  String signature=raw+"|"+available+"|"+canAutoOpen+"|"+activity.getSharedPreferences("task-card",0).getBoolean("rich",true);if(signature.equals(lastRender))return;lastRender=signature;
  try{
   JSONObject next=new JSONObject(raw).optJSONObject("current");if(next==null){current=null;observedId="";observedState="";setVisibility(GONE);return;}
   String id=next.optString("id"),state=next.optString("state");
   String nextPresentation=presentation(next);
   if(!id.equals(observedId)){expanded=false;detailsTouched=false;}
   // New completion transitions open details once. A later title or reopening
   // the app must not reopen details the user has already collapsed.
   if(available&&canAutoOpen&&!detailsTouched&&id.equals(observedId)&&state.equals("completed")&&!nextPresentation.equals("preparing")&&(!observedState.equals("completed")||observedPresentation.equals("preparing")))expanded=true;
   current=next;this.available=available;if(available){observedId=id;observedState=state;observedPresentation=nextPresentation;}draw();
  }catch(JSONException e){current=null;setVisibility(GONE);}
 }
 private void draw(){
   if(current==null)return;removeAllViews();dispose();setVisibility(VISIBLE);
   String state=current.optString("state"),stateLabel;
   switch(state){case "running":stateLabel="Running";break;case "checking":stateLabel="Checking result";break;case "completed":stateLabel="Finished";break;case "failed":stateLabel="Failed";break;case "interrupted":stateLabel="Stopped";break;default:stateLabel="Status unavailable";}
   boolean active=state.equals("running")||state.equals("checking");
   String heading=available?(active?"CURRENT TASK · ":"LAST TASK · ")+stateLabel:"TASK · Status unavailable";
   String update=active?current.optString("activity"):current.optString("result");
   if(active&&update.isEmpty())update=current.optString("progress");
   if(available&&state.equals("checking"))update="Checking the result before finishing.";
   if(!available)update="Connection lost. Showing the last received task.";
   if(update.isEmpty())update=active?"Working on your request.":"No additional details.";
   String display=presentation(current);
   if(available&&display.equals("preparing"))update="Preparing visual…";
   if(available&&display.equals("unavailable"))update="Visual unavailable. Hold the card to read the original answer.";
   LinearLayout top=new LinearLayout(activity);top.setGravity(Gravity.CENTER_VERTICAL);
   TextView badge=label(available?stateLabel:"Offline",12,800,available&&state.equals("completed")?PixelAppearance.color(0xff3e7259):muted);
   badge.setPadding(dp(10),dp(6),dp(10),dp(6));badge.setBackground(activity.shape(PixelAppearance.color(state.equals("completed")?0xffe5efdf:0xffe5ecf5),12));top.addView(badge);
   addView(top);
   String taskTitle=current.optString("title","Current task");TextView title=label(taskTitle,22,900,ink);title.setMaxLines(1);title.setAutoSizeTextTypeUniformWithConfiguration(18,22,1,android.util.TypedValue.COMPLEX_UNIT_SP);title.setEllipsize(android.text.TextUtils.TruncateAt.END);title.setAccessibilityHeading(true);addSpaced(title,12);
   if(expanded){
    JSONObject widget=current.optJSONObject("widget");
    JSONObject visualization=current.optJSONObject("visualization");
    View rich=state.equals("completed")&&activity.getSharedPreferences("task-card",0).getBoolean("rich",true)&&widget!=null?TaskWidgetView.create(activity,widget,()->toggleDetails()):null;
    if(state.equals("completed")&&richMode()&&visualization!=null&&"html".equals(visualization.optString("type"))){htmlView=new TaskHtmlView(activity,visualization.optString("html"));rich=htmlView;}
    if(rich!=null)addSpaced(rich,18);
    else{TextView result=label("",18,600,ink);result.setLineSpacing(dp(7),1);TaskText.set(result,update);result.setTextIsSelectable(false);result.setOnClickListener(v->toggleDetails());addSpaced(result,18);}
   }else{
    TextView progress=label("",15,600,muted);TaskText.set(progress,update);progress.setTextIsSelectable(false);progress.setOnClickListener(v->toggleDetails());progress.setMaxLines(2);progress.setEllipsize(android.text.TextUtils.TruncateAt.END);addSpaced(progress,10);
   }
   setClickable(true);setFocusable(true);setContentDescription(expanded?"Task details. "+taskTitle+". Tap to collapse":heading+". "+taskTitle+". "+update+". Tap to expand");
   setOnLongClickListener(v->{new android.app.AlertDialog.Builder(activity).setTitle("Original answer").setMessage(current.optString("result")).setPositiveButton("Close",null).show();return true;});
 }
}
