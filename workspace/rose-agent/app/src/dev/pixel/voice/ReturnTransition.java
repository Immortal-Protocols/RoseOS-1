package dev.pixel.voice;

import android.app.ActivityOptions;
import android.content.Context;
import android.os.Bundle;

/**
 * 1C, V3 motion: on an acknowledged return Rose fades and moves down 8dp over 200 ms with cubic-bezier(.2,.7,.3,1),
 * and the source app stays still; with reduced motion the switch is immediate (no spatial travel). App Core's
 * ReturnSource passes these options with the exact-task transfer.
 */
final class ReturnTransition {
 private ReturnTransition() {}

 static Bundle options(Context c, boolean reducedMotion) {
  if (reducedMotion) return ActivityOptions.makeCustomAnimation(c, 0, 0).toBundle();
  return ActivityOptions.makeCustomAnimation(c, R.anim.rose_return_source, R.anim.rose_return_depart).toBundle();
 }
}
