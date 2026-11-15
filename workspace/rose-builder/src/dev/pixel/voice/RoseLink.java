package dev.pixel.voice;

import android.content.Context;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Rose App Builder's window onto Rose Agent's rose-auth-v1 code (LocalRoseAuth, LocalRoseKey), compiled into this app
 * from workspace/rose-agent so both apps sign exactly the same canonical bytes and share its pinned cross-language
 * vectors. This app pairs as its own device with its own key in its own Android Keystore; it never sees Rose Agent's
 * key or pairing. Nothing here changes the protocol.
 */
public final class RoseLink {
 private RoseLink() {}

 /** A validated pairing invite (roseagent://pair?…): Tailscale hosts only, the backend's certificate pin, a one-time code. */
 public static final class Invite {
  public final String name, pin, code, serverId; public final List<String> hosts; public final int port;
  Invite(LocalRoseAuth.Invite i) { name = i.name; pin = i.pin; code = i.code; serverId = i.serverId(); hosts = new ArrayList<>(i.hosts); port = i.port; }
 }

 /** Parses a pasted invite; the exception's message is shown to the owner. */
 public static Invite parseInvite(String text) throws IllegalArgumentException {
  try { return new Invite(LocalRoseAuth.parseInvite(text)); }
  catch (LocalRoseAuth.InvalidInvite e) { throw new IllegalArgumentException(e.getMessage()); }
 }

 /** Tailscale-only endpoints, as Rose Agent enforces. */
 public static boolean usableHost(String host) { return LocalRoseAuth.tailscaleHost(host); }

 /** This app's device key, in secure hardware; created at the first pairing. */
 public static final class Key {
  final LocalRoseKey key;
  Key(LocalRoseKey key) { this.key = key; }
  public String deviceId() { return key.deviceId(); }
  public String publicKeyBase64() { return Base64.getEncoder().encodeToString(key.publicKey()); }
  /** The four rose-auth-v1 headers for one request, signed now with a fresh nonce. */
  public Map<String, String> headers(String method, String path, String pin) throws GeneralSecurityException {
   return LocalRoseAuth.headers(key.deviceId(), method, path, System.currentTimeMillis(), LocalRoseAuth.newNonce(), pin, key);
  }
 }

 public static Key key(Context context) throws GeneralSecurityException { return new Key(LocalRoseKey.loadOrCreate(context)); }

 /** The existing key, or null before the first pairing. */
 public static Key existingKey() throws GeneralSecurityException { LocalRoseKey k = LocalRoseKey.load(); return k == null ? null : new Key(k); }

 /** TLS that accepts only the backend whose certificate hashes to the pin (the pin is its identity). */
 public static SSLSocketFactory pinned(String pin) throws GeneralSecurityException {
  X509TrustManager trust = new X509TrustManager() {
   @Override public void checkClientTrusted(X509Certificate[] chain, String auth) throws CertificateException { throw new CertificateException("not a server"); }
   @Override public void checkServerTrusted(X509Certificate[] chain, String auth) throws CertificateException {
    if (chain == null || chain.length == 0 || !LocalRoseAuth.pinMatches(chain[0].getEncoded(), pin)) throw new CertificateException("not the paired backend");
   }
   @Override public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
  };
  SSLContext ssl = SSLContext.getInstance("TLS");
  ssl.init(null, new TrustManager[]{trust}, new SecureRandom());
  return ssl.getSocketFactory();
 }
}
