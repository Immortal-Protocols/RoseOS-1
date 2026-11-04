package dev.pixel.voice;

import java.io.UnsupportedEncodingException;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

/**
 * Rose remote link v1, the parts both ends must agree on byte for byte. Pure Java (no android.*)
 * so app/tests/LocalRoseAuthTest.java runs it on the JVM and cross-checks it against the Mac's Python.
 *
 * Pairing: the Mac shows a QR code holding
 *   roseagent://pair?v=1&name=NAME&hosts=H1,H2&port=8443&pin=64-HEX&code=26-BASE32
 * The pin is the SHA-256 of the Mac's TLS certificate, so it is the Mac's identity: the phone
 * trusts exactly that certificate and nothing else. The code is a one-time invite.
 *
 * Requests: every connection after pairing carries X-Rose-Device / -Time / -Nonce / -Signature,
 * an ECDSA P-256 signature (key in this phone's secure hardware) over {@link #canonical}.
 */
final class LocalRoseAuth {
    static final String SCHEME = "roseagent";
    static final String VERSION = "rose-auth-v1";
    static final int MAX_HOSTS = 8;
    static final long[] BACKOFF_MS = {500, 1000, 2000, 4000, 8000};
    /** The Mac refuses a signature more than 120 s off its clock; an HTTP Date is whole seconds, so allow a margin. */
    static final long CLOCK_SKEW_MS = 110000;

    /** What a 401/403 answer to a signed request means. */
    static final String REFUSED_REVOKED = "revoked", REFUSED_CLOCK = "clock", REFUSED_RETRY = "retry";

    static final String H_DEVICE = "X-Rose-Device";
    static final String H_TIME = "X-Rose-Time";
    static final String H_NONCE = "X-Rose-Nonce";
    static final String H_SIGNATURE = "X-Rose-Signature";

    private static final SecureRandom RANDOM = new SecureRandom();

    private LocalRoseAuth() {
    }

    /** Signs the canonical message: DER ECDSA (SHA256withECDSA). The phone's is the Keystore key. */
    interface Signer {
        byte[] sign(byte[] message) throws GeneralSecurityException;
    }

    /** A validated pairing invite from the QR code. */
    static final class Invite {
        final String name;
        final List<String> hosts;
        final int port;
        final String pin;
        final String code;

        Invite(String name, List<String> hosts, int port, String pin, String code) {
            this.name = name;
            this.hosts = Collections.unmodifiableList(new ArrayList<>(hosts));
            this.port = port;
            this.pin = pin;
            this.code = code;
        }

        /** The Mac's short id, as the Mac reports it in /pair and /hello. */
        String serverId() {
            return pin.substring(0, 16);
        }
    }

    /** Why a scanned code is not a usable invite; the message is shown to the user. */
    static final class InvalidInvite extends Exception {
        InvalidInvite(String message) {
            super(message);
        }
    }

