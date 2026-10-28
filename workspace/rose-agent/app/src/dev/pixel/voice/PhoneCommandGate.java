package dev.pixel.voice;

import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** One execution per phone command id on the bridge's generic main-thread path. The caller claims the id
 * (a durable "running" receipt is saved before anything is scheduled), so a re-query of the same id finds
 * that receipt instead of scheduling a second action. Waiting for the posted action is bounded, and a wait
 * that runs out is not a failure: it answers with the id's current receipt, which stays "running" until
 * the action saves its real result. Pure logic (no Android types) so it is testable. */
final class PhoneCommandGate {
 interface Receipts { String get(String id); void put(String id,String json); }
 static final String RUNNING="{\"status\":\"running\"}";
 private final Receipts receipts;private final Object lock;
 PhoneCommandGate(Receipts receipts,Object lock){this.receipts=receipts;this.lock=lock;}
 /** Returns the id's existing receipt (it already has an owner), or null after saving RUNNING: the caller
  * now owns the id and schedules exactly one action. */
 String claim(String id){synchronized(lock){String existing=receipts.get(id);if(existing!=null)return existing;receipts.put(id,RUNNING);return null;}}
 /** The action's answer if it arrives in time; otherwise the id's current receipt, never an invented error. */
 String await(Future<String> action,String id,long timeoutMs)throws InterruptedException,ExecutionException{
  try{return action.get(timeoutMs,TimeUnit.MILLISECONDS);}
  catch(TimeoutException stillRunning){String now=receipts.get(id);return now!=null?now:RUNNING;}
 }
}
