package dev.pixel.voice;

import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/** The four live customization controls (Nov 7 plan 1O). Conversation and CLI both set these through the same
 * operations, which are listed in assets/customization-agent-tools.json:
 * <ul>
 * <li>Home column count: 0 = automatic (HomeAppLayout.homeColumns), else 3..MAX_HOME_COLUMNS, clamped to what fits;</li>
 * <li>a curated Oxblood wallpaper variant (OxbloodVariants);</li>
 * <li>a bundled font (RoseFonts);</li>
 * <li>the glasses-on hook: enabled, action tray|app, and the app's package (GlassesWearHook).</li>
 * </ul>
 * Values are immutable. {@link #apply} validates one operation strictly: an unknown argument, a wrong type or an
 * unsupported value throws IllegalArgumentException with a short reason, and the old value stays as it was.
 * {@link #parse} never throws: a missing or damaged stored value gives the defaults, which match the shipped
 * behaviour. Pure logic, no Android types, so the contract is testable on the host. */
final class CustomizationConfig {
 static final String STATE = "customization_state", HOME_COLUMNS = "customization_home_columns",
   WALLPAPER = "customization_wallpaper", FONT = "customization_font", GLASSES_HOOK = "customization_glasses_hook";
 static final List<String> OPERATIONS = Collections.unmodifiableList(Arrays.asList(STATE, HOME_COLUMNS, WALLPAPER, FONT, GLASSES_HOOK));

 static final int AUTO_COLUMNS = 0, MIN_COLUMNS = 3, MAX_COLUMNS = HomeAppLayout.MAX_HOME_COLUMNS;
 static final String ACTION_TRAY = "tray", ACTION_APP = "app";
 static final List<String> HOOK_ACTIONS = Collections.unmodifiableList(Arrays.asList(ACTION_TRAY, ACTION_APP));
 /** An Android package name: two or more dot-separated Java identifiers (the server's app-tool rule). */
 static final Pattern PACKAGE = Pattern.compile("^(?:[a-zA-Z][\\w]*\\.)+[\\w]+$");
 static final int MAX_PACKAGE_LENGTH = 255;

 final int homeColumns;
 final String wallpaper, font;
 final boolean hookEnabled;
 final String hookAction, hookPackage;
 /** Grows by one on every accepted change, so Home can tell whether it has applied the latest value. */
 final long revision;

 static final CustomizationConfig DEFAULTS = new CustomizationConfig(AUTO_COLUMNS, OxbloodVariants.CLASSIC, RoseFonts.DEFAULT,
   false, ACTION_TRAY, "", 0);

 CustomizationConfig(int homeColumns, String wallpaper, String font, boolean hookEnabled, String hookAction, String hookPackage, long revision) {
  this.homeColumns = homeColumns; this.wallpaper = wallpaper; this.font = font; this.hookEnabled = hookEnabled;
  this.hookAction = hookAction; this.hookPackage = hookPackage == null ? "" : hookPackage; this.revision = revision;
 }

 static boolean validColumns(int c) { return c == AUTO_COLUMNS || c >= MIN_COLUMNS && c <= MAX_COLUMNS; }

 /** Home's column count for this preference at an inset content width (dp, like HomeAppLayout.homeColumns):
  * automatic is today's width rule; a chosen count is honoured down to MIN_COLUMNS, but never more 60dp cells
  * than fit across with their gaps, so cells never overlap or overflow. */
 static int effectiveHomeColumns(int preferred, float widthDp) {
  int auto = HomeAppLayout.homeColumns(widthDp);
  if (preferred == AUTO_COLUMNS || !validColumns(preferred)) return auto;
  int fit = (int) Math.floor((widthDp + HomeAppLayout.HOME_COL_GAP_DP) / (HomeAppLayout.HOME_CELL_DP + HomeAppLayout.HOME_COL_GAP_DP));
  return Math.max(MIN_COLUMNS, Math.min(preferred, Math.min(MAX_COLUMNS, fit)));
 }

