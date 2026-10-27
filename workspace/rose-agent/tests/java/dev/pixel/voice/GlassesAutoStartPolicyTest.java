package dev.pixel.voice;
public final class GlassesAutoStartPolicyTest {
 public static void main(String[] args){
  GlassesAutoStartPolicy p=new GlassesAutoStartPolicy(false);
  check(p.connected(0),"normal auto-start stays enabled");
  p.userEnded();p.disconnected(1000);p.disconnected(1200);
  check(!p.connected(2400),"reported 1.4-second reconnect must respect End");
  p.disconnected(3000);check(!p.connected(5000),"repeated brief flaps must not clear End");
  p.userStarted();check(p.connected(5100),"explicit tap re-enables voice");
  p.userEnded();p.disconnected(10000);check(p.connected(20000),"ten-second separation retains wearing-cycle behavior");
  p.userEnded();p.hinge(true);check(p.suppressed(),"open advertisement alone is not a new wearing cycle");
  p.hinge(false);p.hinge(true);check(!p.suppressed(),"observed fold/unfold re-arms auto-start");
  p=new GlassesAutoStartPolicy(true);check(!p.connected(0),"restored End must survive service restart");
  System.out.println("PASS: End survives transient reconnects; explicit start and a new wearing cycle re-arm auto-start");
 }
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
}
