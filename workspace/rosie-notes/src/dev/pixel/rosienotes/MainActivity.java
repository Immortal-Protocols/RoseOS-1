package dev.pixel.rosienotes;

import android.app.Activity;
import android.os.Bundle;
import android.graphics.Color;
import android.widget.*;
import java.text.DateFormat;
import java.lang.ref.WeakReference;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A separate read-only notebook surface for Rosie's actual background workspace.
 * Ignores all incoming intent data. No owner files, clickable URLs, WebView or external actions. */
public final class MainActivity extends Activity {
 private static volatile WeakReference<MainActivity> active = new WeakReference<>(null);
 private final ExecutorService reader = Executors.newSingleThreadExecutor();
 private LinearLayout content;
 private int generation;
 private boolean stopped;
 @Override public void onCreate(Bundle state) {
  super.onCreate(state);
  LinearLayout page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL); page.setPadding(24, 24, 24, 24); page.setBackgroundColor(Color.rgb(250, 247, 244));
  TextView heading = label("Rosie notebook", 25); page.addView(heading);
  page.addView(label("Private notes from Rosie's public research. Read only.", 15));
  Button refresh = new Button(this); refresh.setText("Refresh notes"); refresh.setOnClickListener(v -> refresh()); page.addView(refresh);
  ScrollView scroll = new ScrollView(this); content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); scroll.addView(content);
  page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1)); setContentView(page);
 }
 @Override protected void onResume() { super.onResume(); stopped = false; active = new WeakReference<>(this); refresh(); }
 @Override protected void onPause() { stopped = true; if (active.get() == this) active = new WeakReference<>(null); generation++; super.onPause(); }
 @Override protected void onDestroy() { reader.shutdownNow(); super.onDestroy(); }
 /** Same-process saved-note notification only; no broadcast, network or owner-screen navigation. */
 static void notesChanged() {
  MainActivity activity = active.get();
  if (activity != null) activity.runOnUiThread(() -> { if (active.get() == activity && !activity.stopped) activity.refresh(); });
 }
 private TextView label(String text, int size) {
  TextView view = new TextView(this); view.setText(text); view.setTextSize(size); view.setTextColor(Color.rgb(44, 35, 31)); view.setPadding(0, 12, 0, 12); return view;
 }
 private void refresh() {
  if (stopped || reader.isShutdown()) return;
  int current = ++generation; content.removeAllViews(); content.addView(label("Reading private notes…", 16));
  reader.execute(() -> {
   Map<String, Object> snapshot = new LifeNoteStore(getNoBackupFilesDir(), null).localSnapshot();
   runOnUiThread(() -> { if (!stopped && current == generation && !isFinishing()) show(snapshot); });
  });
 }
 private void show(Map<String, Object> snapshot) {
  content.removeAllViews();
  if (!"ok".equals(snapshot.get("status"))) { content.addView(label("Private notes are unavailable. Nothing was changed.", 16)); return; }
  @SuppressWarnings("unchecked") List<Map<String, Object>> notes = (List<Map<String, Object>>) snapshot.get("notes");
  if (notes.isEmpty()) content.addView(label("No saved research yet. A completed note appears here only after its exact content is saved and read back.", 16));
  if (((Number) snapshot.get("unavailable_count")).intValue() > 0) content.addView(label("Some saved records could not be verified. They have not been replaced.", 16));
  if (((Number) snapshot.get("total")).intValue() > notes.size()) content.addView(label("Showing the 20 most recent verified notes.", 14));
  for (Map<String, Object> note : notes) {
   Button title = new Button(this); title.setAllCaps(false); title.setText((String) note.get("title"));
   title.setOnClickListener(v -> detail(note)); content.addView(title);
   long millis = (long) (((Number) note.get("saved_at")).doubleValue() * 1000);
   content.addView(label(DateFormat.getDateTimeInstance().format(new Date(millis)), 13));
  }
 }
 private void detail(Map<String, Object> note) {
  content.removeAllViews();
  Button back = new Button(this); back.setText("All notes"); back.setOnClickListener(v -> refresh()); content.addView(back);
  content.addView(label((String) note.get("title"), 22));
  TextView body = label((String) note.get("body"), 17); body.setAutoLinkMask(0); body.setLinksClickable(false); content.addView(body);
 }
}
