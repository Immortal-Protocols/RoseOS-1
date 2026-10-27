package dev.pixel.voice;

import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.*;
import android.widget.*;
import org.json.*;

/** Native renderer for the bounded ChatKit card vocabulary used by Rose Agent. */
final class TaskWidgetView {
 private final MainActivity activity;
 private final Runnable toggle;
 private int count;
 private final int ink=PixelAppearance.color(0xff29385d),muted=PixelAppearance.color(0xff576f90);
 private TaskWidgetView(MainActivity activity,Runnable toggle){this.activity=activity;this.toggle=toggle;}
 static View create(MainActivity activity,JSONObject data,Runnable toggle){
  try{return new TaskWidgetView(activity,toggle).node(data,0);}catch(Exception invalid){return null;}
 }
 private int dp(float value){return Math.round(value*activity.getResources().getDisplayMetrics().density);}
 private int color(String name){return "secondary".equals(name)?muted:"warning".equals(name)?PixelAppearance.color(0xff8b451e):ink;}
 private GradientDrawable background(int color,int edge,int radius){GradientDrawable d=new GradientDrawable();d.setColor(color);d.setCornerRadius(dp(radius));if(edge!=0)d.setStroke(dp(1),edge);return d;}
 private View node(JSONObject data,int depth)throws JSONException{
  if(++count>100||depth>8)throw new JSONException("Widget exceeds bounds");
  String type=data.getString("type");
  if(type.equals("Card")||type.equals("Col")||type.equals("Row")||type.equals("Box")){
   LinearLayout group=new LinearLayout(activity);boolean row=type.equals("Row");group.setOrientation(row?LinearLayout.HORIZONTAL:LinearLayout.VERTICAL);
   String align=data.optString("align","stretch");int gravity="end".equals(align)?Gravity.END:"center".equals(align)?Gravity.CENTER_HORIZONTAL:Gravity.START;
   group.setGravity(row?Gravity.CENTER_VERTICAL:gravity);group.setBaselineAligned(false);
   // The existing task surface supplies the card background and outer spacing.
   // A generated Card is a structural group, not another white box inside it.
   int pad=type.equals("Card")?0:dp(4*Math.max(0,Math.min(6,data.optInt("padding",0))));group.setPadding(pad,pad,pad,pad);
   JSONArray children=data.getJSONArray("children");int gap=dp(4*Math.max(0,Math.min(6,data.optInt("gap",0))));
   for(int i=0;i<children.length();i++){
    JSONObject child=children.getJSONObject(i);View view=node(child,depth+1);
    LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(row?0:(align.equals("stretch")?-1:-2),-2,row?1:0);
    if(row){if(i>0)p.leftMargin=gap;}else if(i>0)p.topMargin=gap;
    group.addView(view,p);
   }
   group.setOnClickListener(v->toggle.run());return group;
  }
  if(type.equals("Divider")){View line=new View(activity);line.setMinimumHeight(dp(1));line.setBackgroundColor(PixelAppearance.color(0xffd4d9d9));return line;}
  if(!type.equals("Title")&&!type.equals("Text")&&!type.equals("Caption")&&!type.equals("Badge")&&!type.equals("Markdown"))throw new JSONException("Unsupported component");
  TextView text=new TextView(activity);String size=data.optString("size","md"),weight=data.optString("weight","normal");
  boolean title=type.equals("Title"),caption=type.equals("Caption"),badge=type.equals("Badge");
  int fontSize=title?(size.equals("lg")?24:size.equals("sm")?20:22):caption?14:badge?14:size.equals("lg")?22:size.equals("sm")?16:18;
  int fontWeight=title?800:badge?800:weight.equals("bold")?900:weight.equals("semibold")?800:weight.equals("medium")?700:600;
  text.setTextSize(fontSize);text.setTypeface(activity.chatFont(fontWeight));
  text.setIncludeFontPadding(false);text.setLineSpacing(dp(3),1);text.setTextColor(caption?muted:color(data.optString("color")));
  String value=badge?data.getString("label"):data.getString("value");
  if(value.length()>12000)throw new JSONException("Text exceeds bounds");
  if(type.equals("Markdown")){TaskText.set(text,value);text.setLinkTextColor(PixelAppearance.color(0xff008d80));}
  else{text.setText(value);text.setOnClickListener(v->toggle.run());}
  if(title)text.setAccessibilityHeading(true);
  if(badge){String tone=data.optString("color");int bg=tone.equals("warning")?0xffffe9dd:tone.equals("success")?0xffe5f3ea:tone.equals("info")?0xffe8effa:0xffeef0f3;text.setBackground(background(PixelAppearance.color(bg),0,5));text.setPadding(dp(8),dp(4),dp(8),dp(4));}
  return text;
 }
}
