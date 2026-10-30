package dev.pixel.voice;

import android.content.Context;
import android.graphics.*;
import android.graphics.drawable.Drawable;
import android.view.View;

/** Conversation-local drawing for the HiFi reading surface and tray icons. */
final class ConversationDrawing {
 private ConversationDrawing(){}

 /** HiFi .ivory-bg: a vertical ivory gradient (#f7f9fc → #eef3f9), a soft blue radial wash at the lower right, and
  * the Home wallpaper (rose_home_wallpaper) at 16% fading in from that corner, covering the -10% inset at
  * background-position 80% 20% rather than stretched. */
 static final class IvoryBackground extends Drawable {
  private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG|Paint.FILTER_BITMAP_FLAG);private final Bitmap wallpaper;
  IvoryBackground(Context c){
   Bitmap w=null;
   try{BitmapFactory.Options o=new BitmapFactory.Options();o.inSampleSize=8;w=BitmapFactory.decodeResource(c.getResources(),R.drawable.rose_home_wallpaper,o);}catch(RuntimeException e){w=null;}
   wallpaper=w;
  }
  @Override public void draw(Canvas c){
   Rect b=getBounds();float w=b.width(),h=b.height();
   paint.setShader(new LinearGradient(0,0,0,h,0xfff7f9fc,0xffeef3f9,Shader.TileMode.CLAMP));c.drawRect(b,paint);
   paint.setShader(new RadialGradient(w*1.1f,h*1.05f,Math.max(w,h)*.7f,0x59a0bee6,0x00a0bee6,Shader.TileMode.CLAMP));c.drawRect(b,paint);
   paint.setShader(null);
   if(wallpaper!=null){
    int layer=c.saveLayer(0,0,w,h,null);
    paint.setAlpha(41);int[] crop=HomeAppLayout.cover(wallpaper.getWidth(),wallpaper.getHeight(),w*1.2f,h*1.2f,HomeAppLayout.IVORY_X,HomeAppLayout.IVORY_Y);c.drawBitmap(wallpaper,new Rect(crop[0],crop[1],crop[2],crop[3]),new RectF(-w*.1f,-h*.1f,w*1.1f,h*1.1f),paint);paint.setAlpha(255);
    paint.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.DST_IN));
    paint.setShader(new RadialGradient(w,h,Math.max(w*.9f,h*.7f),new int[]{0xff000000,0xff000000,0x00000000},new float[]{0f,.1f,1f},Shader.TileMode.CLAMP));
    c.drawRect(0,0,w,h,paint);paint.setShader(null);paint.setXfermode(null);c.restoreToCount(layer);
   }
  }
  @Override public void setAlpha(int alpha){}
  @Override public void setColorFilter(ColorFilter cf){}
  @Override public int getOpacity(){return PixelFormat.OPAQUE;}
 }

 /** One tray icon (HiFi icons.js: 24-unit line glyphs, 1.9/2.2 stroke): microphone, crossed microphone, end ✕ in
  * the stop color, a cancel ellipsis, and the 2×2 Rose's apps grid. Decorative: the well carries the spoken name. */
 static final class TrayIcon extends View {
  private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);private final RectF r=new RectF();private final Path path=new Path();
  private ConversationPresentation.Glyph glyph=ConversationPresentation.Glyph.MIC;private int color=0xff26303f;
  TrayIcon(Context c){super(c);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);}
  void set(ConversationPresentation.Glyph g,int color){if(g!=glyph||color!=this.color){glyph=g;this.color=color;invalidate();}}
  @Override protected void onDraw(Canvas c){
   float s=Math.min(getWidth(),getHeight())*.45f/24f,ox=(getWidth()-24*s)/2f,oy=(getHeight()-24*s)/2f; // 28px icon in a 66px well
   c.save();c.translate(ox,oy);c.scale(s,s);p.setColor(color);
   p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(glyph==ConversationPresentation.Glyph.END?2.2f:1.9f);
   switch(glyph){
    case MIC:case MIC_OFF:
     r.set(9,3,15,15);c.drawRoundRect(r,3,3,p);
     path.reset();path.moveTo(5.5f,11);path.arcTo(new RectF(5.5f,4.5f,18.5f,17.5f),180,-180,false);c.drawPath(path,p);
     c.drawLine(12,17.5f,12,21,p);
     if(glyph==ConversationPresentation.Glyph.MIC_OFF)c.drawLine(4,4,20,20,p);
     break;
    case END:c.drawLine(6,6,18,18,p);c.drawLine(18,6,6,18,p);break;
    case CANCEL:p.setStyle(Paint.Style.FILL);for(int i=0;i<3;i++)c.drawCircle(6+6*i,12,1.6f,p);break;
    case APPS:p.setStyle(Paint.Style.FILL);
     for(float[] q:new float[][]{{3.5f,3.5f},{13,3.5f},{3.5f,13},{13,13}}){r.set(q[0],q[1],q[0]+7.5f,q[1]+7.5f);c.drawRoundRect(r,2.2f,2.2f,p);}
     break;
   }
   c.restore();
  }
 }
}
