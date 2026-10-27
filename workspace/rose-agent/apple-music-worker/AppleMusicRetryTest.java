package dev.pixel.voice;

public class AppleMusicRetryTest {
 static void require(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  require(AppleMusicRetry.allowed("ime_enter","stale_screen",0,200));
  require(AppleMusicRetry.allowed("ime_enter","stale_screen",2,2400));
  require(!AppleMusicRetry.allowed("ime_enter","stale_screen",3,100));
  require(!AppleMusicRetry.allowed("ime_enter","stale_screen",0,2500));
  for(String status:new String[]{"action_accepted","action_failed","input_not_focused","unsupported_action","expired","error"})
   require(!AppleMusicRetry.allowed("ime_enter",status,0,200));
  for(String action:new String[]{"tap","set_text","open_app"})
   require(!AppleMusicRetry.allowed(action,"stale_screen",0,200));
  require(AppleMusicRetry.allowed("tap_label","label_target_mismatch",0,100));
  require(AppleMusicRetry.allowed("tap_label","stale_screen",0,100));
  require(!AppleMusicRetry.allowed("tap_label","action_accepted",0,100));
  require(!AppleMusicRetry.allowed("tap_label","action_failed",0,100));
  require(!AppleMusicRetry.allowed("tap_label","label_target_mismatch",3,100));
  System.out.println("Search retry checks passed: rejected-only, bounded, no replay of accepted or uncertain actions.");
 }
}
