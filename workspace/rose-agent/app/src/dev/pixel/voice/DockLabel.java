package dev.pixel.voice;

/** How a Home dock label fits its tile (OS-HOME-2804-FIX-001 M008). The full label is always kept: it
 * stays on one line at the dock size when it fits; a label with words wraps once at a space; a single
 * word shrinks to no less than minPx; anything still longer wraps to two lines and only then ends with an
 * ellipsis. Sizes are in pixels already scaled by the owner's font size. Pure logic, no Android types. */
final class DockLabel {
 interface Measure { float width(String text, float sizePx); }

 final String text;
 final float sizePx;
 final int lines;
 final boolean ellipsize;

 private DockLabel(String text, float sizePx, int lines, boolean ellipsize) {
  this.text = text; this.sizePx = sizePx; this.lines = lines; this.ellipsize = ellipsize;
 }

 static DockLabel fit(String label, float basePx, float minPx, float available, Measure measure) {
  if (measure.width(label, basePx) <= available) return new DockLabel(label, basePx, 1, false);
  // Two lines at a space, as balanced as possible, each fitting at the dock size.
  String best = null;
  float bestWidest = Float.MAX_VALUE;
  for (int i = label.indexOf(' '); i > 0; i = label.indexOf(' ', i + 1)) {
   String first = label.substring(0, i).trim(), second = label.substring(i + 1).trim();
   if (first.isEmpty() || second.isEmpty()) continue;
   float widest = Math.max(measure.width(first, basePx), measure.width(second, basePx));
   if (widest <= available && widest < bestWidest) { best = first + "\n" + second; bestWidest = widest; }
  }
  if (best != null) return new DockLabel(best, basePx, 2, false);
  // A single long word: shrink a little, never below minPx.
  float size = basePx;
  while (size > minPx && measure.width(label, size) > available) size = Math.max(minPx, size - 0.5f);
  if (measure.width(label, size) <= available) return new DockLabel(label, size, 1, false);
  // Bounded overflow: wrap within two lines at the smallest size, ellipsis only past the second line.
  return new DockLabel(label, minPx, 2, true);
 }
}