 /** The result of an operation on this value. STATE returns this value unchanged, and so does a request identical
  * to the current value (idempotent). A real change returns a new value with revision + 1. */
 CustomizationConfig apply(String operation, Map<String, Object> params) {
  if (params == null) params = Collections.emptyMap();
  if (STATE.equals(operation)) { only(params); return this; }
  CustomizationConfig next;
  if (HOME_COLUMNS.equals(operation)) {
   only(params, "columns");
   int c = integer(params, "columns");
   if (!validColumns(c)) throw new IllegalArgumentException("columns must be 0 (automatic) or " + MIN_COLUMNS + " to " + MAX_COLUMNS);
   next = new CustomizationConfig(c, wallpaper, font, hookEnabled, hookAction, hookPackage, revision);
  } else if (WALLPAPER.equals(operation)) {
   only(params, "variant");
   String v = string(params, "variant");
   if (!OxbloodVariants.IDS.contains(v)) throw new IllegalArgumentException("Unsupported wallpaper variant");
   next = new CustomizationConfig(homeColumns, v, font, hookEnabled, hookAction, hookPackage, revision);
  } else if (FONT.equals(operation)) {
   only(params, "font");
   String f = string(params, "font");
   if (!RoseFonts.IDS.contains(f)) throw new IllegalArgumentException("Unsupported font");
   next = new CustomizationConfig(homeColumns, wallpaper, f, hookEnabled, hookAction, hookPackage, revision);
  } else if (GLASSES_HOOK.equals(operation)) {
   only(params, "enabled", "action", "packageName");
   if (params.isEmpty()) throw new IllegalArgumentException("Supply enabled, action or packageName");
   boolean enabled = params.containsKey("enabled") ? bool(params, "enabled") : hookEnabled;
   String action = params.containsKey("action") ? string(params, "action") : hookAction;
   if (!HOOK_ACTIONS.contains(action)) throw new IllegalArgumentException("action must be tray or app");
   String pkg = params.containsKey("packageName") ? string(params, "packageName").trim() : hookPackage;
   if (params.containsKey("packageName") && !pkg.isEmpty() && (pkg.length() > MAX_PACKAGE_LENGTH || !PACKAGE.matcher(pkg).matches()))
    throw new IllegalArgumentException("Invalid packageName");
   if (ACTION_APP.equals(action) && pkg.isEmpty()) throw new IllegalArgumentException("action app needs packageName");
   if (ACTION_TRAY.equals(action) && params.containsKey("packageName") && !pkg.isEmpty())
    throw new IllegalArgumentException("packageName applies only to action app");
   if (ACTION_TRAY.equals(action)) pkg = "";
   next = new CustomizationConfig(homeColumns, wallpaper, font, enabled, action, pkg, revision);
  } else throw new IllegalArgumentException("Unknown customization operation");
  return next.sameValues(this) ? this : next.withRevision(revision + 1);
 }

 CustomizationConfig withRevision(long r) { return new CustomizationConfig(homeColumns, wallpaper, font, hookEnabled, hookAction, hookPackage, r); }

 boolean sameValues(CustomizationConfig o) {
  return o != null && homeColumns == o.homeColumns && wallpaper.equals(o.wallpaper) && font.equals(o.font)
    && hookEnabled == o.hookEnabled && hookAction.equals(o.hookAction) && hookPackage.equals(o.hookPackage);
 }

 /** The stored and reported form; key order is stable. */
 Map<String, Object> toMap() {
  Map<String, Object> m = new LinkedHashMap<>();
  m.put("homeColumns", (long) homeColumns);
  m.put("wallpaper", wallpaper);
  m.put("font", font);
  Map<String, Object> hook = new LinkedHashMap<>();
  hook.put("enabled", hookEnabled);
  hook.put("action", hookAction);
  hook.put("packageName", hookPackage);
  m.put("glassesHook", hook);
  m.put("revision", revision);
  return m;
 }

 String toJson() { return json(toMap()); }

