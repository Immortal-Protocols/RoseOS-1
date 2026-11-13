package dev.pixel.roseauth;

import java.io.UnsupportedEncodingException;
import java.net.URLDecoder;
import java.nio.ByteBuffer;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * rose-auth-v1 client core (docs/ROSE-AUTH-V1.md). Pure Java (no android.*), shared by the RoseOS-native flow and the
 * stock-Android flow of generated apps, and checked against the Rust server library through
 * workspace/rose-auth/vectors/rose-auth-v1.txt.
 *
 * <p>It parses and validates enrollment URIs, generates codes (for a device that acts as the authenticator, decision
 * D3) and applies the knowledge-factor length policy so users learn early. It never stores the PIN/passphrase: the
 * server verifies it (decision D1), so it travels only inside the sign-in request.
 */
public final class RoseAuthCore {
    private RoseAuthCore() {}

    /** The v1 profile: HMAC-SHA-1, 6 digits, 30 s steps, one step of skew. */
    public static final int DIGITS = 6;
    public static final long STEP_SECONDS = 30;
    public static final long SKEW_STEPS = 1;
    public static final int KNOWLEDGE_MIN = 8, KNOWLEDGE_MAX = 128;

    private static final String ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

    public static String base32Encode(byte[] data) {
        StringBuilder out = new StringBuilder((data.length + 4) / 5 * 8);
        int buffer = 0, bits = 0;
        for (byte b : data) {
            buffer = (buffer << 8) | (b & 0xff);
            bits += 8;
            while (bits >= 5) {
                bits -= 5;
                out.append(ALPHABET.charAt((buffer >> bits) & 31));
            }
        }
        if (bits > 0) out.append(ALPHABET.charAt((buffer << (5 - bits)) & 31));
        return out.toString();
    }

    /** Lower case, spaces and trailing '=' are accepted; null for anything else or non-zero trailing bits. */
    public static byte[] base32Decode(String text) {
        String t = text;
        while (t.endsWith("=")) t = t.substring(0, t.length() - 1);
        ByteBuffer out = ByteBuffer.allocate(t.length() * 5 / 8 + 1);
        int buffer = 0, bits = 0;
        for (int i = 0; i < t.length(); i++) {
            char c = Character.toUpperCase(t.charAt(i));
            if (c == ' ') continue;
            int v = ALPHABET.indexOf(c);
            if (v < 0) return null;
            buffer = (buffer << 5) | v;
            bits += 5;
            if (bits >= 8) {
                bits -= 8;
                out.put((byte) (buffer >> bits));
            }
        }
        if (bits >= 5 || (buffer & ((1 << bits) - 1)) != 0) return null;
        byte[] r = new byte[out.position()];
        out.flip();
        out.get(r);
        return r;
    }

    public static int hotp(byte[] secret, long counter, int digits) {
        try {
            Mac mac = Mac.getInstance("HmacSHA1");
            mac.init(new SecretKeySpec(secret.length == 0 ? new byte[1] : secret, "HmacSHA1"));
            byte[] h = mac.doFinal(ByteBuffer.allocate(8).putLong(counter).array());
            int o = h[19] & 0x0f;
            int binary = ((h[o] & 0x7f) << 24) | ((h[o + 1] & 0xff) << 16) | ((h[o + 2] & 0xff) << 8) | (h[o + 3] & 0xff);
            int mod = 1;
            for (int i = 0; i < digits; i++) mod *= 10;
            return binary % mod;
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HmacSHA1 is required on every Java and Android runtime", e);
        }
    }

    public static String format(int value, int digits) {
        String s = Integer.toString(value);
        StringBuilder b = new StringBuilder();
        for (int i = s.length(); i < digits; i++) b.append('0');
        return b.append(s).toString();
    }

    public static String totp(byte[] secret, long unixSeconds, int digits) {
        return format(hotp(secret, unixSeconds / STEP_SECONDS, digits), digits);
    }

