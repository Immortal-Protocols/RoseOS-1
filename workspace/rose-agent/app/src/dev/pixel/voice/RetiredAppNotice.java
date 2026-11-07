package dev.pixel.voice;

import android.app.Activity;
import android.app.AlertDialog;

/**
 * 1I: the one-time offer to uninstall the retired Rose Models app (AppRemoval.retired). A window-owned dialog, so Home's
 * layout and page plan are untouched. Uninstall records the answer and hands the exact package to Android's own
 * confirmation (MainActivity.openRemoval); Not now, back or a tap outside records the answer and changes nothing.
 */
final class RetiredAppNotice {
 private RetiredAppNotice() {}

 static AlertDialog show(Activity activity, Runnable answered, Runnable uninstall) {
  boolean[] once = {false};
  Runnable record = () -> { if (!once[0]) { once[0] = true; answered.run(); } };
  AlertDialog dialog = new AlertDialog.Builder(activity).setTitle(AppRemoval.RETIRED_TITLE).setMessage(AppRemoval.RETIRED_BODY)
    .setPositiveButton(AppRemoval.RETIRED_UNINSTALL, (d, which) -> { record.run(); uninstall.run(); })
    .setNegativeButton(AppRemoval.RETIRED_LATER, (d, which) -> record.run())
    .setOnCancelListener(d -> record.run())
    .create();
  dialog.show();
  return dialog;
 }
}