    /**
     * Parses and validates a pair URI. Scheme and host are case-insensitive; the pin is
     * normalised to lowercase and the code to uppercase. Unknown parameters are ignored
     * (later versions may add some); repeated ones are refused.
     */
    static Invite parseInvite(String uri) throws InvalidInvite {
        if (uri == null) throw new InvalidInvite("That is not a Rose pairing code.");
        String s = uri.trim();
        String prefix = SCHEME + "://pair";
        if (s.length() < prefix.length() || !s.substring(0, prefix.length()).equalsIgnoreCase(prefix)) {
            throw new InvalidInvite("That is not a Rose pairing code.");
        }
        String rest = s.substring(prefix.length());
        if (rest.startsWith("/")) rest = rest.substring(1);
        if (!rest.startsWith("?")) throw new InvalidInvite("That is not a Rose pairing code.");
        int hash = rest.indexOf('#');
        String query = rest.substring(1, hash < 0 ? rest.length() : hash);

        Map<String, String> params = new HashMap<>();
        for (String part : query.split("&")) {
            if (part.isEmpty()) continue;
            int eq = part.indexOf('=');
            String key = decode(eq < 0 ? part : part.substring(0, eq));
            String value = eq < 0 ? "" : decode(part.substring(eq + 1));
            if (params.put(key, value) != null) throw new InvalidInvite("The pairing code repeats \"" + key + "\".");
        }

        if (!"1".equals(params.get("v"))) {
            throw new InvalidInvite("This pairing code is for a different version of Rose. Update Rose Agent and Rose on the backend.");
        }
        String name = params.get("name");
        if (name == null || name.trim().isEmpty() || name.length() > 128 || hasControl(name)) {
            throw new InvalidInvite("The pairing code has no valid backend name.");
        }
        String pin = lower(params.get("pin"));
        if (pin == null || !pin.matches("[0-9a-f]{64}")) throw new InvalidInvite("The pairing code has no valid backend identity.");
        String code = params.get("code");
        code = code == null ? null : code.toUpperCase(Locale.ROOT);
        if (code == null || !code.matches("[A-Z2-7]{26}")) throw new InvalidInvite("The pairing code has no valid one-time code.");
        int port;
        try {
            String p = params.get("port");
            if (p == null || !p.matches("[0-9]{1,5}")) throw new NumberFormatException();
            port = Integer.parseInt(p);
        } catch (NumberFormatException e) {
            port = 0;
        }
        if (port < 1 || port > 65535) throw new InvalidInvite("The pairing code has no valid port.");
        String hostList = params.get("hosts");
        if (hostList == null || hostList.isEmpty()) throw new InvalidInvite("The pairing code lists no address for the backend.");
        List<String> hosts = new ArrayList<>();
        for (String h : hostList.split(",", -1)) {
            String host = lower(h.trim());
            if (!validHost(host)) throw new InvalidInvite("The pairing code lists an address this phone can't use.");
            if (!hosts.contains(host)) hosts.add(host);
        }
        if (hosts.size() > MAX_HOSTS) throw new InvalidInvite("The pairing code lists too many addresses.");
        return new Invite(name.trim(), hosts, port, pin, code);
    }

    /** A DNS hostname (RFC 1123 labels) or a dotted-quad IPv4 address. No IPv6 in v1. */
    static boolean validHost(String host) {
        if (host == null || host.isEmpty() || host.length() > 253) return false;
        if (host.matches("[0-9.]+")) return ipv4(host) != null;
        for (String label : host.split("\\.", -1)) {
            if (!label.matches("[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")) return false;
        }
        return true;
    }

    /** The four octets of a dotted-quad IPv4 address, or null. */
    static int[] ipv4(String host) {
        if (host == null) return null;
        String[] parts = host.split("\\.", -1);
        if (parts.length != 4) return null;
        int[] out = new int[4];
        for (int i = 0; i < 4; i++) {
            if (!parts[i].matches("0|[1-9][0-9]{0,2}")) return null;
            out[i] = Integer.parseInt(parts[i]);
            if (out[i] > 255) return null;
        }
        return out;
    }

    /** How the phone reaches the backend, for the UI: "Tailscale", "Wi-Fi" or "USB". */
    static String route(String host) {
        String h = lower(host);
        if (h == null) return "Wi-Fi";
        int[] ip = ipv4(h);
        if (ip != null) {
            if (ip[0] == 127) return "USB";
            if (ip[0] == 100 && (ip[1] & 0xc0) == 64) return "Tailscale";  // 100.64.0.0/10
            return "Wi-Fi";
        }
        if (h.equals("localhost")) return "USB";
        if (h.endsWith(".ts.net")) return "Tailscale";
        return "Wi-Fi";
    }

    /**
     * The exact bytes the phone signs and the Mac verifies:
     * "rose-auth-v1\n" METHOD "\n" PATH "\n" TIME "\n" NONCE "\n" PIN, UTF-8.
     */
    static byte[] canonical(String method, String path, String time, String nonce, String pin) {
        for (String part : new String[]{method, path, time, nonce, pin}) {
            if (part == null || part.indexOf('\n') >= 0 || part.indexOf('\r') >= 0) {
                throw new IllegalArgumentException("canonical field missing or has a line break");
            }
        }
        String text = VERSION + "\n" + method + "\n" + path + "\n" + time + "\n" + nonce + "\n" + pin;
        return text.getBytes(StandardCharsets.UTF_8);
    }

    /** 16 random bytes as base64url without padding: 22 characters. */
    static String newNonce() {
        byte[] b = new byte[16];
        RANDOM.nextBytes(b);
        return nonce(b);
    }

