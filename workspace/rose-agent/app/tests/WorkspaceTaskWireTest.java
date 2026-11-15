package dev.pixel.voice;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Project 2: the wire under the workspace task client, with fakes only. It covers:
 * <ul>
 *   <li>the HTTP/1.1 framing WorkspaceTaskTransport writes and reads (WorkspaceHttp);</li>
 *   <li>the client end to end through those exact bytes against in-memory replies shaped like App Core's
 *   route;</li>
 *   <li>the GET framing for App Core's page-task read.</li>
 * </ul>
 * Nothing opens a socket, and no task runs.
 */
public final class WorkspaceTaskWireTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}
 static String text(byte[] b){return new String(b,StandardCharsets.UTF_8);}
 static WorkspaceTaskClient.Response reply(String raw)throws IOException{return WorkspaceHttp.read(new ByteArrayInputStream(raw.getBytes(StandardCharsets.ISO_8859_1)));}
 static String http(int status,String json){byte[] b=json.getBytes(StandardCharsets.UTF_8);return "HTTP/1.1 "+status+" X\r\ncontent-type: application/json\r\ncache-control: no-store\r\ncontent-length: "+b.length+"\r\n\r\n"+json;}

 public static void main(String[] args)throws Exception{
  framing();replies();endToEnd();card();
  System.out.println("WorkspaceTaskWireTest: "+checks+" checks passed (in memory; no socket, no task)");
 }

 /** First test: the exact POST bytes, and no way to inject a header. */
 static void framing(){
  Map<String,String> h=new LinkedHashMap<>();
  h.put("X-Rose-Device","abc");h.put("X-Rose-Time","1");h.put("X-Rose-Nonce","n");h.put("X-Rose-Signature","sig");h.put("Content-Type","application/json");
  byte[] body="{\"a\":1}".getBytes(StandardCharsets.UTF_8);
  String req=text(WorkspaceHttp.post("rose.tailnet.ts.net",9443,"/workspace/v1/tasks",h,body));
  expect(req.equals("POST /workspace/v1/tasks HTTP/1.1\r\nHost: rose.tailnet.ts.net:9443\r\nX-Rose-Device: abc\r\nX-Rose-Time: 1\r\nX-Rose-Nonce: n\r\nX-Rose-Signature: sig\r\nContent-Type: application/json\r\nContent-Length: 7\r\nConnection: close\r\n\r\n{\"a\":1}"),"exact request:\n"+req);
  expect(text(WorkspaceHttp.post("fd7a::1",9443,"/x",new LinkedHashMap<String,String>(),new byte[0])).contains("Host: [fd7a::1]:9443\r\n"),"IPv6 host in brackets");
  String[][] bad={{"X-Evil","a\r\nX-Injected: 1"},{"X-Evil","a\nb"},{"Bad Name","v"},{"Host","h"},{"Content-Length","9"},{"Transfer-Encoding","chunked"},{"X-Null","a\0b"}};
  for(String[] b:bad){
   Map<String,String> m=new LinkedHashMap<>();m.put(b[0],b[1]);
   try{WorkspaceHttp.post("h",1,"/p",m,new byte[0]);expect(false,"accepted "+b[0]);}catch(IllegalArgumentException e){checks++;}
  }
  try{WorkspaceHttp.post("h\r\nX: y",1,"/p",new LinkedHashMap<String,String>(),new byte[0]);expect(false,"host injection");}catch(IllegalArgumentException e){checks++;}
  try{WorkspaceHttp.post("h",1,"/p HTTP/1.1\r\nX: y",new LinkedHashMap<String,String>(),new byte[0]);expect(false,"path injection");}catch(IllegalArgumentException e){checks++;}
  try{WorkspaceHttp.post("h",0,"/p",new LinkedHashMap<String,String>(),new byte[0]);expect(false,"port 0");}catch(IllegalArgumentException e){checks++;}
 }

 /** The reply: a status and a bounded body, or no definite answer at all. */
 static void replies()throws Exception{
  WorkspaceTaskClient.Response r=reply(http(200,"{\"schemaVersion\":1,\"status\":\"accepted\",\"taskId\":null}"));
  expect(r.status==200&&r.body.startsWith("{\"schemaVersion\":1"),"status and body");
  expect(reply("HTTP/1.1 503 Service Unavailable\r\n\r\n{\"schemaVersion\":1,\"status\":\"unavailable\"}").status==503,"no length: body to end of stream");
  String[] broken={"","HTTP/1.1 200 OK\r\ncontent-length: 50\r\n\r\n{\"short\":1}","SMTP 220 hi\r\n\r\n","HTTP/1.1 abc OK\r\n\r\n","HTTP/1.1 200 OK\r\ncontent-length: 5\r\ncontent-length: 6\r\n\r\nhello","HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n","HTTP/1.1 200 OK\r\ncontent-length: 99999\r\n\r\n","HTTP/1.1 200 OK\r\n"};
  for(String b:broken){try{reply(b);expect(false,"accepted a broken reply: "+b);}catch(IOException e){checks++;}}
  StringBuilder huge=new StringBuilder("HTTP/1.1 200 OK\r\n\r\n");for(int i=0;i<WorkspaceHttp.MAX_REPLY+10;i++)huge.append('x');
  try{reply(huge.toString());expect(false,"oversized unbounded body");}catch(IOException e){checks++;}
  StringBuilder head=new StringBuilder("HTTP/1.1 200 OK\r\n");for(int i=0;i<WorkspaceHttp.MAX_HEAD;i++)head.append('h');
  try{reply(head.toString());expect(false,"oversized head");}catch(IOException e){checks++;}
 }

 /** The client through the exact bytes: what App Core's route would answer, read back as the page shows it. */
 static void endToEnd()throws Exception{
  final String[] wire={null};
  WorkspaceTaskClient.Transport overBytes=(path,headers,body)->{
   wire[0]=text(WorkspaceHttp.post("rose.local",9443,path,headers,body));
   return reply(http(200,"{\"schemaVersion\":1,\"status\":\"accepted\",\"taskId\":\"t-1\"}"));
  };
  WorkspaceTaskClient.Signer signer=(m,p)->{Map<String,String> h=new LinkedHashMap<>();h.put(LocalRoseAuth.H_DEVICE,"dev");h.put(LocalRoseAuth.H_TIME,"2");h.put(LocalRoseAuth.H_NONCE,"nn");h.put(LocalRoseAuth.H_SIGNATURE,"s");return h;};
  WorkspaceSnapshot.App rose=new WorkspaceSnapshot.App("dev.pixel.voice","dev.pixel.voice.MainActivity","Rose Agent",null,0);
  WorkspaceSnapshot.Task t=new WorkspaceSnapshot.Task("grocery","Add missing items to grocery list","Add them?","Compare the recipe with the list",rose);
  WorkspaceTaskClient.Outcome o=new WorkspaceTaskClient(new WorkspaceTaskClient.Gate(true),overBytes,signer).start("tap-1",t,true);
  expect(o.kind==WorkspaceTaskClient.Kind.STARTED&&"t-1".equals(o.taskId),"started through real bytes");
  expect(wire[0].startsWith("POST /workspace/v1/tasks HTTP/1.1\r\nHost: rose.local:9443\r\nX-Rose-Device: dev\r\n"),"signed headers on the wire");
  expect(wire[0].contains("\r\n\r\n{\"schemaVersion\":1,\"requestId\":\"tap-1\",\"actionId\":\"task:grocery\""),"the body after the blank line");
  expect(!wire[0].contains("Add them?"),"the ask never leaves the phone");
  // The default gate: no bytes at all.
  wire[0]=null;
  WorkspaceTaskClient.Outcome off=new WorkspaceTaskClient(WorkspaceTaskClient.Gate.disabled(),overBytes,signer).start("tap-2",t,true);
  expect(off.kind==WorkspaceTaskClient.Kind.NOT_SENT&&wire[0]==null,"gate off: nothing written");
 }

 /** The GET framing for App Core's page-task read. */
 static void card(){
  Map<String,String> h=new LinkedHashMap<>();h.put("X-Rose-Device","abc");
  String req=text(WorkspaceHttp.get("rose.local",9443,"/workspace/v1/tasks/wt-0123456789abcdef",h));
  expect(req.equals("GET /workspace/v1/tasks/wt-0123456789abcdef HTTP/1.1\r\nHost: rose.local:9443\r\nX-Rose-Device: abc\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),"exact GET:\n"+req);
  try{WorkspaceHttp.get("h",1,"/a b",new LinkedHashMap<String,String>());expect(false,"space in path");}catch(IllegalArgumentException e){checks++;}
 }
}
