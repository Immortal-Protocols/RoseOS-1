package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import java.util.IdentityHashMap;
import java.util.Map;

/** Durable home of the four customization controls (Nov 7 plan 1O). Conversation and CLI both write through
 * {@link #apply}, Home and the conversation surface listen through {@link #register}, so a change is live in every
 * surface that has integrated the listener, without an OS rebuild or a restart.
 *
 * <p>Its own preferences file, "rose-customization", keeps these values apart from every other Rose preference: no
 * other code reads or clears it, and a damaged value only resets customization (CustomizationConfig.parse falls back
 * to the shipped defaults). Writes use commit(), whose result says whether the value reached disk; the read-back that
 * follows reads SharedPreferences' in-memory map, so it confirms what every reader now sees, not the disk itself. When
 * commit() fails, the in-memory map (and its listeners) already hold the new value, so the previous value is written
 * back and the result is unverified, instead of reporting an error while customization_state shows a change that a
 * restart would revert. All writes are serialized on the class lock.
 *
 * <p>Home reports what it has drawn through {@link #markHomeApplied}; the command result compares that revision with
 * the stored one, so "saved" and "visible on Home" are never confused. NEVER_APPLIED means Home has never reported.
 * A change always gets a revision above the mark, even after a damaged config reset the revision to 0, so a reset
 * can never make an undrawn revision look drawn. */
final class CustomizationStore {
 private CustomizationStore() {}

 static final String PREFS = "rose-customization", CONFIG = "config", HOME_APPLIED = "homeAppliedRevision",
   HOME_COLUMNS = "homeAppliedColumns";
 static final long NEVER_APPLIED = CustomizationOutcome.NEVER_APPLIED;

 /** The outcome of one operation: the value before, the value the operation asked for, and the stored read-back. */
 static final class Applied {
  final CustomizationConfig before, requested, stored;
  Applied(CustomizationConfig before, CustomizationConfig requested, CustomizationConfig stored) {
   this.before = before; this.requested = requested; this.stored = stored;
  }
  boolean changed() { return !requested.sameValues(before); }
  boolean verified() { return stored.sameValues(requested); }
 }

 private static SharedPreferences prefs(Context c) { return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE); }

 static synchronized CustomizationConfig read(Context c) { return CustomizationConfig.parse(prefs(c).getString(CONFIG, null)); }

 /** Validates and stores one operation. A validation failure throws IllegalArgumentException and stores nothing;
  * an identical request stores nothing and keeps the revision; a failed commit restores the previous value and
  * returns an unverified result. */
 static synchronized Applied apply(Context c, String operation, Map<String, Object> params) {
  CustomizationConfig before = read(c);
  CustomizationConfig next = before.apply(operation, params);
  long mark = homeAppliedRevision(c);
  if (!next.sameValues(before) && next.revision <= mark) next = next.withRevision(mark + 1);
  if (!next.sameValues(before) && !prefs(c).edit().putString(CONFIG, next.toJson()).commit()) {
   prefs(c).edit().putString(CONFIG, before.toJson()).commit(); // best effort; the result is unverified either way
   CustomizationConfig now = read(c);
   return new Applied(before, next, now.sameValues(next) ? before : now);
  }
  return new Applied(before, next, read(c));
 }

 /** Home calls this after it has drawn revision with effectiveColumns (the count it actually lays out); older
  * reports never move the mark backwards. Both values are written together. */
 static synchronized void markHomeApplied(Context c, long revision, int effectiveColumns) {
  long mark = homeAppliedRevision(c);
  if (revision > mark || revision == mark && effectiveColumns != homeAppliedColumns(c))
   prefs(c).edit().putLong(HOME_APPLIED, revision).putInt(HOME_COLUMNS, effectiveColumns).apply();
 }

 static synchronized int homeAppliedColumns(Context c) {
  try { return prefs(c).getInt(HOME_COLUMNS, 0); } catch (ClassCastException damaged) { return 0; }
 }

 static synchronized long homeAppliedRevision(Context c) {
  try { return prefs(c).getLong(HOME_APPLIED, NEVER_APPLIED); } catch (ClassCastException damaged) { return NEVER_APPLIED; }
 }

 // SharedPreferences holds its listeners weakly, so the store keeps them alive until unregister.
 private static final Map<Runnable, SharedPreferences.OnSharedPreferenceChangeListener> LISTENERS = new IdentityHashMap<>();
 private static Handler main;

 /** Runs onChange on the main thread after every change to the stored config (not to the applied mark). */
 static synchronized void register(Context c, final Runnable onChange) {
  if (onChange == null || LISTENERS.containsKey(onChange)) return;
  if (main == null) main = new Handler(Looper.getMainLooper());
  final Handler h = main;
  SharedPreferences.OnSharedPreferenceChangeListener l = (p, key) -> { if (key == null || CONFIG.equals(key)) h.post(onChange); };
  LISTENERS.put(onChange, l);
  prefs(c).registerOnSharedPreferenceChangeListener(l);
 }

 static synchronized void unregister(Context c, Runnable onChange) {
  SharedPreferences.OnSharedPreferenceChangeListener l = LISTENERS.remove(onChange);
  if (l != null) prefs(c).unregisterOnSharedPreferenceChangeListener(l);
 }
}
