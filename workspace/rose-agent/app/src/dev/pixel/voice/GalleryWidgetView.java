package dev.pixel.voice;

import android.Manifest;
import android.content.*;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.*;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import android.view.*;
import android.widget.*;
import java.text.DateFormat;
import java.util.*;
import java.util.concurrent.*;

/** Local, read-only photo carousel. The gallery file remains the source of truth. */
final class GalleryWidgetView extends FrameLayout {
 private static final ExecutorService loader=Executors.newSingleThreadExecutor();
 private static final long INTERVAL=15_000;
 private final Handler handler=new Handler(Looper.getMainLooper());
 private final Runnable requestAccess;
 private final ImageView photo;
 private final TextView empty,caption;
 private final android.content.SharedPreferences prefs;
 private final ArrayList<Item> items=new ArrayList<>();
 private final Set<String> failed=new HashSet<>();
 private boolean requestedActive,running,loading;
 private int generation,index=-1;
 private long lastLoaded;
 private Item current;
 private Bitmap currentBitmap;
 private final Runnable advance=()->showNext(false);
 private static final class Item {
  final Uri uri;final long taken;
  Item(Uri value,long date){uri=value;taken=date;}
 }
 GalleryWidgetView(Context context,Typeface font,Runnable access){
  super(context);requestAccess=access;prefs=context.getSharedPreferences("gallery-widget",0);
  setPadding(dp(4),dp(4),dp(4),dp(7));setBackground(PlayfulTheme.surface(PixelAppearance.color(0xfffff7e5),PixelAppearance.color(0xff789ab5),getResources().getDisplayMetrics().density,24));
  FrameLayout frame=new FrameLayout(context);frame.setBackgroundColor(PixelAppearance.color(0xfffff7e5));frame.setClipToOutline(true);
  frame.setOutlineProvider(new ViewOutlineProvider(){@Override public void getOutline(View v,Outline out){out.setRoundRect(0,0,v.getWidth(),v.getHeight(),dp(20));}});
  addView(frame,new FrameLayout.LayoutParams(-1,-1));
  photo=new ImageView(context);photo.setScaleType(ImageView.ScaleType.CENTER_CROP);frame.addView(photo,new FrameLayout.LayoutParams(-1,-1));
  empty=new TextView(context);empty.setTypeface(font);empty.setTextSize(18);empty.setGravity(Gravity.CENTER);empty.setTextColor(PixelAppearance.color(0xff29385d));empty.setPadding(dp(12),0,dp(12),0);frame.addView(empty,new FrameLayout.LayoutParams(-1,-1));
  caption=new TextView(context);caption.setTypeface(font);caption.setTextSize(11);caption.setTextColor(PixelAppearance.color(0xff29385d));caption.setPadding(dp(9),dp(5),dp(9),dp(5));
  android.graphics.drawable.GradientDrawable chip=new android.graphics.drawable.GradientDrawable();chip.setColor(0xeefff7e5);chip.setCornerRadius(dp(12));caption.setBackground(chip);
  FrameLayout.LayoutParams cp=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.START);cp.setMargins(dp(10),0,dp(10),dp(10));frame.addView(caption,cp);caption.setVisibility(GONE);
  setClickable(true);setFocusable(true);setContentDescription("Gallery widget. Allow photos");setOnClickListener(v->{if(!hasAccess())requestAccess.run();else if(current!=null)openCurrent();else reload();});setOnLongClickListener(v->{options();return true;});
  showEmpty();
 }
 private int dp(float value){return Math.round(value*getResources().getDisplayMetrics().density);}
 private boolean hasAccess(){
  Context c=getContext();if(Build.VERSION.SDK_INT>=33&&c.checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES)==PackageManager.PERMISSION_GRANTED)return true;
  if(Build.VERSION.SDK_INT>=34&&c.checkSelfPermission(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)==PackageManager.PERMISSION_GRANTED)return true;
  return Build.VERSION.SDK_INT<33&&c.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)==PackageManager.PERMISSION_GRANTED;
 }
 void setActive(boolean value){requestedActive=value;updateRunning();}
 private void updateRunning(){boolean next=requestedActive&&isAttachedToWindow();if(next==running)return;running=next;handler.removeCallbacks(advance);
  if(next){if(!hasAccess()){showEmpty();return;}photo.setVisibility(INVISIBLE);reload();}
  else{generation++;loading=false;photo.animate().cancel();photo.setAlpha(1);}
 }
 @Override protected void onAttachedToWindow(){super.onAttachedToWindow();updateRunning();}
 @Override protected void onDetachedFromWindow(){requestedActive=false;updateRunning();super.onDetachedFromWindow();}
 void reload(){
  handler.removeCallbacks(advance);if(!running)return;if(!hasAccess()){clearPhoto();showEmpty();return;}
  final int request=++generation;loading=true;clearPhoto();empty.setText("Finding photos…");empty.setVisibility(VISIBLE);
  final Set<String> hidden=new HashSet<>(prefs.getStringSet("hidden",Collections.emptySet()));
  final boolean full=Build.VERSION.SDK_INT<33||getContext().checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES)==PackageManager.PERMISSION_GRANTED;
  loader.execute(()->{
   ArrayList<Item> found=new ArrayList<>();
   try{
    Uri images=MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
    String[] projection={MediaStore.Images.Media._ID,MediaStore.Images.Media.DATE_TAKEN,MediaStore.Images.Media.DATE_ADDED,MediaStore.Images.Media.RELATIVE_PATH};
    Bundle args=new Bundle();args.putStringArray(ContentResolver.QUERY_ARG_SORT_COLUMNS,new String[]{MediaStore.Images.Media.DATE_ADDED});args.putInt(ContentResolver.QUERY_ARG_SORT_DIRECTION,ContentResolver.QUERY_SORT_DIRECTION_DESCENDING);args.putInt(ContentResolver.QUERY_ARG_LIMIT,512);
    args.putString(ContentResolver.QUERY_ARG_SQL_SELECTION,MediaStore.Images.Media.SIZE+">0 AND "+MediaStore.Images.Media.IS_PENDING+"=0"+(Build.VERSION.SDK_INT>=30?" AND "+MediaStore.Images.Media.IS_TRASHED+"=0":""));
    try(Cursor rows=getContext().getContentResolver().query(images,projection,args,null)){
     if(rows!=null)while(rows.moveToNext()){
      String path=rows.getString(3);if(full&&path!=null){path=path.toLowerCase(Locale.ROOT);if(path.contains("screenshots/")||path.startsWith("download/")||path.startsWith("downloads/"))continue;}
      Uri uri=ContentUris.withAppendedId(images,rows.getLong(0));if(hidden.contains(uri.toString()))continue;
      long date=rows.getLong(1);if(date<=0)date=rows.getLong(2)*1000L;found.add(new Item(uri,date));
     }
    }
   }catch(Exception unavailable){}
   // A bounded, newer-biased local sample follows Gallery's shuffle approach.
   Random random=new Random();ArrayList<Item> chosen=new ArrayList<>();while(!found.isEmpty()&&chosen.size()<128){int at;do{at=(int)(-Math.log(Math.max(1e-9,random.nextDouble()))*found.size()/2);}while(at>=found.size());chosen.add(found.remove(at));}
   handler.post(()->{if(request!=generation||!running)return;loading=false;items.clear();items.addAll(chosen);failed.clear();index=-1;lastLoaded=SystemClock.elapsedRealtime();if(items.isEmpty()){clearPhoto();showEmpty();}else showNext(true);});
  });
 }
 private void showNext(boolean immediate){
  handler.removeCallbacks(advance);if(!running||loading||items.isEmpty()||(items.size()==1&&current!=null&&current.uri.equals(items.get(0).uri)))return;
  if(!hasAccess()){clearPhoto();showEmpty();return;}
  Item next=null;String previous=current!=null?current.uri.toString():prefs.getString("last","");
  for(int tries=0;tries<items.size();tries++){index=(index+1)%items.size();Item candidate=items.get(index);if(failed.contains(candidate.uri.toString()))continue;if(items.size()>1&&candidate.uri.toString().equals(previous))continue;next=candidate;break;}
  if(next==null){if(current==null)showEmpty();return;}
  final Item picked=next;final int request=++generation;loading=true;
  loader.execute(()->{Bitmap image=null;try{image=ImageDecoder.decodeBitmap(ImageDecoder.createSource(getContext().getContentResolver(),picked.uri),(decoder,info,source)->{int longest=Math.max(info.getSize().getWidth(),info.getSize().getHeight());decoder.setTargetSampleSize(Math.max(1,(longest+719)/720));decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE);});}catch(Exception unavailable){}
   final Bitmap decoded=image;handler.post(()->{
    if(request!=generation||!running){if(decoded!=null)decoded.recycle();return;}loading=false;
    if(decoded==null){failed.add(picked.uri.toString());handler.post(advance);return;}
    current=picked;currentBitmap=decoded;photo.animate().cancel();photo.setImageBitmap(decoded);photo.setVisibility(VISIBLE);photo.setAlpha(immediate||!android.animation.ValueAnimator.areAnimatorsEnabled()?1:.25f);
    if(photo.getAlpha()<1)photo.animate().alpha(1).setDuration(650).start();
    empty.setVisibility(GONE);caption.setText(picked.taken>0?DateFormat.getDateInstance(DateFormat.MEDIUM).format(new Date(picked.taken)):"Gallery");caption.setVisibility(VISIBLE);
    setContentDescription("Gallery widget. Photo from "+caption.getText()+". Tap to open; hold for options.");prefs.edit().putString("last",picked.uri.toString()).apply();handler.postDelayed(advance,INTERVAL);
   });
  });
 }
 private void clearPhoto(){current=null;currentBitmap=null;photo.setImageDrawable(null);caption.setVisibility(GONE);}
 private void showEmpty(){empty.setVisibility(VISIBLE);empty.setText(hasAccess()?"No photos to show":"Allow photos");setContentDescription("Gallery widget. "+empty.getText());}
 private void openCurrent(){
  try{Intent open=new Intent(Intent.ACTION_VIEW).setDataAndType(current.uri,"image/*").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
  // Prefer the system Gallery fork; stock Android (e.g. Samsung) uses its own default viewer.
  if(new Intent(open).setPackage("com.android.gallery3d").resolveActivity(getContext().getPackageManager())!=null)open.setPackage("com.android.gallery3d");getContext().startActivity(open);}
  catch(RuntimeException missing){Toast.makeText(getContext(),"This photo is unavailable",Toast.LENGTH_SHORT).show();reload();}
 }
 private void options(){
  PopupMenu menu=new PopupMenu(getContext(),this);
  if(current!=null){menu.getMenu().add("Next photo").setOnMenuItemClickListener(i->{showNext(false);return true;});menu.getMenu().add("Hide from widget").setOnMenuItemClickListener(i->{Set<String> hidden=new HashSet<>(prefs.getStringSet("hidden",Collections.emptySet()));hidden.add(current.uri.toString());prefs.edit().putStringSet("hidden",hidden).apply();reload();return true;});}
  menu.getMenu().add("Photo access").setOnMenuItemClickListener(i->{requestAccess.run();return true;});
  if(!prefs.getStringSet("hidden",Collections.emptySet()).isEmpty())menu.getMenu().add("Restore hidden photos").setOnMenuItemClickListener(i->{prefs.edit().remove("hidden").apply();reload();return true;});
  menu.show();
 }
}
