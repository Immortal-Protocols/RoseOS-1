package dev.pixel.voice;

import android.graphics.Rect;
import android.graphics.RectF;
import android.view.View;
import android.view.ViewGroup;

/**
 * Where a closing app lands on Home (GestureContract): the window rect of its tile's 59dp glass mark (never the label)
 * on the page Home is showing, or, for an app kept in the System folder, the folder's cell. Only a mark that is laid
 * out and wholly visible counts; anything else is no target, and Home does not reply. Views only: no Activity.
 */
final class HomeGestureTarget {
 private HomeGestureTarget() {}

 /** @param page the page Home is showing; tag the app's GestureContract.tag; inFolder the app is a System folder app;
  * windowWidth/windowHeight Home's window. Returns null when there is nothing fully visible to report. */
 static RectF find(View page, String tag, boolean inFolder, int windowWidth, int windowHeight) {
  if (page == null || tag == null) return null;
  View tile = page.findViewWithTag(tag);
  if (tile == null && inFolder) tile = page.findViewWithTag(GestureContract.FOLDER_TAG);
  if (tile == null) return null;
  View mark = tile instanceof ViewGroup && ((ViewGroup) tile).getChildCount() > 0 ? ((ViewGroup) tile).getChildAt(0) : tile;
  if (!mark.isShown() || !mark.isLaidOut() || mark.getWidth() <= 0 || mark.getHeight() <= 0) return null;
  Rect visible = new Rect();
  if (!mark.getGlobalVisibleRect(visible) || visible.width() != mark.getWidth() || visible.height() != mark.getHeight()) return null;
  int[] at = new int[2];
  mark.getLocationInWindow(at);
  float left = at[0], top = at[1], right = left + mark.getWidth(), bottom = top + mark.getHeight();
  return GestureContract.rect(left, top, right, bottom, windowWidth, windowHeight) ? new RectF(left, top, right, bottom) : null;
 }
}
