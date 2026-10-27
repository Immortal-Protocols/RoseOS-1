package dev.pixel.voice;

import android.content.*;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import java.io.*;
import java.security.MessageDigest;

/** Signature-protected image files; broadcasts carry only their small URI. */
public final class BookIllustrationProvider extends ContentProvider {
 static Uri store(Context context,byte[] bytes)throws Exception{
  if(bytes.length>450000)throw new IOException("Illustration exceeds transfer limit");
  byte[] digest=MessageDigest.getInstance("SHA-256").digest(bytes);StringBuilder name=new StringBuilder();
  for(byte b:digest)name.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
  File dir=new File(context.getCacheDir(),"book-illustrations");if(!dir.isDirectory()&&!dir.mkdirs())throw new IOException("Cannot store illustration");
  File target=new File(dir,name+".jpeg");
  if(!target.exists()){File tmp=File.createTempFile("image-",".tmp",dir);try{try(OutputStream out=new FileOutputStream(tmp)){out.write(bytes);}if(!tmp.renameTo(target))throw new IOException("Cannot save illustration");}finally{tmp.delete();}}
  return Uri.parse("content://dev.pixel.voice.book-illustrations/"+name+".jpeg");
 }
 @Override public boolean onCreate(){return true;}
 @Override public String getType(Uri uri){return "image/jpeg";}
 @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException{
  if(!"r".equals(mode)||!uri.getPath().matches("/[a-f0-9]{64}\\.jpeg"))throw new FileNotFoundException("Read-only illustration");
  return ParcelFileDescriptor.open(new File(getContext().getCacheDir(),"book-illustrations"+uri.getPath()),ParcelFileDescriptor.MODE_READ_ONLY);
 }
 @Override public Cursor query(Uri u,String[] p,String s,String[] a,String o){return null;}
 @Override public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}
 @Override public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
 @Override public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}
}