 /** A stored value, or the defaults when it is missing or damaged; individually invalid fields fall back alone. */
 static CustomizationConfig parse(String stored) {
  if (stored == null || stored.isEmpty()) return DEFAULTS;
  Object root;
  try { root = MiniJson.parse(stored); } catch (RuntimeException damaged) { return DEFAULTS; }
  if (!(root instanceof Map)) return DEFAULTS;
  Map<?, ?> m = (Map<?, ?>) root;
  int columns = m.get("homeColumns") instanceof Long && validColumns((int) (long) (Long) m.get("homeColumns")) ? (int) (long) (Long) m.get("homeColumns") : DEFAULTS.homeColumns;
  String wallpaper = m.get("wallpaper") instanceof String && OxbloodVariants.IDS.contains(m.get("wallpaper")) ? (String) m.get("wallpaper") : DEFAULTS.wallpaper;
  String font = m.get("font") instanceof String && RoseFonts.IDS.contains(m.get("font")) ? (String) m.get("font") : DEFAULTS.font;
  boolean enabled = DEFAULTS.hookEnabled; String action = DEFAULTS.hookAction, pkg = DEFAULTS.hookPackage;
  if (m.get("glassesHook") instanceof Map) {
   Map<?, ?> h = (Map<?, ?>) m.get("glassesHook");
   Object a = h.get("action"), p = h.get("packageName");
   if (a instanceof String && HOOK_ACTIONS.contains(a)) action = (String) a;
   if (p instanceof String && (((String) p).isEmpty() || PACKAGE.matcher((String) p).matches())) pkg = (String) p;
   if (ACTION_TRAY.equals(action)) pkg = "";
   if (ACTION_APP.equals(action) && pkg.isEmpty()) action = ACTION_TRAY;
   if (h.get("enabled") instanceof Boolean) enabled = (Boolean) h.get("enabled");
  }
  long revision = m.get("revision") instanceof Long && (Long) m.get("revision") >= 0 ? (Long) m.get("revision") : 0;
  return new CustomizationConfig(columns, wallpaper, font, enabled, action, pkg, revision);
 }

 // ---- strict argument readers (the same rules the server's app-tool catalog applies) ----
 private static void only(Map<String, Object> p, String... allowed) {
  List<String> ok = Arrays.asList(allowed);
  for (String k : p.keySet()) if (!ok.contains(k)) throw new IllegalArgumentException("Unknown argument: " + k);
 }
 private static int integer(Map<String, Object> p, String k) {
  Object v = p.get(k);
  if (v instanceof Long || v instanceof Integer) {
   long l = ((Number) v).longValue();
   if (l >= Integer.MIN_VALUE && l <= Integer.MAX_VALUE) return (int) l;
  }
  if (v instanceof Double && !((Double) v).isInfinite() && ((Double) v) == Math.rint((Double) v) && Math.abs((Double) v) < 1e6) return (int) (double) (Double) v;
  throw new IllegalArgumentException(v == null ? "Missing argument: " + k : "Expected integer " + k);
 }
 private static String string(Map<String, Object> p, String k) {
  Object v = p.get(k);
  if (!(v instanceof String)) throw new IllegalArgumentException(v == null ? "Missing argument: " + k : "Expected string " + k);
  return (String) v;
 }
 private static boolean bool(Map<String, Object> p, String k) {
  Object v = p.get(k);
  if (!(v instanceof Boolean)) throw new IllegalArgumentException("Expected boolean " + k);
  return (Boolean) v;
 }

 /** A minimal JSON writer for maps, lists, strings, numbers and booleans (MiniJson is read-only). */
 static String json(Object v) {
  StringBuilder b = new StringBuilder();
  write(b, v);
  return b.toString();
 }
 private static void write(StringBuilder b, Object v) {
  if (v == null || v == MiniJson.NULL) b.append("null");
  else if (v instanceof Boolean || v instanceof Long || v instanceof Integer) b.append(v);
  else if (v instanceof Number) {
   double d = ((Number) v).doubleValue();
   if (Double.isNaN(d) || Double.isInfinite(d)) throw new IllegalArgumentException("non-finite number");
   b.append(d == Math.rint(d) && Math.abs(d) < 1e15 ? Long.toString((long) d) : Double.toString(d));
  } else if (v instanceof Map) {
   b.append('{'); boolean first = true;
   for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
    if (!first) b.append(','); first = false;
    quote(b, String.valueOf(e.getKey())); b.append(':'); write(b, e.getValue());
   }
   b.append('}');
  } else if (v instanceof List) {
   b.append('['); boolean first = true;
   for (Object o : (List<?>) v) { if (!first) b.append(','); first = false; write(b, o); }
   b.append(']');
  } else quote(b, String.valueOf(v));
 }
 private static void quote(StringBuilder b, String s) {
  b.append('"');
  for (int i = 0; i < s.length(); i++) {
   char c = s.charAt(i);
   switch (c) {
    case '"': b.append("\\\""); break;
    case '\\': b.append("\\\\"); break;
    case '\n': b.append("\\n"); break;
    case '\r': b.append("\\r"); break;
    case '\t': b.append("\\t"); break;
    default:
     if (c < 0x20 || c == 0x2028 || c == 0x2029) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
   }
  }
  b.append('"');
 }
}
