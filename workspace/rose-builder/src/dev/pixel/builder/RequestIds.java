package dev.pixel.builder;

/**
 * Project 1, builder API v1 idempotency: one clientRequestId per request the owner makes. Retrying the same text after
 * a lost connection reuses its id, so the builder never starts a second build. Changed text is a new request with a
 * new id; a reused id would make the builder answer with the earlier request. Once the builder answers (accepted, or
 * refused before anything started, §3.6), the request is finished and the next one gets a new id. Pure, host-tested.
 */
final class RequestIds {
 interface Source { String next(); }

 private final Source source;
 private String id, text;

 RequestIds(Source source) { this.source = source; }

 /** The id to send with this text. */
 String forText(String t) {
  if (id == null || text == null || !text.equals(t)) { id = source.next(); text = t; }
  return id;
 }

 /** The builder answered: the next request is a new one. */
 void done() { id = null; text = null; }
}
