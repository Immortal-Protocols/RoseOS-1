package dev.pixel.voice;

import android.content.Context;
import android.content.Intent;
import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.List;
import java.util.Map;
import javax.net.ssl.SSLSocketFactory;

/**
 * Rose Agent's existing link to its one Rose server, for the app builder screens (dev.pixel.builder) inside Rose Agent:
 * the paired addresses and port (LocalRoseConnection.paired), TLS that accepts only the pinned certificate
 * (LocalRoseConnection.pinned) and the phone's own pairing key signing rose-auth-v1 (LocalRoseKey, LocalRoseAuth), as
 * WorkspaceTaskTransport uses them. Nothing new is trusted, stored, requested or paired, and the key never leaves
 * this class.
 */
public final class RoseServerLink {
 private RoseServerLink() {}

 /** One paired Rose server. */
 public static final class Link {
  public final String serverName;
  /** Tailscale addresses only, the one that worked last first. */
  public final List<String> hosts;
  public final int port;
  private final String pin, deviceId;

  Link(String serverName, List<String> hosts, int port, String pin, String deviceId) {
   this.serverName = serverName; this.hosts = hosts; this.port = port; this.pin = pin; this.deviceId = deviceId;
  }

  /** TLS that accepts only this server's certificate. */
  public SSLSocketFactory tls() throws GeneralSecurityException {
   try { return LocalRoseConnection.pinned(pin); }
   catch (IOException e) { throw new GeneralSecurityException("pinned TLS unavailable"); }
  }

  /** The four rose-auth-v1 headers for one request, signed now with a fresh nonce by this phone's pairing key. */
  public Map<String, String> headers(String method, String path) throws GeneralSecurityException {
   LocalRoseKey key = LocalRoseKey.load();
   if (key == null || !key.deviceId().equals(deviceId)) throw new GeneralSecurityException("pairing key unavailable");
   return LocalRoseAuth.headers(deviceId, method, path, System.currentTimeMillis(), LocalRoseAuth.newNonce(), pin, key);
  }
 }

 /** Rose Agent's pairing with its Rose server, or null while the Agent isn't paired. */
 public static Link paired(Context context) {
  LocalRoseConnection.Paired p = LocalRoseConnection.paired(context.getApplicationContext());
  return p == null ? null : new Link(p.serverName, p.order(), p.port, p.pin, p.deviceId);
 }

 /** Rose Agent's own pairing screen. */
 public static Intent pairIntent(Context context) { return new Intent(context, LocalRosePairActivity.class); }
}
