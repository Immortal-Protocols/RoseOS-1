package dev.pixel.voice;

import android.graphics.*;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;

/** Original, quiet console-world artwork shared by the launcher and its drawer. */
final class PlayfulTheme {
 static Drawable surface(int face,int edge,float density,int radius) {
  GradientDrawable shadow=new GradientDrawable();shadow.setColor(edge);shadow.setCornerRadius(radius*density);
  GradientDrawable top=new GradientDrawable();top.setColor(face);top.setCornerRadius(radius*density);top.setStroke(Math.max(1,(int)density),PixelAppearance.color(0x2629385d));
  LayerDrawable layers=new LayerDrawable(new Drawable[]{shadow,top});
  layers.setLayerInset(0,0,(int)(3*density),0,0);layers.setLayerInset(1,0,0,0,(int)(3*density));
  return layers;
 }
 static final class World extends Drawable {
  private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
  private void oval(Canvas c,float l,float t,float r,float b,int color){p.setColor(color);c.drawOval(l,t,r,b,p);}
  private void cloud(Canvas c,float x,float y,float size){
   p.setColor(PixelAppearance.dark()?0x183f6896:0xccffffff);c.drawRoundRect(x-size,y,x+size,y+size*.38f,size*.2f,size*.2f,p);
   c.drawCircle(x-size*.42f,y,size*.35f,p);c.drawCircle(x+size*.1f,y-size*.1f,size*.47f,p);c.drawCircle(x+size*.58f,y+size*.05f,size*.28f,p);
  }
  private void star(Canvas c,float x,float y,float r){p.setColor(0xffffd66b);Path path=new Path();path.moveTo(x,y-r);path.quadTo(x+r*.2f,y-r*.15f,x+r,y);path.quadTo(x+r*.15f,y+r*.2f,x,y+r);path.quadTo(x-r*.2f,y+r*.15f,x-r,y);path.quadTo(x-r*.15f,y-r*.2f,x,y-r);c.drawPath(path,p);}
  @Override public void draw(Canvas c){
   float w=getBounds().width(),h=getBounds().height();
   p.setShader(new LinearGradient(0,0,0,h,new int[]{PixelAppearance.color(0xffb9dff3),PixelAppearance.color(0xfffff7e5),PixelAppearance.color(0xffe1edf4)},new float[]{0,.57f,1},Shader.TileMode.CLAMP));c.drawRect(getBounds(),p);p.setShader(null);
   cloud(c,w*.03f,h*.23f,w*.15f);cloud(c,w*.96f,h*.16f,w*.20f);
   star(c,w*.83f,h*.31f,w*.021f);star(c,w*.15f,h*.40f,w*.013f);
   oval(c,-w*.5f,h*.79f,w*.68f,h*1.22f,PixelAppearance.color(0xffdcecf5));
   oval(c,w*.3f,h*.82f,w*1.5f,h*1.21f,PixelAppearance.color(0xffc7e0ef));
   oval(c,-w*.45f,h*.92f,w*.62f,h*1.18f,PixelAppearance.color(0xffb3d4e9));
  }
  @Override public void setAlpha(int a){} @Override public void setColorFilter(ColorFilter f){} @Override public int getOpacity(){return PixelFormat.OPAQUE;}
 }
}
