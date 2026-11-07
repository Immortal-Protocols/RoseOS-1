package dev.pixel.voice;

import android.content.Context;
import android.graphics.ColorFilter;
import android.graphics.ColorMatrix;
import android.graphics.ColorMatrixColorFilter;
import android.graphics.Typeface;

/** Android rendering of the wallpaper and font choices (Nov 7 plan 1O). OxbloodVariants and RoseFonts decide; this
 * class only turns their answers into a ColorFilter and Typefaces, so the decisions stay host-testable.
 *
 * <p>The defaults reproduce today's drawing exactly: classic has no filter, the default conversation face is
 * MainActivity.chatFont (the Nunito asset at the requested weight) and the default label face is
 * Typeface.create(Typeface.SANS_SERIF, weight, false) as HomeAppTile and HomeFolder use. Nothing here throws: a
 * missing asset or an unknown id falls back to the default face, because a font choice must never break Home.
 * Weighted Typeface.create needs API 28; minSdk is 29. */
final class CustomizationVisuals {
 private CustomizationVisuals() {}

 private static volatile Typeface nunito;

 /** The Home wallpaper filter for variant, or null for classic (and for any id OxbloodVariants does not know). */
 static ColorFilter wallpaperFilter(String variant) {
  try {
   float[] m = OxbloodVariants.matrix(variant);
   return m == null ? null : new ColorMatrixColorFilter(new ColorMatrix(m));
  } catch (RuntimeException unknown) { return null; }
 }

 static Typeface conversationTypeface(Context ctx, String fontId, int weight) {
  String family;
  try { family = RoseFonts.conversationFamily(fontId); } catch (RuntimeException unknown) { family = RoseFonts.NUNITO_ASSET; }
  Typeface t = resolve(ctx, family, weight);
  return t != null ? t : fallbackConversation(ctx, weight);
 }

 static Typeface labelTypeface(Context ctx, String fontId, int weight) {
  String family;
  try { family = RoseFonts.labelFamily(fontId); } catch (RuntimeException unknown) { family = "sans-serif"; }
  Typeface t = resolve(ctx, family, weight);
  return t != null ? t : weighted(Typeface.SANS_SERIF, weight);
 }

 private static Typeface fallbackConversation(Context ctx, int weight) {
  Typeface n = nunito(ctx);
  return weighted(n != null ? n : Typeface.DEFAULT, weight);
 }

 private static Typeface resolve(Context ctx, String family, int weight) {
  try {
   Typeface base;
   if (RoseFonts.NUNITO_ASSET.equals(family)) base = nunito(ctx);
   else if ("sans-serif".equals(family)) base = Typeface.SANS_SERIF;
   else if ("serif".equals(family)) base = Typeface.SERIF;
   else if ("monospace".equals(family)) base = Typeface.MONOSPACE;
   else base = Typeface.create(family, Typeface.NORMAL);
   return base == null ? null : weighted(base, weight);
  } catch (RuntimeException failed) { return null; }
 }

 private static Typeface nunito(Context ctx) {
  Typeface n = nunito;
  if (n == null && ctx != null) {
   synchronized (CustomizationVisuals.class) {
    if (nunito == null) try { nunito = Typeface.createFromAsset(ctx.getApplicationContext().getAssets(), RoseFonts.NUNITO_ASSET); } catch (RuntimeException missing) { return null; }
    n = nunito;
   }
  }
  return n;
 }

 private static Typeface weighted(Typeface base, int weight) {
  try { return Typeface.create(base, Math.max(1, Math.min(1000, weight)), false); } catch (RuntimeException failed) { return base; }
 }
}
