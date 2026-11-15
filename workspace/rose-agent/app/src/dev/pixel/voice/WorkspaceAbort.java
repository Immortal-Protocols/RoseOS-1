package dev.pixel.voice;

import java.io.Closeable;
import java.io.IOException;

/**
 * Project 2: stops one in-flight workspace request from another thread. The transport attaches its socket while
 * the request runs. fire() closes that socket, which ends a blocked connect or read at once. The Rose server
 * sees the disconnect and cancels its composition (workspace_model_routes.py), so a superseded request neither
 * holds up the phone's worker nor keeps the server's model busy.
 *
 * Firing is one-way: a socket attached after fire() is closed immediately and the attach reports false. Pure
 * (java.io only).
 */
final class WorkspaceAbort {
 private Closeable open;
 private boolean fired;

 /** Registers the request's socket. False, with the socket closed, if this request was already stopped. */
 boolean attach(Closeable c){
  synchronized(this){if(!fired){open=c;return true;}}
  close(c);return false;
 }

 /** The request finished: its socket is no longer this abort's to close. */
 synchronized void detach(Closeable c){if(open==c)open=null;}

 /** Stops the request: closes its socket if one is attached, and any attached later. */
 void fire(){
  Closeable c;
  synchronized(this){fired=true;c=open;open=null;}
  close(c);
 }

 synchronized boolean fired(){return fired;}

 private static void close(Closeable c){if(c==null)return;try{c.close();}catch(IOException|RuntimeException ignored){}}
}
