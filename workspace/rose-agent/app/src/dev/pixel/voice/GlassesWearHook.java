package dev.pixel.voice;

/** The glasses-on hook's decision (Nov 7 plan 1O): run the configured action once per not-worn to worn transition.
 *
 * <p>Two sources can drive it. {@link Source#BLUETOOTH_AUDIO} is the owner's chosen signal (Nathan, 2026-11-07): any
 * Bluetooth audio device connecting counts as glasses on, and the last one disconnecting counts as removal
 * (BluetoothAudioWear). It is a proxy, not a worn sensor, and status says so. {@link Source#WEAR_SENSOR} is kept for a
 * real worn signal if one is ever added. Any other kind is refused, including a generic connection event and the RB
 * Meta hinge (unfold) advertisement.
 *
 * <p>Rules:
 * <ul>
 * <li>The first observation after start only establishes the state. A device already connected at start, an unknown
 * state followed by worn, is not a transition.</li>
 * <li>Worn must follow an observed not-worn that lasted at least {@link #MIN_REMOVED_MS}, so a brief disconnect is not
 * a second put-on.</li>
 * <li>Repeated worn reports fire nothing.</li>
 * <li>A disabled hook still tracks the state.</li>
 * <li>The action and package are read at the transition, so a change applies from the next one.</li>
 * </ul>
 * Pure logic, no Android types. */
final class GlassesWearHook {
 static final int UNKNOWN = 0, NOT_WORN = 1, WORN = 2;
 /** A removal shorter than this is treated as a flicker of the same wearing. */
 static final long MIN_REMOVED_MS = 3000;

 /** What a signal measures. Only WEAR_SENSOR can drive the hook. */
 static final class Source {
  static final String WEAR_SENSOR = "wear_sensor", BLUETOOTH_AUDIO = "bluetooth_audio", CONNECTION = "connection", HINGE = "hinge";
  final String kind, name;
  Source(String kind, String name) { this.kind = kind; this.name = name; }
  /** Whether this source may drive the hook: a worn sensor, or the owner's chosen Bluetooth audio connection. */
  boolean isWear() { return WEAR_SENSOR.equals(kind) || BLUETOOTH_AUDIO.equals(kind); }
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
   return new Decision(Decision.REFUSED, null, null, "Not a signal the hook accepts: " + (from == null ? "no source" : from.kind));
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
