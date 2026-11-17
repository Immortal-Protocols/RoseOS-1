package dev.pixel.builder;

import android.content.ActivityNotFoundException;
import dev.pixel.voice.RoseServerLink;

/**
 * The app builder inside Rose Agent, opened from the Agent workspace. It talks to the same Rose server the Agent is
 * paired with, over the Agent's own link (AgentEndpoint): no separate builder app, pairing or server. While the Agent
 * isn't paired, it sends the owner to the Agent's pairing screen and offers the labelled demo.
 */
public final class BuilderActivity extends BuilderScreen {
 /** The Agent pairing the real builder was opened on; a re-pair while this screen lives replaces it on return. */
 private RoseServerLink.Link link;

 @Override BuilderApi connectReal() {
  AgentEndpoint e = AgentEndpoint.current(this);
  if (e == null) return null;
  try { BuilderApi a = new HttpsBuilderApi(e); link = e.link; return a; } catch (java.security.GeneralSecurityException x) { return null; }
 }

 @Override void showPair(String error) {
  screen++;
  openProject = null;
  main.removeCallbacks(poll);
  root.removeAllViews();
  title("Build apps with Rose");
  body("Rose builds your apps on your Rose server, through Rose Agent's own connection to it. Rose Agent isn't paired with your Rose server yet.");
  if (error != null) body(error);
  button("Pair Rose Agent", x -> {
   try { startActivity(RoseServerLink.pairIntent(this)); } catch (ActivityNotFoundException e) { toast("Rose Agent's pairing screen isn't available"); }
  });
  button("Try again", x -> { if (connect()) showConnected(); else showPair(null); });
  button("Try the demo instead", x -> { api = new FakeBuilderApi(); demo = true; showLibrary(); });
 }

 /** Rose Agent keeps its own pairing; its connection reports a removal on its own. Here the owner is told what helps. */
 @Override void pairingRefused() { showPair("Your Rose server no longer accepts this phone. Pair Rose Agent with it again."); }

 @Override String connectLabel() { return "Use your Rose server"; }

 /** Back from pairing Rose Agent: the real builder, with the owner's words if they brought any, without asking again.
  *  If the Agent was paired again (another server or a new phone identity) while this screen lived, its old link can
  *  only fail closed, so the builder starts over on the current pairing. */
 @Override protected void onResume() {
  super.onResume();
  if (demo) return;
  boolean repaired = api != null && link != null && !link.samePairing(RoseServerLink.paired(this));
  if (repaired) { api = null; link = null; }
  if (api == null && connect()) showConnected();
  else if (repaired) showPair(null);
 }
}
