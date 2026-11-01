package dev.pixel.voice;

import java.util.function.IntPredicate;

/** The page-to-native handoff of local Rose's task list (LOCAL-TASK-FEED-002): VoiceProviders local() onLocalTasks,
 * JSON.stringify'd by the voice page, reaches WorkspaceOverviewFeed.accept unmodified. A delivery is taken only from
 * the current voice page generation (CallbackOwner), so a replaced or destroyed page's late call never replaces the
 * list. When the page that delivered is torn down without delivering [] (renderer gone, service destroyed), the feed
 * is cleared with "[]", so no list outlives its page or connection. Read-only: nothing is sent anywhere. Pure logic. */
final class LocalTaskFeedGate {
 interface Sink { void accept(String raw); }
 static final String EMPTY="[]";
 private final IntPredicate current;private final Sink sink;
 private int holder; // the page generation whose delivery the feed holds; 0 when it holds none
 LocalTaskFeedGate(IntPredicate current,Sink sink){this.current=current;this.sink=sink;}

 /** One onLocalTasks delivery from page `generation`; returns whether it was handed to the feed. */
 synchronized boolean deliver(int generation,String raw){
  if(generation<=0||raw==null||!current.test(generation))return false;
  holder=EMPTY.equals(raw)?0:generation;
  sink.accept(raw);
  return true;
 }
 /** Page `generation` is gone (its renderer died or it was replaced): clear the feed if it holds that page's list. */
 synchronized void pageGone(int generation){if(holder!=0&&holder==generation){holder=0;sink.accept(EMPTY);}}
 /** The service is being destroyed: no page's list survives it. */
 synchronized void closeAll(){if(holder!=0){holder=0;sink.accept(EMPTY);}}
}
