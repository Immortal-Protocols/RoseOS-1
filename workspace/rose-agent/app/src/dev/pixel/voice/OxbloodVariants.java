package dev.pixel.voice;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** The curated Oxblood wallpaper variations for Rose Home (Nov 7 plan 1O). Each variant is the shipped Oxblood field
 * (OxbloodField.HOME, unchanged geometry) seen through a small colour transform: a hue turn of at most
 * {@link #MAX_HUE_TURN_DEG} degrees, a saturation and a brightness factor. classic is the shipped field with no
 * transform, so the rose-verify Oxblood reference stays valid while classic is selected. The transforms are bounded so
 * every saturated colour stays in the oxblood family (OxbloodVariantsTest checks this against OxbloodField's own
 * palette); no other theme, colour or image can be chosen.
 *
 * <p>{@link #matrix} returns a 4x5 row-major colour matrix in Android ColorMatrix layout (R, G, B, A rows; columns
 * R, G, B, A, offset with offsets in 0..255), or null for classic. {@link #apply} runs it on one ARGB colour, which the
 * host test uses; on the phone CustomizationVisuals wraps it in a ColorMatrixColorFilter. Pure logic. */
final class OxbloodVariants {
 private OxbloodVariants() {}

 static final String CLASSIC = "classic", GARNET = "garnet", EMBER = "ember", VELVET = "velvet", DUSK = "dusk";
 static final List<String> IDS = Collections.unmodifiableList(Arrays.asList(CLASSIC, GARNET, EMBER, VELVET, DUSK));
 static final float MAX_HUE_TURN_DEG = 12;

 /** hue turn (degrees, + toward orange), saturation factor, brightness factor; classic is the identity. */
 static float[] parameters(String id) {
  if (GARNET.equals(id)) return new float[]{0, 1.15f, 1.12f};
  if (EMBER.equals(id)) return new float[]{8, 1.05f, 1.06f};
  if (VELVET.equals(id)) return new float[]{0, 1.05f, 0.78f};
  if (DUSK.equals(id)) return new float[]{-10, 0.9f, 0.95f};
  if (CLASSIC.equals(id)) return new float[]{0, 1, 1};
  throw new IllegalArgumentException("Unsupported wallpaper variant");
 }

 static String label(String id) {
  if (GARNET.equals(id)) return "Garnet";
  if (EMBER.equals(id)) return "Ember";
  if (VELVET.equals(id)) return "Velvet";
  if (DUSK.equals(id)) return "Dusk";
  if (CLASSIC.equals(id)) return "Oxblood";
  throw new IllegalArgumentException("Unsupported wallpaper variant");
 }

 /** The variant's colour matrix, or null for classic (draw the shipped field untouched). */
 static float[] matrix(String id) {
  if (CLASSIC.equals(id)) return null;
  float[] p = parameters(id);
  float[] m = multiply(saturate(p[1]), hueRotate(p[0]));
  for (int row = 0; row < 3; row++) for (int col = 0; col < 3; col++) m[row * 5 + col] *= p[2];
  return m;
 }

 /** W3C feColorMatrix hueRotate (luminance-preserving), as a 4x5 matrix. */
 static float[] hueRotate(float degrees) {
  double a = Math.toRadians(degrees), c = Math.cos(a), s = Math.sin(a);
  return new float[]{
    (float) (.213 + c * .787 - s * .213), (float) (.715 - c * .715 - s * .715), (float) (.072 - c * .072 + s * .928), 0, 0,
    (float) (.213 - c * .213 + s * .143), (float) (.715 + c * .285 + s * .140), (float) (.072 - c * .072 - s * .283), 0, 0,
    (float) (.213 - c * .213 - s * .787), (float) (.715 - c * .715 + s * .715), (float) (.072 + c * .928 + s * .072), 0, 0,
    0, 0, 0, 1, 0};
 }

 /** W3C feColorMatrix saturate, as a 4x5 matrix. */
 static float[] saturate(float s) {
  return new float[]{
    .213f + .787f * s, .715f - .715f * s, .072f - .072f * s, 0, 0,
    .213f - .213f * s, .715f + .285f * s, .072f - .072f * s, 0, 0,
    .213f - .213f * s, .715f - .715f * s, .072f + .928f * s, 0, 0,
    0, 0, 0, 1, 0};
 }

 /** a then b: (b * a) for colour matrices applied to column vectors (offsets compose too). */
 static float[] multiply(float[] b, float[] a) {
  float[] out = new float[20];
  for (int row = 0; row < 4; row++) {
   for (int col = 0; col < 4; col++) {
    float sum = 0;
    for (int k = 0; k < 4; k++) sum += b[row * 5 + k] * a[k * 5 + col];
    out[row * 5 + col] = sum;
   }
   float offset = b[row * 5 + 4];
   for (int k = 0; k < 4; k++) offset += b[row * 5 + k] * a[k * 5 + 4];
   out[row * 5 + 4] = offset;
  }
  return out;
 }

 /** The matrix applied to one ARGB colour, clamped like Android's ColorMatrixColorFilter. */
 static int apply(float[] m, int argb) {
  if (m == null) return argb;
  float r = (argb >> 16) & 255, g = (argb >> 8) & 255, b = argb & 255, a = (argb >>> 24) & 255;
  int nr = clamp(m[0] * r + m[1] * g + m[2] * b + m[3] * a + m[4]);
  int ng = clamp(m[5] * r + m[6] * g + m[7] * b + m[8] * a + m[9]);
  int nb = clamp(m[10] * r + m[11] * g + m[12] * b + m[13] * a + m[14]);
  int na = clamp(m[15] * r + m[16] * g + m[17] * b + m[18] * a + m[19]);
  return (na << 24) | (nr << 16) | (ng << 8) | nb;
 }
 private static int clamp(float v) { return Math.max(0, Math.min(255, Math.round(v))); }

 /** HSL hue in degrees [0, 360), saturation and lightness in [0, 1], of an RGB colour. */
 static float[] hsl(int rgb) {
  float r = ((rgb >> 16) & 255) / 255f, g = ((rgb >> 8) & 255) / 255f, b = (rgb & 255) / 255f;
  float max = Math.max(r, Math.max(g, b)), min = Math.min(r, Math.min(g, b)), l = (max + min) / 2, d = max - min;
  if (d == 0) return new float[]{0, 0, l};
  float s = d / (1 - Math.abs(2 * l - 1));
  float h;
  if (max == r) h = 60 * (((g - b) / d) % 6);
  else if (max == g) h = 60 * ((b - r) / d + 2);
  else h = 60 * ((r - g) / d + 4);
  if (h < 0) h += 360;
  return new float[]{h, s, l};
 }

 /** Whether a colour belongs to the oxblood family: either a near-neutral (desaturated ink, highlight or near-black)
  * or a red whose hue lies between crimson-magenta and red-orange, and never bright. */
 static boolean oxbloodFamily(int rgb) {
  float[] h = hsl(rgb);
  if (h[1] < 0.2f || h[2] < 0.04f || h[2] > 0.88f) return true;
  // A pale tint is one of the field's warm highlights (the ivory beam edges, the rose hairlines): warm hue only.
  if (h[2] > 0.72f) return h[0] >= 325 || h[0] <= 35;
  return h[0] >= 325 || h[0] <= 22;
 }
}
