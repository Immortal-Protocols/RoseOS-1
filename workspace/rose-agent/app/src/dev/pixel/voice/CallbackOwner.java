package dev.pixel.voice;

/** Ownership of asynchronous bridge callbacks by voice page generation. Each voice WebView is one
 * generation, and its page numbers its requests from zero, so a late completion from a replaced (or
 * destroyed) view would otherwise reach the replacement page and settle its request with the same ID.
 * Work runs only while its generation is still the current, open one; checked at delivery time, on the
 * main thread. Pure logic (no Android types) so it is testable. */
final class CallbackOwner {
 private int current;private boolean closed;
 /** A new voice view replaces the previous one; earlier generations stop receiving callbacks. */
 synchronized int open(){return ++current;}
 synchronized int current(){return current;}
 /** The service is being destroyed: no generation receives callbacks any more. */
 synchronized void close(){closed=true;}
 synchronized boolean owns(int generation){return !closed&&generation==current;}
 /** Runs `work` only for the current generation; returns whether it ran. */
 boolean run(int generation,Runnable work){if(!owns(generation))return false;work.run();return true;}
}
