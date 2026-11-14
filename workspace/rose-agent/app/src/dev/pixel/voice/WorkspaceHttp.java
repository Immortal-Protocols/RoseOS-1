package dev.pixel.voice;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Map;

/**
 * Project 2: the one HTTP/1.1 exchange the workspace task client makes over the paired, pinned TLS link
 * (WorkspaceTaskTransport): a POST with a small JSON body, and a bounded reply. Pure (streams only), so the
 * framing is tested on the JVM.
 *
 * <ul>
 *   <li>Header names and values must not contain CR or LF, so nothing can inject a header or split the request.</li>
 *   <li>The reply must have a status line and either a Content-Length or close after its body. A body over
 *   MAX_REPLY bytes, a truncated body or a malformed status line is an IOException: no definite answer, which
 *   the client reports as uncertain.</li>
 * </ul>
 */
final class WorkspaceHttp {
 private WorkspaceHttp() {}
 static final int MAX_REPLY=16*1024,MAX_HEAD=8*1024;

 /** The request bytes: POST path HTTP/1.1, Host, the given headers in order, Content-Length, Connection: close, the body. */
 static byte[] post(String host,int port,String path,Map<String,String> headers,byte[] body){return request("POST",host,port,path,headers,body);}
 /** A GET: the same framing with no body (Content-Length: 0). */
 static byte[] get(String host,int port,String path,Map<String,String> headers){return request("GET",host,port,path,headers,new byte[0]);}

 private static byte[] request(String method,String host,int port,String path,Map<String,String> headers,byte[] body){
  if(!safe(host)||!safe(path)||!path.startsWith("/")||path.indexOf(' ')>=0||port<1||port>65535)throw new IllegalArgumentException("bad target");
  StringBuilder b=new StringBuilder();
  b.append(method).append(' ').append(path).append(" HTTP/1.1\r\n");
  b.append("Host: ").append(host.indexOf(':')>=0?"["+host+"]":host).append(':').append(port).append("\r\n");
  for(Map.Entry<String,String> h:headers.entrySet()){
   String name=h.getKey(),value=h.getValue();
   if(!token(name)||value==null||!safe(value))throw new IllegalArgumentException("bad header");
   String lower=name.toLowerCase(Locale.ROOT);
   if(lower.equals("host")||lower.equals("content-length")||lower.equals("connection")||lower.equals("transfer-encoding"))throw new IllegalArgumentException("reserved header");
   b.append(name).append(": ").append(value).append("\r\n");
  }
  b.append("Content-Length: ").append(body.length).append("\r\n");
  b.append("Connection: close\r\n\r\n");
  byte[] head=b.toString().getBytes(StandardCharsets.UTF_8);
  byte[] out=new byte[head.length+body.length];
  System.arraycopy(head,0,out,0,head.length);System.arraycopy(body,0,out,head.length,body.length);
  return out;
 }

 /** The reply: its status and body (UTF-8). Throws IOException when there is no definite, complete reply. */
 static WorkspaceTaskClient.Response read(InputStream in)throws IOException{
  String head=readHead(in);
  String[] lines=head.split("\r\n",-1);
  String[] status=lines[0].split(" ",3);
  if(status.length<2||!status[0].startsWith("HTTP/1."))throw new IOException("bad status line");
  int code;try{code=Integer.parseInt(status[1]);}catch(NumberFormatException e){throw new IOException("bad status");}
  if(code<100||code>599)throw new IOException("bad status");
  long length=-1;boolean chunked=false;
  for(int i=1;i<lines.length;i++){
   String l=lines[i];if(l.isEmpty())continue;
   int colon=l.indexOf(':');if(colon<=0)throw new IOException("bad header");
   String name=l.substring(0,colon).trim().toLowerCase(Locale.ROOT),value=l.substring(colon+1).trim();
   if(name.equals("content-length")){
    long v;try{v=Long.parseLong(value);}catch(NumberFormatException e){throw new IOException("bad length");}
    if(v<0||(length>=0&&v!=length))throw new IOException("bad length");length=v;
   }
   if(name.equals("transfer-encoding")&&!value.equalsIgnoreCase("identity"))chunked=true;
  }
  if(chunked)throw new IOException("chunked replies are not expected");
  if(length>MAX_REPLY)throw new IOException("reply too large");
  ByteArrayOutputStream body=new ByteArrayOutputStream();
  byte[] buf=new byte[2048];
  if(length>=0){
   long left=length;
   while(left>0){int n=in.read(buf,0,(int)Math.min(buf.length,left));if(n<0)throw new IOException("truncated reply");body.write(buf,0,n);left-=n;}
  }else{
   int n;while((n=in.read(buf))>=0){body.write(buf,0,n);if(body.size()>MAX_REPLY)throw new IOException("reply too large");}
  }
  return new WorkspaceTaskClient.Response(code,new String(body.toByteArray(),StandardCharsets.UTF_8));
 }

 /** The status line and headers, up to the blank line (CRLF CRLF), at most MAX_HEAD bytes. */
 private static String readHead(InputStream in)throws IOException{
  byte[] b=new byte[MAX_HEAD];int n=0;
  while(true){
   int c=in.read();if(c<0)throw new IOException("no reply");
   if(n==MAX_HEAD)throw new IOException("reply head too large");
   b[n++]=(byte)c;
   if(n>=4&&b[n-4]=='\r'&&b[n-3]=='\n'&&b[n-2]=='\r'&&b[n-1]=='\n')return new String(b,0,n-4,StandardCharsets.ISO_8859_1);
  }
 }

 private static boolean safe(String s){if(s==null)return false;for(int i=0;i<s.length();i++){char c=s.charAt(i);if(c=='\r'||c=='\n'||c==0)return false;}return true;}
 private static boolean token(String s){if(s==null||s.isEmpty())return false;for(int i=0;i<s.length();i++){char c=s.charAt(i);if(c<=' '||c>'~'||c==':')return false;}return true;}
}
