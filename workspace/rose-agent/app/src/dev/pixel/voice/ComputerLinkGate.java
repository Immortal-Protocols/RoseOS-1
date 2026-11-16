package dev.pixel.voice;

/** One Allow interval. Stop invalidates in-flight work even if Allow is tapped again. */
final class ComputerLinkGate {
 private long generation;
 private boolean allowed;
 synchronized void allow(){generation++;allowed=true;}
 synchronized void stop(){generation++;allowed=false;}
 synchronized long token(){return generation;}
 synchronized boolean allowed(){return allowed;}
 synchronized boolean valid(long token,boolean foregroundUnlocked){
  if(!foregroundUnlocked)stop();
  return allowed&&token==generation;
 }
}
