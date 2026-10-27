package dev.pixel.voice;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import javax.net.ssl.SSLSocket;
import org.json.JSONObject;

/** Outbound, pinned TLS tunnel to this device's own explicitly selected ADB port. */
final class DebugBridge {
 private final VoiceService service;
 private final Map<String,Connection> active=new ConcurrentHashMap<>();
 DebugBridge(VoiceService service){this.service=service;}
 JSONObject connect(JSONObject command)throws Exception{
  String id=command.getString("channelId"),host=command.getString("targetHost");
  int port=command.getInt("targetPort");long expires=command.getLong("expiresAt");
  if(!id.matches("[a-f0-9-]{36}")||port<1024||port>65535||expires<System.currentTimeMillis()||expires>System.currentTimeMillis()+20000)throw new IOException("Invalid debugging channel");
  InetAddress address=InetAddress.getByName(host);
  if(!address.isLoopbackAddress()&&NetworkInterface.getByInetAddress(address)==null)throw new IOException("Debugging target is not this phone");
  if(active.size()>=8)throw new IOException("Too many debugging connections");
  Connection connection=new Connection(id,address,port);
  if(active.putIfAbsent(id,connection)==null){Thread worker=new Thread(connection,"PixelDebugBridge");worker.setDaemon(true);worker.start();}
  return new JSONObject().put("status","accepted").put("channelId",id);
 }
 void close(){for(Connection c:active.values())c.close();}
 private final class Connection implements Runnable {
  final String id;final InetAddress address;final int port;volatile Socket local,remote;
  Connection(String id,InetAddress address,int port){this.id=id;this.address=address;this.port=port;}
  public void run(){
   try{
    local=new Socket();local.connect(new InetSocketAddress(address,port),5000);local.setTcpNoDelay(true);
    URL endpoint=new URL(service.endpoint);
    if(!"https".equals(endpoint.getProtocol()))throw new IOException("TLS is required");
    int remotePort=endpoint.getPort()<0?443:endpoint.getPort();
    Socket tcp=new Socket();remote=tcp;tcp.connect(new InetSocketAddress(endpoint.getHost(),remotePort),6000);
    SSLSocket tls=(SSLSocket)service.ssl.createSocket(tcp,endpoint.getHost(),remotePort,true);remote=tls;
    tls.setSoTimeout(12000);tls.startHandshake();
    String request="GET /debug-bridge/connect?channel="+id+" HTTP/1.1\r\nHost: "+endpoint.getHost()+":"+remotePort+"\r\nConnection: Upgrade\r\nUpgrade: pixel-debug-v1\r\n\r\n";
    tls.getOutputStream().write(request.getBytes(StandardCharsets.US_ASCII));tls.getOutputStream().flush();
    InputStream in=tls.getInputStream();ByteArrayOutputStream header=new ByteArrayOutputStream();int tail=0;
    while(tail!=0x0d0a0d0a){int next=in.read();if(next<0||header.size()>=4096)throw new IOException("Invalid bridge response");header.write(next);tail=(tail<<8)|next;}
    String response=new String(header.toByteArray(),StandardCharsets.US_ASCII);
    if(!response.startsWith("HTTP/1.1 101 "))throw new IOException("Bridge rejected");
    tls.setSoTimeout(120000);local.setSoTimeout(120000);
    Thread upload=new Thread(()->{try{copy(local,remote);}catch(Exception ignored){}finally{close();}},"PixelDebugUpload");upload.setDaemon(true);upload.start();
    copy(remote,local);
   }catch(Exception ignored){}finally{close();}
  }
  void copy(Socket from,Socket to)throws IOException{byte[] buffer=new byte[32768];int n;InputStream input=from.getInputStream();OutputStream output=to.getOutputStream();while((n=input.read(buffer))!=-1){output.write(buffer,0,n);output.flush();}}
  void close(){active.remove(id,this);try{if(local!=null)local.close();}catch(Exception ignored){}try{if(remote!=null)remote.close();}catch(Exception ignored){}}
 }
}
