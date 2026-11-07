package dev.pixel.voice;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * The Rose Agent settings dialog's view (RoseSettingsModel): a Mac section with the one action, Pair with Mac, and
 * its state line; an About section with the app name and version; Close. A window-owned dialog, so it never depends
 * on the position or attachment of a Home anchor. SettingsDialog supplies the text and the pair action.
 */
final class SettingsScreen {
 private SettingsScreen() {}

 static AlertDialog show(Activity activity, String pairingState, String version, Runnable pair) {
  float density = activity.getResources().getDisplayMetrics().density;
  int pad = Math.round(24 * density), gap = Math.round(8 * density);
  LinearLayout page = new LinearLayout(activity);
  page.setOrientation(LinearLayout.VERTICAL);
  page.setPadding(pad, gap, pad, 0);

  page.addView(heading(activity, "Mac"));
  LinearLayout row = new LinearLayout(activity);
  row.setOrientation(LinearLayout.VERTICAL);
  row.setPadding(0, gap, 0, gap);
  row.setClickable(true);
  row.setFocusable(true);
  android.util.TypedValue ripple = new android.util.TypedValue();
  if (activity.getTheme().resolveAttribute(android.R.attr.selectableItemBackground, ripple, true))
   row.setBackgroundResource(ripple.resourceId);
  TextView title = text(activity, RoseSettingsModel.PAIR, 18), state = text(activity, pairingState, 14);
  title.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  state.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  row.addView(title);
  row.addView(state);
  row.setContentDescription(RoseSettingsModel.PAIR + ". " + pairingState);
  row.setTag("pair-with-mac");
  page.addView(row);

  page.addView(heading(activity, RoseSettingsModel.ABOUT));
  page.addView(text(activity, RoseSettingsModel.APP, 16));
  TextView versionLine = text(activity, version, 14);
  versionLine.setPadding(0, 0, 0, gap);
  page.addView(versionLine);

  AlertDialog dialog = new AlertDialog.Builder(activity).setTitle(RoseSettingsModel.TITLE).setView(page)
   .setNegativeButton("Close", null).create();
  row.setOnClickListener(v -> {
   dialog.dismiss();
   pair.run();
  });
  dialog.show();
  return dialog;
 }

 private static TextView heading(Context c, String label) {
  TextView t = text(c, label, 13);
  t.setAllCaps(true);
  t.setAccessibilityHeading(true);
  t.setPadding(0, Math.round(12 * c.getResources().getDisplayMetrics().density), 0, 0);
  return t;
 }

 private static TextView text(Context c, CharSequence value, float sp) {
  TextView t = new TextView(c);
  t.setText(value);
  t.setTextSize(sp);
  return t;
 }
}
