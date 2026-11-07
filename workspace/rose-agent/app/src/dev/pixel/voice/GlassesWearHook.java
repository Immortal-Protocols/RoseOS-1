package dev.pixel.voice;

/** The glasses-on hook's decision (Nov 7 plan 1O): run the configured action once per real not-worn to worn
 * transition of the glasses.
 *
 * <p>Only a wear signal can drive it. {@link #observe} takes a {@link Source} whose kind says what the signal
 * measures, and anything that is not {@link Source#WEAR_SENSOR} is refused: a Bluetooth/HFP connection, an A2DP
 * stream or the RB Meta hinge (unfold) advertisement are not "worn" and must never be substituted silently.
 * At this revision the repository has no wear source (GlassesAdvertisement reports hinge open/closed only, and
 * GlassesAutoStartPolicy approximates wearing from reconnects), so on a real phone {@link #status} reports the signal
 * as unavailable and the hook never fires until a wear source is added and agreed.
 *
 * <p>Rules: the first observation after start only establishes the state (an unknown-to-worn change is not a
 * transition anyone saw); worn must follow an observed not-worn that lasted at least {@link #MIN_REMOVED_MS}, so a
 * sensor's brief flicker is not a second put-on; repeated worn reports fire nothing; a disabled hook still tracks the
 * state, so enabling it while the glasses are on does not fire until the next put-on; the action and package are read
 * at the moment of the transition, so a change applies from the next transition. Pure logic, no Android types. */
final class GlassesWearHook {
 static final int UNKNOWN = 0, NOT_WORN = 1, WORN = 2;
 /** A removal shorter than this is treated as a flicker of the same wearing. */
 static final long MIN_REMOVED_MS = 3000;

 /** What a signal measures. Only WEAR_SENSOR can drive the hook. */
 static final class Source {
  static final String WEAR_SENSOR = "wear_sensor", CONNECTION = "connection", HINGE = "hinge";
  final String kind, name;
  Source(String kind, String name) { this.kind = kind; this.name = name; }
  boolean isWear() { return WEAR_SENSOR.equals(kind); }
 }

 /** What the caller should do after an observation. */
 static final class Decision {
  static final String NONE = "none", FIRE = "fire", REFUSED = "refused";
  final String kind, action, packageName, reason;
  Decision(String kind, String action, String packageName, String reason) {
   this.kind = kind; this.action = action; this.packageName = packageName; this.reason = reason;
  }
  boolean fires() { return FIRE.equals(kind); }
  static Decision none(String reason) { return new Decision(NONE, null, null, reason); }
 }

 private int state = UNKNOWN;
 private long removedAt = -1, lastFiredAt = -1;
 private Source source;
 private long fires;

 synchronized int state() { return state; }
 synchronized long fires() { return fires; }
 synchronized long lastFiredAt() { return lastFiredAt; }
 synchronized String sourceName() { return source == null ? null : source.name; }

 /** One observation of the glasses' wear state from source at elapsed time now (ms, monotonic). */
 synchronized Decision observe(Source from, int observed, long now, CustomizationConfig config) {
  if (from == null || !from.isWear())
   return new Decision(Decision.REFUSED, null, null, "Not a glasses-worn signal: " + (from == null ? "no source" : from.kind));
  if (observed != WORN && observed != NOT_WORN && observed != UNKNOWN) return new Decision(Decision.REFUSED, null, null, "Unknown wear state");
  source = from;
  int before = state;
  if (observed == UNKNOWN) { state = UNKNOWN; removedAt = -1; return Decision.none("Wear state unknown; the next put-on is not armed"); }
  if (observed == NOT_WORN) {
   if (before != NOT_WORN) removedAt = now;
   state = NOT_WORN;
   return Decision.none("Not worn");
  }
  state = WORN;
  if (before == WORN) return Decision.none("Already worn");
  if (before == UNKNOWN) return Decision.none("First observation; no transition seen");
  if (removedAt < 0 || now - removedAt < MIN_REMOVED_MS) return Decision.none("Removed too briefly to count as a new put-on");
  removedAt = -1;
  if (config == null || !config.hookEnabled) return Decision.none("Hook disabled");
  fires++; lastFiredAt = now;
  return new Decision(Decision.FIRE, config.hookAction,
    CustomizationConfig.ACTION_APP.equals(config.hookAction) ? config.hookPackage : null, "Glasses put on");
 }

 static String stateName(int s) { return s == WORN ? "worn" : s == NOT_WORN ? "not_worn" : "unknown"; }
}
