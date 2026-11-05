package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Rose's explicit phone-clipboard write (F07), as PhoneControl applies the "clipboard_write" phone command. A producer
 * (the shared Rose voice backend, on the Mac or in the cloud) sends the exact text the owner asked to copy plus its
 * SHA-256; the phone writes it only if the text it received is that exact text. This is write-only: nothing here reads
 * the clipboard except to confirm Rose's own write, and no result, receipt or log carries the text or a digest of it.
 * Pure rules, no Android types.
 */
final class ClipboardWrite {
 private ClipboardWrite() {}

 static final String ACTION = "clipboard_write";
 /** The same bound as the iPhone transfer (ClipboardSync): UTF-8 bytes of the text. */
 static final int MAX_BYTES = 65536;
 /** ClipDescription extra naming the command that wrote the clip, so a readback can tell Rose's write from another. */
 static final String MARKER = "dev.pixel.voice.clipboardWriteId";

 // Refusals: nothing was written.
 static final String EMPTY = "empty_text", TOO_LARGE = "text_too_large", MALFORMED = "malformed_text",
  MISMATCH = "text_mismatch", WRONG_DEVICE = "clipboard_wrong_device", DENIED = "clipboard_write_denied",
  UNAVAILABLE = "clipboard_unavailable";
 // Outcomes after the write call.
 static final String COPIED = "copied", FAILED = "clipboard_write_failed", REPLACED = "clipboard_replaced";
 /** How a "copied" was established: Rose read its own clip back, or (no read access without focus) the write was accepted. */
 static final String READBACK = "readback", ACCEPTED = "write_accepted";

 /** Whether every surrogate is paired: a lone surrogate cannot be the exact text in UTF-8 on both ends. */
 static boolean wellFormed(String text) {
  for (int i = 0; i < text.length(); i++) {
   char c = text.charAt(i);
   if (Character.isHighSurrogate(c)) { if (i + 1 >= text.length() || !Character.isLowSurrogate(text.charAt(i + 1))) return false; i++; }
   else if (Character.isLowSurrogate(c)) return false;
  }
  return true;
 }

 static String sha256(String text) {
  try {
   byte[] digest = MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
   StringBuilder hex = new StringBuilder();
   for (byte b : digest) hex.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
   return hex.toString();
  } catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
 }

 /**
  * Why this command must not write, or null when it may. text is exactly as received (never trimmed or normalized);
  * sha256 is the producer's digest of the owner's text, so a transport change of even one character refuses.
  */
 static String refusal(String text, String sha256) {
  if (text == null || text.isEmpty()) return EMPTY;
  if (!wellFormed(text)) return MALFORMED;
  if (text.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) return TOO_LARGE;
  if (sha256 == null || !sha256.matches("[0-9a-f]{64}") || !sha256.equals(sha256(text))) return MISMATCH;
  return null;
 }

 /**
  * Whether a readable primary clip is Rose's own write, judged by its description alone, before any of its content is
  * read: another app's clip is never read.
  */
 static boolean ours(String expectedMarker, String marker) { return expectedMarker.equals(marker); }

 /**
  * The outcome once setPrimaryClip returned without an exception. readable: the clipboard let Rose read the primary
  * clip's description (Android allows it only while Rose has focus or is the default IME; otherwise the write is
  * accepted unseen). A readable clip that is not Rose's marker means a later write replaced it; Rose's marker with any
  * other text is a failed write. Neither is ever "copied".
  */
 static String outcome(boolean readable, String expectedMarker, String marker, String expectedText, String readText) {
  if (!readable) return COPIED;
  if (!ours(expectedMarker, marker)) return REPLACED;
  return expectedText.equals(readText) ? COPIED : FAILED;
 }

 static String verification(boolean readable) { return readable ? READBACK : ACCEPTED; }
}
