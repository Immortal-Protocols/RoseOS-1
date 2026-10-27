package dev.pixel.voice;

/** Retry only a rejected, undispatched step after fresh inspection and matching. */
final class AppleMusicRetry {
 static boolean allowed(String action,String status,int previousRetries,long phaseMs){
  boolean rejected=("ime_enter".equals(action)&&"stale_screen".equals(status))
      ||("tap_label".equals(action)&&("stale_screen".equals(status)||"label_target_mismatch".equals(status)));
  return rejected&&previousRetries>=0&&previousRetries<3&&phaseMs>=0&&phaseMs<2500;
 }
}
