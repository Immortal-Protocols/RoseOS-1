package dev.pixel.voice;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.LinkedHashMap;
import java.util.Map;

/** What a customization_* command reports (Nov 7 plan 1O), kept free of Android types so it is host-tested.
 *
 * <p>Three things a caller must never confuse are reported separately:
 * <ul>
 * <li>status: "verified" means the requested value is stored (read back equal), "uncertain" means it may not be, "ok"
 * answers customization_state. It never means "visible on Home".</li>
 * <li>home: whether Home has drawn exactly this revision ("applied"), and only then the column count Home actually
 * draws ("effectiveColumns"), which can be lower than the preference when that many cells do not fit
 * (CustomizationConfig.effectiveHomeColumns). Until Home reports the revision, effectiveColumns is null and a note says
 * why, so Rose never says "6 columns" while Home draws 4.</li>
 * <li>glassesWear: whether a real worn signal exists (GlassesWearHookRunner.status).</li>
 * <li>system (plan 1E): the theme SystemUI acknowledged drawing on the tray link ("shown", null when it has not
 * answered) and whether that is the stored theme ("applied"). Launcher3's chin and switcher and the keyboard follow
 * the setting SystemUI writes before it acknowledges, on their next draw.</li>
 * </ul>
 * {@link #plain} is the argument rule: JSON values become the Long/Double/Boolean/String CustomizationConfig validates;
 * nested objects, arrays, null and anything else are refused. */
final class CustomizationOutcome {
 private CustomizationOutcome() {}

 static final long NEVER_APPLIED = -1;
 static final String HOME_PENDING_NOTE = "Saved, but Home has not drawn this change yet, so its effective column count is not known";
 static final String SYSTEM_PENDING_NOTE = "Saved, but the system surfaces (tray, chin, notifications, keyboard) have not confirmed this theme yet";

 static String status(String operation, boolean verified) {
  return CustomizationConfig.STATE.equals(operation) ? "ok" : verified ? "verified" : "uncertain";
 }

 /** Home's part of the result. appliedRevision/appliedColumns are what Home last reported (NEVER_APPLIED if never). */
 static Map<String, Object> home(long storedRevision, long appliedRevision, int appliedColumns) {
  boolean drawn = appliedRevision != NEVER_APPLIED && appliedRevision == storedRevision;
  Map<String, Object> home = new LinkedHashMap<>();
  home.put("appliedRevision", appliedRevision == NEVER_APPLIED ? MiniJson.NULL : (Object) appliedRevision);
  home.put("applied", drawn);
  home.put("effectiveColumns", drawn && appliedColumns >= CustomizationConfig.MIN_COLUMNS ? (Object) (long) appliedColumns : MiniJson.NULL);
  if (!drawn) home.put("note", HOME_PENDING_NOTE);
  return home;
 }

 /** SystemUI's part of the result: what it last acknowledged showing (null if never, or no SystemUI is linked). */
 static Map<String, Object> system(String storedTheme, String shownTheme) {
  boolean applied = shownTheme != null && shownTheme.equals(storedTheme);
  Map<String, Object> system = new LinkedHashMap<>();
  system.put("shown", shownTheme == null ? MiniJson.NULL : (Object) shownTheme);
  system.put("applied", applied);
  if (!applied) system.put("note", SYSTEM_PENDING_NOTE);
  return system;
 }

 static Object plain(Object v) {
  if (v instanceof String || v instanceof Boolean) return v;
  if (v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte) return ((Number) v).longValue();
  if (v instanceof BigInteger && ((BigInteger) v).bitLength() < 64) return ((BigInteger) v).longValue();
  if (v instanceof BigDecimal) {
   BigDecimal d = (BigDecimal) v;
   try { return d.longValueExact(); } catch (ArithmeticException fraction) { return d.doubleValue(); }
  }
  if (v instanceof Double || v instanceof Float) return ((Number) v).doubleValue();
  throw new IllegalArgumentException("Unsupported argument type");
 }
}