    /**
     * The accepted step for code at unixSeconds, or -1. Only steps after lastUsedStep (-1 for none) are eligible; the
     * server is authoritative, this mirrors it for the vectors and for a device authenticator's own checks.
     */
    public static long verify(byte[] secret, String code, long unixSeconds, long lastUsedStep) {
        String c = code.trim();
        if (c.length() != DIGITS) return -1;
        for (int i = 0; i < c.length(); i++) if (c.charAt(i) < '0' || c.charAt(i) > '9') return -1;
        long now = unixSeconds / STEP_SECONDS, found = -1;
        for (long s = Math.max(0, now - SKEW_STEPS); s <= now + SKEW_STEPS; s++) {
            boolean match = MessageDigest.isEqual(format(hotp(secret, s, DIGITS), DIGITS).getBytes(), c.getBytes());
            if (match && s > lastUsedStep && found < 0) found = s;
        }
        return found;
    }

    /** Client-side length policy (the server is authoritative): null if acceptable, else a reason. */
    public static String knowledgeProblem(String passphrase) {
        int n = passphrase.codePointCount(0, passphrase.length());
        if (n < KNOWLEDGE_MIN) return "too short";
        if (n > KNOWLEDGE_MAX) return "too long";
        return null;
    }

    /** A parsed v1 enrollment URI. The secret is exposed only through {@link #secret()}. */
    public static final class Enrollment {
        public final String issuer, account;
        private final byte[] secret;

        Enrollment(String issuer, String account, byte[] secret) {
            this.issuer = issuer;
            this.account = account;
            this.secret = secret;
        }

        public byte[] secret() { return secret.clone(); }

        @Override public String toString() { return "Enrollment(" + issuer + ":" + account + ", secret=<redacted>)"; }
    }

    /**
     * Parses an otpauth://totp/ URI and accepts only the v1 profile (SHA1, 6 digits, 30 s), with a secret of at least
     * 128 bits and a label issuer matching the issuer parameter. Null for anything else: a URI asking for another
     * profile is refused rather than silently enrolled with different parameters.
     */
    public static Enrollment parseOtpAuth(String uri) {
        String prefix = "otpauth://totp/";
        if (uri == null || !uri.startsWith(prefix)) return null;
        int q = uri.indexOf('?');
        if (q < 0) return null;
        // Split the raw label at its separator (':' or %3A) before decoding, so an encoded name cannot move it; a name
        // that still contains ':' after decoding is refused (the server never issues one).
        String raw = uri.substring(prefix.length(), q);
        int sep = raw.indexOf(':'), enc = raw.toUpperCase(java.util.Locale.ROOT).indexOf("%3A");
        int at = sep < 0 ? enc : enc < 0 ? sep : Math.min(sep, enc);
        if (at <= 0) return null;
        String issuer = decode(raw.substring(0, at));
        String account = decode(raw.substring(at + (at == sep ? 1 : 3)));
        if (issuer == null || account == null || issuer.isEmpty() || account.isEmpty()
                || issuer.indexOf(':') >= 0 || account.indexOf(':') >= 0) return null;
        Map<String, String> p = new LinkedHashMap<>();
        for (String kv : uri.substring(q + 1).split("&")) {
            int e = kv.indexOf('=');
            if (e <= 0) return null;
            String v = decode(kv.substring(e + 1));
            if (v == null || p.put(kv.substring(0, e), v) != null) return null;
        }
        byte[] secret = p.get("secret") == null ? null : base32Decode(p.get("secret"));
        if (secret == null || secret.length < 16) return null;
        if (!issuer.equals(p.get("issuer"))) return null;
        if (!"SHA1".equals(p.getOrDefault("algorithm", "SHA1"))) return null;
        if (!Integer.toString(DIGITS).equals(p.getOrDefault("digits", "6"))) return null;
        if (!Long.toString(STEP_SECONDS).equals(p.getOrDefault("period", "30"))) return null;
        return new Enrollment(issuer, account, secret);
    }

    private static String decode(String s) {
        try {
            return URLDecoder.decode(s.replace("+", "%2B"), "UTF-8");
        } catch (UnsupportedEncodingException | IllegalArgumentException e) {
            return null;
        }
    }
}
