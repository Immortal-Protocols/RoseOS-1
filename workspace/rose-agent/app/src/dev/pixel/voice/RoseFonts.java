package dev.pixel.voice;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Rose's bundled font choices (Nov 7 plan 1O). Every choice is already on the phone, so a change is live and needs
 * neither an OS rebuild nor a download: nunito is the app's own OFL asset (assets/fonts/nunito.ttf, licence in
 * assets/licenses/Nunito-OFL.txt); sans, serif, condensed and mono are the platform's standard family aliases
 * (fonts.xml), which every AOSP/GrapheneOS image resolves. default is Rose's shipped type and changes nothing:
 * Nunito for conversation text and the system sans-serif for Home labels.
 *
 * <p>{@link #conversationFamily} and {@link #labelFamily} name the family for each of Rose's two text roles; the
 * value {@link #NUNITO_ASSET} means the bundled asset, anything else is a platform family name for
 * Typeface.create. CustomizationVisuals turns these into Typefaces. Pure logic, no Android types. */
final class RoseFonts {
 private RoseFonts() {}

 static final String DEFAULT = "default", NUNITO = "nunito", SANS = "sans", SERIF = "serif", CONDENSED = "condensed", MONO = "mono";
 static final List<String> IDS = Collections.unmodifiableList(Arrays.asList(DEFAULT, NUNITO, SANS, SERIF, CONDENSED, MONO));
 /** The bundled asset path; also the marker returned for "use the asset". */
 static final String NUNITO_ASSET = "fonts/nunito.ttf";

 /** Family for conversation, cards and Rose's own controls (today MainActivity.chatFont: Nunito). */
 static String conversationFamily(String id) {
  if (DEFAULT.equals(id) || NUNITO.equals(id)) return NUNITO_ASSET;
  return platformFamily(id);
 }

 /** Family for Home and folder labels (today HomeAppTile/HomeFolder: sans-serif). */
 static String labelFamily(String id) {
  if (DEFAULT.equals(id)) return "sans-serif";
  if (NUNITO.equals(id)) return NUNITO_ASSET;
  return platformFamily(id);
 }

 static String platformFamily(String id) {
  if (SANS.equals(id)) return "sans-serif";
  if (SERIF.equals(id)) return "serif";
  if (CONDENSED.equals(id)) return "sans-serif-condensed";
  if (MONO.equals(id)) return "monospace";
  throw new IllegalArgumentException("Unsupported font");
 }

 static String label(String id) {
  if (DEFAULT.equals(id)) return "Rose default";
  if (NUNITO.equals(id)) return "Nunito";
  if (SANS.equals(id)) return "Sans";
  if (SERIF.equals(id)) return "Serif";
  if (CONDENSED.equals(id)) return "Condensed";
  if (MONO.equals(id)) return "Mono";
  throw new IllegalArgumentException("Unsupported font");
 }
}
