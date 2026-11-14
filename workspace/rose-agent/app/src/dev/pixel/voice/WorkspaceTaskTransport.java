package dev.pixel.voice;

import android.content.Context;
import java.io.IOException;
import java.io.OutputStream;
import java.net.Socket;
import java.security.GeneralSecurityException;
import java.util.Map;
import javax.net.ssl.SSLSocket;

/**
 * Project 2: WorkspaceTaskClient's transport and signer, over the trust the phone already has.
 * <ul>
 *   <li>The paired backend's addresses, port and certificate pin (LocalRoseConnection.paired).</li>
 *   <li>TLS that accepts only the pinned certificate (LocalRoseConnection.tls).</li>
 *   <li>The phone's own pairing key, signing rose-auth-v1 (LocalRoseKey, LocalRoseAuth.headers), as /hello is signed.</li>
 * </ul>
 * Nothing new is trusted, stored or requested.
 *
 * Only WorkspaceHub calls this, and only for a task the person confirmed while WorkspaceTaskGate is on, which
 * it is not by default. It moves to the next paired address only when the TLS connection itself fails. Once the
 * request has been written, the answer, or its absence, is final for this attempt, so one signed nonce is never
 * sent twice.
 */
final class WorkspaceTaskTransport implements WorkspaceTaskClient.Transport {
 static final int READ_TIMEOUT_MS=10000;
 private final Context context;
 WorkspaceTaskTransport(Context context){this.context=context.getApplicationContext();}

 @Override public WorkspaceTaskClient.Response post(String path,Map<String,String> headers,byte[] body)throws Exception{
  LocalRoseConnection.Paired p=LocalRoseConnection.paired(context);
  if(p==null)throw new IOException("not paired");
  IOException last=null;
  for(String host:p.order()){
   Socket raw=new Socket();SSLSocket s;
   try{s=LocalRoseConnection.tls(raw,host,p.port,p.pin);}
   catch(IOException e){last=e;try{raw.close();}catch(IOException ignored){}continue;}
   try{
    s.setSoTimeout(READ_TIMEOUT_MS);
    OutputStream out=s.getOutputStream();
    out.write(WorkspaceHttp.post(host,p.port,path,headers,body));out.flush();
    return WorkspaceHttp.read(s.getInputStream());
   }finally{try{s.close();}catch(IOException ignored){}try{raw.close();}catch(IOException ignored){}}
  }
  throw last!=null?last:new IOException("no address for the backend");
 }

 /** The rose-auth-v1 headers for one request, signed with this phone's pairing key. */
 static WorkspaceTaskClient.Signer signer(Context context){
  final Context app=context.getApplicationContext();
  return (method,path)->{
   LocalRoseConnection.Paired p=LocalRoseConnection.paired(app);
   LocalRoseKey key=LocalRoseKey.load();
   if(p==null||key==null||!key.deviceId().equals(p.deviceId))throw new GeneralSecurityException("pairing key unavailable");
   return LocalRoseAuth.headers(p.deviceId,method,path,System.currentTimeMillis(),LocalRoseAuth.newNonce(),p.pin,key);
  };
 }
}
