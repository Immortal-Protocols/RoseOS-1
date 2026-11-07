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
 * to the shipped defaults). Writes use commit(), not apply(), so the caller's read-back is a real verification of what
 * is on disk rather than of the in-memory cache alone. All writes are serialized on the class lock.
 *
 * <p>Home reports what it has drawn through {@link #markHomeApplied}; the command result compares that revision with
 * the stored one, so "saved" and "visible on Home" are never confused. */
final class CustomizationStore {
 private CustomizationStore() {}

 static final String PREFS = "rose-customization", CONFIG = "config", HOME_APPLIED = "homeAppliedRevision";

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
  * an identical request stores nothing and keeps the revision; a failed commit throws IllegalStateException. */
 static synchronized Applied apply(Context c, String operation, Map<String, Object> params) {
  CustomizationConfig before = read(c);
  CustomizationConfig next = before.apply(operation, params);
  if (!next.sameValues(before) && !prefs(c).edit().putString(CONFIG, next.toJson()).commit())
   throw new IllegalStateException("Could not save customization");
  return new Applied(before, next, read(c));
 }

 /** Home calls this after it has drawn revision; older reports never move the mark backwards. */
 static synchronized void markHomeApplied(Context c, long revision) {
  if (revision > homeAppliedRevision(c)) prefs(c).edit().putLong(HOME_APPLIED, revision).apply();
 }

 static synchronized long homeAppliedRevision(Context c) {
  try { return prefs(c).getLong(HOME_APPLIED, 0); } catch (ClassCastException damaged) { return 0; }
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