    static String nonce(byte[] sixteen) {
        if (sixteen.length != 16) throw new IllegalArgumentException("nonce is 16 bytes");
        return Base64.getUrlEncoder().withoutPadding().encodeToString(sixteen);
    }

    /** The four request headers, in order, for one websocket upgrade or GET /hello. */
    static Map<String, String> headers(String deviceId, String method, String path, long timeMs,
                                       String nonce, String pin, Signer signer) throws GeneralSecurityException {
        String time = Long.toString(timeMs);
        byte[] signature = signer.sign(canonical(method, path, time, nonce, pin));
        Map<String, String> h = new LinkedHashMap<>();
        h.put(H_DEVICE, deviceId);
        h.put(H_TIME, time);
        h.put(H_NONCE, nonce);
        h.put(H_SIGNATURE, Base64.getEncoder().encodeToString(signature));
        return h;
    }

    /** True when the certificate's DER hashes to the pin (constant-time compare). */
    static boolean pinMatches(byte[] certificateDer, String pin) {
        if (certificateDer == null || pin == null || !pin.matches("[0-9a-f]{64}")) return false;
        byte[] want = unhex(pin);
        return MessageDigest.isEqual(sha256(certificateDer), want);
    }

    /** deviceId: lowercase hex SHA-256 of the public key's SPKI DER. */
    static String deviceId(byte[] spkiDer) {
        return hex(sha256(spkiDer));
    }

    /**
     * Classifies a refused signed request. Only a refusal that names this device as unknown or
     * removed (or gives no reason) is "revoked"; a clock difference (the X-Rose-Error reason, or
     * the response's Date header against this phone's clock) is "clock", and a replayed nonce,
     * a busy nonce cache or a garbled header is "retry". A bad signature means the Mac holds a
     * different key for this phone, so it counts as revoked. Any status other than 401/403 is "retry".
     */
    static String refusal(int status, String roseError, String date, long nowMs) {
        if (status != 401 && status != 403) return REFUSED_RETRY;
        long server = httpDate(date);
        if (server > 0 && Math.abs(nowMs - server) > CLOCK_SKEW_MS) return REFUSED_CLOCK;
        String why = lower(roseError);
        if (why == null || why.trim().isEmpty()) return REFUSED_REVOKED;
        if (why.contains("revoked") || why.contains("removed") || why.contains("unknown")) return REFUSED_REVOKED;
        if (why.contains("time") || why.contains("clock") || why.contains("skew")) return REFUSED_CLOCK;
        if (why.contains("nonce") || why.contains("replay") || why.contains("busy") || why.contains("full")
                || why.contains("malformed") || why.contains("header")) return REFUSED_RETRY;
        return REFUSED_REVOKED;
    }

    /** An RFC 7231 IMF-fixdate ("Sun, 06 Nov 1994 08:49:37 GMT") in unix ms, or 0. */
    static long httpDate(String date) {
        if (date == null || date.trim().isEmpty()) return 0;
        SimpleDateFormat f = new SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss zzz", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("GMT"));
        f.setLenient(false);
        try {
            return f.parse(date.trim()).getTime();
        } catch (ParseException e) {
            return 0;
        }
    }

    /** Retry delay after the given number of consecutive failures: 0.5, 1, 2, 4, then 8 s. */
    static long backoffMs(int failures) {
        return BACKOFF_MS[Math.max(0, Math.min(failures, BACKOFF_MS.length - 1))];
    }

    static byte[] sha256(byte[] data) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(data);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder(b.length * 2);
        for (byte x : b) s.append(Character.forDigit((x >> 4) & 0xf, 16)).append(Character.forDigit(x & 0xf, 16));
        return s.toString();
    }

    private static byte[] unhex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return out;
    }

    private static String decode(String s) throws InvalidInvite {
        try {
            return URLDecoder.decode(s, "UTF-8");
        } catch (IllegalArgumentException | UnsupportedEncodingException e) {
            throw new InvalidInvite("The pairing code is damaged.");
        }
    }

    private static String lower(String s) {
        return s == null ? null : s.toLowerCase(Locale.ROOT);
    }

    private static boolean hasControl(String s) {
        for (int i = 0; i < s.length(); i++) if (Character.isISOControl(s.charAt(i))) return true;
        return false;
    }
}
