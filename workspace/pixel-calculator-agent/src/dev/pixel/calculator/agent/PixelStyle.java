package dev.pixel.calculator.agent;
import android.app.Activity;
import android.graphics.*;
import android.graphics.drawable.*;
import android.view.*;
import android.widget.*;
import java.util.*;

final class PixelStyle {
 static Drawable raised(int color,float density,float radius,boolean dark){
  GradientDrawable shadow=new GradientDrawable();shadow.setColor(dark?0xff1a2644:0xff9eb8d0);shadow.setCornerRadius(radius*density);
  GradientDrawable face=new GradientDrawable();face.setColor(color);face.setCornerRadius(radius*density);face.setStroke(Math.max(1,(int)density),dark?0x44b9dff3:0x2529385d);
  LayerDrawable layers=new LayerDrawable(new Drawable[]{shadow,face});layers.setLayerInset(0,0,(int)(3*density),0,0);layers.setLayerInset(1,0,0,0,(int)(3*density));return layers;
 }
 static GradientDrawable face(Drawable d){if(d instanceof GradientDrawable)return (GradientDrawable)d;if(d instanceof LayerDrawable){LayerDrawable layers=(LayerDrawable)d;for(int i=layers.getNumberOfLayers()-1;i>=0;i--){GradientDrawable found=face(layers.getDrawable(i));if(found!=null&&found.getColor()!=null)return found;}}return null;}
 static void install(Activity activity,boolean forceDark){
  PixelAppearance.attach(activity);final boolean dark=forceDark||PixelAppearance.dark();
  final float density=activity.getResources().getDisplayMetrics().density;
  activity.getWindow().setStatusBarColor(dark?0xff141e33:0xffb9dff3);activity.getWindow().setNavigationBarColor(dark?0xff141e33:0xffe1edf4);
  PixelAppearance.bars(activity);
  Set<View> seen=Collections.newSetFromMap(new WeakHashMap<View,Boolean>());
  activity.getWindow().getDecorView().getViewTreeObserver().addOnGlobalLayoutListener(()->{
   ViewGroup content=activity.findViewById(android.R.id.content);if(content==null)return;
   if(content.getChildCount()>0){View root=content.getChildAt(0);if(root instanceof ViewGroup&&root.getBackground() instanceof ColorDrawable)root.setBackground(new Landscape(dark));}
   PixelAppearance.bars(activity);
   style(content,seen,density,dark);
  });
 }
 static void style(View v,Set<View> seen,float density,boolean dark){
  if(seen.add(v)){
   if(v instanceof TextView){TextView text=(TextView)v;Typeface current=text.getTypeface();if(current!=Typeface.MONOSPACE)text.setTypeface(Typeface.create("sans-serif-rounded",current==null?0:current.getStyle()));}
   Drawable bg=v.getBackground();
   if(v instanceof Button && !(v instanceof CompoundButton)){
    Button b=(Button)v; b.setBackgroundTintList(null);b.setStateListAnimator(null);b.setElevation(0);
    int color=dark?0xff24324b:0xfffff7e5;float radius=22;
    GradientDrawable fill=face(bg);if(fill!=null&&fill.getColor()!=null){color=fill.getColor().getDefaultColor();radius=Math.max(16,fill.getCornerRadius()/density);}
    if(bg instanceof ColorDrawable)color=((ColorDrawable)bg).getColor();
    if(Color.alpha(color)>0)b.setBackground(raised(color,density,radius,dark));
    double light=(Color.red(color)*.2126+Color.green(color)*.7152+Color.blue(color)*.0722)/255;
    b.setTextColor(Color.alpha(color)==0?(dark?0xfff9f4da:0xff29385d):light>.5?0xff29385d:0xfff9f4da);b.setAllCaps(false);
   } else if(bg instanceof GradientDrawable){GradientDrawable shape=(GradientDrawable)bg;if(shape.getShape()==GradientDrawable.RECTANGLE&&shape.getColor()!=null&&shape.getColor().getDefaultColor()!=Color.TRANSPARENT)v.setBackground(raised(shape.getColor().getDefaultColor(),density,Math.max(16,shape.getCornerRadius()/density),dark));}
  }
  if(v instanceof ViewGroup){ViewGroup group=(ViewGroup)v;for(int i=0;i<group.getChildCount();i++)style(group.getChildAt(i),seen,density,dark);}
 }
 static class Landscape extends Drawable {
  Paint p=new Paint(3);boolean dark;Landscape(boolean d){dark=d;}
  public void draw(Canvas c){float w=getBounds().width(),h=getBounds().height();p.setShader(new LinearGradient(0,0,0,h,dark?new int[]{0xff141e33,0xff1b2942}:new int[]{0xffb9dff3,0xfffff7e5,0xffe0edf5},null,Shader.TileMode.CLAMP));c.drawRect(getBounds(),p);p.setShader(null);
   p.setColor(dark?0x1580bde1:0xffdcecf5);c.drawOval(-w*.4f,h*.87f,w*.85f,h*1.2f,p);p.setColor(dark?0x18b9dff3:0xffb3d4e9);c.drawOval(w*.3f,h*.91f,w*1.5f,h*1.2f,p);
   p.setColor(dark?0x22fff7d8:0xaaffffff);c.drawRoundRect(w*.80f,h*.12f,w*1.12f,h*.17f,w*.07f,w*.07f,p);c.drawCircle(w*.94f,h*.12f,w*.08f,p);
  }
  public void setAlpha(int a){}public void setColorFilter(ColorFilter f){}public int getOpacity(){return PixelFormat.OPAQUE;}
 }
}
