package dev.pixel.builder;

import android.content.Context;
import dev.pixel.voice.RoseServerLink;
import java.security.GeneralSecurityException;
import java.util.List;
import java.util.Map;
import javax.net.ssl.SSLSocketFactory;

/** The builder API over Rose Agent's own pairing with its one Rose server (RoseServerLink): no second pairing or key. */
final class AgentEndpoint implements HttpsBuilderApi.Endpoint {
 final RoseServerLink.Link link;
 private AgentEndpoint(RoseServerLink.Link link) { this.link = link; }

 /** The Agent's current pairing, or null while it isn't paired. */
 static AgentEndpoint current(Context c) {
  RoseServerLink.Link l = RoseServerLink.paired(c);
  return l == null ? null : new AgentEndpoint(l);
 }

 @Override public List<String> hosts() { return link.hosts; }
 @Override public int port() { return link.port; }
 @Override public SSLSocketFactory tls() throws GeneralSecurityException { return link.tls(); }
 @Override public Map<String, String> headers(String method, String path) throws GeneralSecurityException { return link.headers(method, path); }
}
