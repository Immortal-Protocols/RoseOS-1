package dev.pixel.builder;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import dev.pixel.voice.RoseLink;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.SSLSocketFactory;

/**
 * Project 1: this app's pairing with the owner's Rose builder, as its own device (App Core: the server identifies a
 * device by key, not by package). The owner pastes a pairing link made on their Mac; the app sends its public key
 * with the one-time code (POST /pair, as Rose Agent does), proves the key with a signed GET /hello, and keeps only the
 * backend's name, addresses, port, certificate pin and this device's id. The private key stays in this app's Android
 * Keystore. Nothing here is a credential or secret; unpairing forgets the backend, never the key.
 */
final class Pairing {
 static final String PREFS = "builder-pairing";

 final String name, pin, deviceId; final List<String> hosts; final int port;

 Pairing(String name, String pin, String deviceId, List<String> hosts, int port) {
  this.name = name; this.pin = pin; this.deviceId = deviceId; this.hosts = hosts; this.port = port;
 }

 static final class Failure extends Exception { Failure(String m) { super(m); } }

 static Pairing load(Context c) {
  SharedPreferences p = c.getSharedPreferences(PREFS, 0);
  String pin = p.getString("pin", null), id = p.getString("deviceId", null), hosts = p.getString("hosts", null);
  int port = p.getInt("port", 0);
  if (pin == null || id == null || hosts == null || port < 1) return null;
  List<String> list = new ArrayList<>();
  for (String h : hosts.split(",")) if (RoseLink.usableHost(h)) list.add(h);
  if (list.isEmpty()) return null;
  return new Pairing(p.getString("name", "your Rose builder"), pin, id, list, port);
 }

 void save(Context c) {
  c.getSharedPreferences(PREFS, 0).edit().putString("name", name).putString("pin", pin).putString("deviceId", deviceId)
    .putString("hosts", String.join(",", hosts)).putInt("port", port).apply();
 }

 static void forget(Context c) { c.getSharedPreferences(PREFS, 0).edit().clear().apply(); }

 /** Pairs with the backend named in a pasted link. Network: call off the main thread. */
 static Pairing pair(Context c, String link) throws Failure {
  RoseLink.Invite invite;
  try { invite = RoseLink.parseInvite(link); } catch (IllegalArgumentException e) { throw new Failure(e.getMessage()); }
  RoseLink.Key key;
  SSLSocketFactory tls;
  try { key = RoseLink.key(c); tls = RoseLink.pinned(invite.pin); }
  catch (java.security.GeneralSecurityException e) { throw new Failure(e.getMessage() == null ? "This phone can't keep a pairing key in secure hardware." : e.getMessage()); }
  Map<String, Object> req = new LinkedHashMap<>();
  req.put("code", invite.code);
  req.put("publicKey", key.publicKeyBase64());
  req.put("name", clip("Rose App Builder on " + Build.MODEL));
  req.put("model", clip(Build.MODEL));
  byte[] body = Json.write(req).getBytes(StandardCharsets.UTF_8);
  boolean wrongIdentity = false;
  for (String host : invite.hosts) {
   HttpsURLConnection h = null;
   int code; String text;
   try {
    h = open(tls, host, invite.port, "/pair");
    h.setRequestMethod("POST");
    h.setDoOutput(true);
    h.setRequestProperty("Content-Type", "application/json");
    h.setFixedLengthStreamingMode(body.length);
    try (OutputStream out = h.getOutputStream()) { out.write(body); }
    code = h.getResponseCode();
    text = read(code < 400 ? h.getInputStream() : h.getErrorStream(), 64 * 1024);
   } catch (SSLHandshakeException e) { wrongIdentity = true; continue; }
   catch (IOException e) { continue; }
   finally { if (h != null) h.disconnect(); }
   if (code == 403) throw new Failure("That pairing code expired or was used. Make a new one on your Mac.");
   if (code == 400) throw new Failure("Your Rose builder couldn't read this phone's request. Update Rose on your Mac.");
   if (code != 200) throw new Failure("Your Rose builder couldn't pair right now (error " + code + "). Make a new code and try again.");
   Map<String, Object> r;
   try { r = BuilderWire.asObject(Json.parse(text)); } catch (Json.ParseError | BuilderWire.Malformed e) { throw new Failure("Your Rose builder's answer wasn't understood. Update Rose on your Mac."); }
   if (!key.deviceId().equals(r.get("deviceId")) || !invite.serverId.equals(r.get("serverId")))
    throw new Failure("Your Rose builder's answer didn't match this phone. Make a new code and try again.");
   String name = r.get("serverName") instanceof String ? ((String) r.get("serverName")).trim() : "";
   if (name.isEmpty() || name.length() > 128) name = invite.name;
   List<String> hosts = new ArrayList<>(invite.hosts);
   hosts.remove(host);
   hosts.add(0, host);
   Pairing p = new Pairing(name, invite.pin, key.deviceId(), hosts, invite.port);
   p.hello(c, tls, key, host);
   p.save(c);
   return p;
  }
  if (wrongIdentity) throw new Failure("The computer that answered isn't the Rose builder in this code. Make a new code on your Mac.");
  throw new Failure("Couldn't reach " + invite.name + ". Check that your Mac is on and connected to Tailscale, then try again.");
 }

 /** A signed GET /hello proves the backend accepts this key before the pairing is kept. */
 void hello(Context c, SSLSocketFactory tls, RoseLink.Key key, String host) throws Failure {
  HttpsURLConnection h = null;
  try {
   h = open(tls, host, port, "/hello");
   for (Map.Entry<String, String> e : key.headers("GET", "/hello", pin).entrySet()) h.setRequestProperty(e.getKey(), e.getValue());
   int code = h.getResponseCode();
   if (code == 401 || code == 403) throw new Failure("Your Rose builder didn't accept this phone's signature. Check the phone's date and time are automatic, then make a new code.");
  } catch (IOException | java.security.GeneralSecurityException e) {
   // Paired a moment ago at this address; requests retry by themselves.
  } finally { if (h != null) h.disconnect(); }
 }

 /** The pin is the backend's identity, so the certificate's hostname isn't checked; only Tailscale hosts are used. */
 static HttpsURLConnection open(SSLSocketFactory tls, String host, int port, String path) throws IOException {
  if (!RoseLink.usableHost(host)) throw new IOException("not a Tailscale address");
  HttpsURLConnection h = (HttpsURLConnection) new URL("https", host, port, path).openConnection();
  h.setSSLSocketFactory(tls);
  h.setHostnameVerifier((n, s) -> true);
  h.setConnectTimeout(5000);
  h.setReadTimeout(20000);
  h.setUseCaches(false);
  h.setInstanceFollowRedirects(false);
  return h;
 }

 static String read(InputStream in, int max) throws IOException {
  if (in == null) return "";
  try (InputStream i = in) {
   ByteArrayOutputStream b = new ByteArrayOutputStream();
   byte[] buf = new byte[8192];
   for (int n; (n = i.read(buf)) > 0; ) { b.write(buf, 0, n); if (b.size() > max) throw new IOException("answer too large"); }
   return new String(b.toByteArray(), StandardCharsets.UTF_8);
  }
 }

 static String clip(String s) { String t = s == null ? "" : s.trim(); return t.length() > 64 ? t.substring(0, 64) : t; }
 static List<String> list(String... s) { return new ArrayList<>(Arrays.asList(s)); }
}
