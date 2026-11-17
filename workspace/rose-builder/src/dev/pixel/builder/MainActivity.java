package dev.pixel.builder;

import android.widget.EditText;
import dev.pixel.voice.RoseLink;
import java.util.List;
import java.util.Map;
import javax.net.ssl.SSLSocketFactory;

/**
 * The standalone Rose App Builder, kept only until the builder inside Rose Agent (BuilderActivity) replaces it. The
 * screens are Rose Agent's (BuilderScreen, compiled in from workspace/rose-agent); this app pairs as its own device
 * with its own key and pasted pairing link.
 */
public final class MainActivity extends BuilderScreen {
 @Override BuilderApi connectReal() {
  Pairing p = Pairing.load(this);
  if (p == null) return null;
  try { return new HttpsBuilderApi(endpoint(p)); }
  catch (java.security.GeneralSecurityException e) { Pairing.forget(this); return null; }
 }

 /** This app's own pairing: its saved backend and its own device key. */
 static HttpsBuilderApi.Endpoint endpoint(Pairing p) throws java.security.GeneralSecurityException {
  RoseLink.Key k = RoseLink.existingKey();
  if (k == null || !k.deviceId().equals(p.deviceId)) throw new java.security.GeneralSecurityException("This phone's pairing key is missing; pair again.");
  return new HttpsBuilderApi.Endpoint() {
   @Override public List<String> hosts() {
    List<String> out = new java.util.ArrayList<>();
    for (String h : p.hosts) if (RoseLink.usableHost(h)) out.add(h);  // Tailscale addresses only, as Rose Agent enforces
    return out;
   }
   @Override public int port() { return p.port; }
   @Override public SSLSocketFactory tls() throws java.security.GeneralSecurityException { return RoseLink.pinned(p.pin); }
   @Override public Map<String, String> headers(String method, String path) throws java.security.GeneralSecurityException { return k.headers(method, path, p.pin); }
  };
 }

 /** Pairing: the owner makes a pairing code on their Mac and pastes its link here. */
 @Override void showPair(String error) {
  screen++;
  openProject = null;
  main.removeCallbacks(poll);
  root.removeAllViews();
  title("Connect to your Rose builder");
  body("Apps are built on your Mac, not on this phone. On your Mac, make a Rose pairing code, copy its link (it starts with roseagent://pair) and paste it here. Your phone and Mac both need Tailscale.");
  if (error != null) body(error);
  EditText link = input("roseagent://pair?…");
  button("Connect", x -> {
   String t = link.getText().toString().trim();
   if (t.isEmpty()) { toast("Paste the pairing link first"); return; }
   act(() -> Pairing.pair(this, t), p -> {
    try { api = new HttpsBuilderApi(endpoint(p)); demo = false; showConnected(); }
    catch (java.security.GeneralSecurityException e) { showPair("This phone couldn't use its pairing key. Try again."); }
   }, e -> showPair(e instanceof Pairing.Failure ? e.getMessage() : "Couldn't connect. Try again."));
  });
  button("Try the demo instead", x -> { api = new FakeBuilderApi(); demo = true; showLibrary(); });
 }

 @Override void pairingRefused() {
  Pairing.forget(this);
  showPair("Your Rose builder no longer knows this phone. Make a new pairing code on your Mac and paste it here.");
 }

 @Override String connectLabel() { return "Connect to your Rose builder"; }
}
