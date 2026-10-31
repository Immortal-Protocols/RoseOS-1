package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.Arrays;
import java.util.Base64;
import java.util.Map;

/**
 * LocalRoseAuth on the JVM. With no arguments: the checks below. The node wrapper
 * (tests/local-rose-auth.test.mjs) also uses two modes to cross-check against the Mac's Python:
 *   --sign METHOD PATH TIME NONCE PIN      prints spki=, signature=, canonical= (base64) for Python to verify
 *   --verify SPKI METHOD PATH TIME NONCE PIN SIGNATURE   exits 0 when a Python signature verifies here
 */
public final class LocalRoseAuthTest {
    static int checks;

    static void expect(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    static final String PIN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    static final String CODE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".substring(0, 20) + "234567";

    static String uri(String hosts, String extra) {
        return "roseagent://pair?v=1&name=Rose%20on%20Nathan%E2%80%99s%20MacBook&hosts=" + hosts
                + "&port=8443&pin=" + PIN + "&code=" + CODE + extra;
    }

    static void invalid(String uri, String why) {
        try {
            LocalRoseAuth.parseInvite(uri);
        } catch (LocalRoseAuth.InvalidInvite e) {
            expect(e.getMessage() != null && !e.getMessage().isEmpty(), why + ": has a reason");
            return;
        }
        throw new AssertionError("accepted " + why);
    }

    public static void main(String[] args) throws Exception {
        if (args.length > 0 && args[0].equals("--sign")) {
            sign(args);
            return;
        }
        if (args.length > 0 && args[0].equals("--verify")) {
            System.exit(verify(args) ? 0 : 1);
            return;
        }

        // ---- the QR invite ----
        LocalRoseAuth.Invite inv = LocalRoseAuth.parseInvite(uri("nathans-mbp.tail1234.ts.net,100.101.102.103,192.168.1.20", ""));
        expect(inv.name.equals("Rose on Nathan’s MacBook"), "name is percent-decoded UTF-8: " + inv.name);
        expect(inv.hosts.equals(Arrays.asList("nathans-mbp.tail1234.ts.net", "100.101.102.103", "192.168.1.20")), "hosts keep QR order");
        expect(inv.port == 8443 && inv.pin.equals(PIN) && inv.code.equals(CODE), "port, pin and code");
        expect(inv.serverId().equals(PIN.substring(0, 16)), "serverId is the first 16 hex of the pin");
        LocalRoseAuth.Invite upper = LocalRoseAuth.parseInvite(("ROSEAGENT://PAIR?v=1&name=Mac&hosts=10.0.0.2&port=8443&pin="
                + PIN.toUpperCase() + "&code=" + CODE.toLowerCase()));
        expect(upper.pin.equals(PIN) && upper.code.equals(CODE), "scheme/host case-insensitive; pin lowercased, code uppercased");
        expect(LocalRoseAuth.parseInvite(uri("10.0.0.2%2C10.0.0.3", "")).hosts.size() == 2, "an encoded comma separates hosts");
        expect(LocalRoseAuth.parseInvite("roseagent://pair?v=1&name=Rose+on+Mac&hosts=10.0.0.2&port=1&pin=" + PIN + "&code=" + CODE)
                .name.equals("Rose on Mac"), "form-encoded spaces decode too");
        expect(LocalRoseAuth.parseInvite(uri("10.0.0.2", "&future=1")).hosts.size() == 1, "unknown parameters are ignored");
        expect(LocalRoseAuth.parseInvite(uri("a,a,b", "")).hosts.size() == 2, "duplicate hosts collapse");
        expect(LocalRoseAuth.parseInvite(uri("1.2.3.4,1.2.3.5,1.2.3.6,1.2.3.7,1.2.3.8,1.2.3.9,1.2.3.10,1.2.3.11", "")).hosts.size() == 8,
                "eight hosts is the limit");

        invalid("https://pair?v=1", "another scheme");
        invalid("roseagent://other?v=1&name=x&hosts=a&port=1&pin=" + PIN + "&code=" + CODE, "another host");
        invalid("roseagent://pairing?v=1&name=x&hosts=a&port=1&pin=" + PIN + "&code=" + CODE, "a longer host");
        invalid(uri("a", "").replace("v=1", "v=2"), "version 2");
        invalid(uri("a", "").replace("v=1&", ""), "no version");
        invalid(uri("a", "").replace(PIN, PIN.substring(1)), "a 63-hex pin");
        invalid(uri("a", "").replace(PIN, PIN.replace('a', 'g')), "a non-hex pin");
        invalid(uri("a", "").replace(CODE, CODE.substring(1)), "a 25-char code");
        invalid(uri("a", "").replace(CODE, "0" + CODE.substring(1)), "a code with 0 (not base32)");
        invalid(uri("a", "").replace(CODE, "1" + CODE.substring(1)), "a code with 1 (not base32)");
        invalid(uri("a", "").replace("port=8443", "port=0"), "port 0");
        invalid(uri("a", "").replace("port=8443", "port=65536"), "port 65536");
        invalid(uri("a", "").replace("port=8443", "port=-1"), "a negative port");
        invalid(uri("a", "").replace("port=8443&", ""), "no port");
        invalid(uri("", ""), "no hosts");
        invalid(uri("a,", ""), "an empty host");
        invalid(uri("%5B::1%5D", ""), "IPv6");
        invalid(uri("256.1.1.1", ""), "a bad IPv4");
        invalid(uri("01.2.3.4", ""), "an IPv4 with a leading zero");
        invalid(uri("bad_host", ""), "an underscore host");
        invalid(uri("-a.b", ""), "a label starting with a hyphen");
        invalid(uri("1.2.3.4,1.2.3.5,1.2.3.6,1.2.3.7,1.2.3.8,1.2.3.9,1.2.3.10,1.2.3.11,1.2.3.12", ""), "nine hosts");
        invalid(uri("a", "&code=" + CODE), "a repeated parameter");
        invalid(uri("a", "").replace("name=Rose%20on%20Nathan%E2%80%99s%20MacBook", "name=%0A"), "a control character name");
        invalid(uri("a", "").replace("name=Rose%20on%20Nathan%E2%80%99s%20MacBook", "name="), "an empty name");
        invalid(uri("a", "").replace("name=Rose", "name=%ZZ"), "broken percent-encoding");
        invalid(null, "null");

        // ---- routes for the UI ----
        expect(LocalRoseAuth.route("nathans-mbp.tail1234.ts.net").equals("Tailscale"), "MagicDNS is Tailscale");
        expect(LocalRoseAuth.route("100.64.0.1").equals("Tailscale") && LocalRoseAuth.route("100.127.255.254").equals("Tailscale"), "100.64/10");
        expect(LocalRoseAuth.route("100.63.0.1").equals("Wi-Fi") && LocalRoseAuth.route("100.128.0.1").equals("Wi-Fi"), "outside 100.64/10");
        expect(LocalRoseAuth.route("192.168.1.20").equals("Wi-Fi") && LocalRoseAuth.route("macbook.local").equals("Wi-Fi"), "LAN is Wi-Fi");
        expect(LocalRoseAuth.route("127.0.0.1").equals("USB") && LocalRoseAuth.route("localhost").equals("USB"), "loopback is USB");

        // ---- the signed message ----
        String canonical = new String(LocalRoseAuth.canonical("GET", "/ws/audio", "1767225600000", "AAECAwQFBgcICQoLDA0ODw", PIN),
                StandardCharsets.UTF_8);
        expect(canonical.equals("rose-auth-v1\nGET\n/ws/audio\n1767225600000\nAAECAwQFBgcICQoLDA0ODw\n" + PIN), "canonical string");
        try {
            LocalRoseAuth.canonical("GET", "/x\nevil", "1", "n", PIN);
            throw new AssertionError("accepted a line break");
        } catch (IllegalArgumentException expected) {
            checks++;
        }
        byte[] counting = new byte[16];
        for (int i = 0; i < 16; i++) counting[i] = (byte) i;
        expect(LocalRoseAuth.nonce(counting).equals("AAECAwQFBgcICQoLDA0ODw"), "nonce vector: base64url, no padding");
        byte[] high = new byte[16];
        Arrays.fill(high, (byte) 0xfb);
        expect(LocalRoseAuth.nonce(high).equals("-_v7-_v7-_v7-_v7-_v7-w"), "nonce uses the URL-safe alphabet");
        for (int i = 0; i < 50; i++) {
            String n = LocalRoseAuth.newNonce();
            expect(n.matches("[A-Za-z0-9_-]{22}"), "random nonce shape: " + n);
        }

        // ---- pin and device id ----
        byte[] der = "not really a certificate".getBytes(StandardCharsets.US_ASCII);
        String derPin = LocalRoseAuth.hex(LocalRoseAuth.sha256(der));
        expect(LocalRoseAuth.pinMatches(der, derPin), "a certificate matches its pin");
        expect(!LocalRoseAuth.pinMatches(der, PIN), "and not another pin");
        expect(!LocalRoseAuth.pinMatches(der, derPin.toUpperCase()), "pins are lowercase hex");
        expect(!LocalRoseAuth.pinMatches(null, derPin) && !LocalRoseAuth.pinMatches(der, null), "missing inputs never match");
        expect(LocalRoseAuth.hex(LocalRoseAuth.sha256(new byte[0])).equals(
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"), "SHA-256 hex vector");

        // ---- backoff ----
        long[] want = {500, 1000, 2000, 4000, 8000, 8000, 8000};
        for (int i = 0; i < want.length; i++) expect(LocalRoseAuth.backoffMs(i) == want[i], "backoff " + i);

        // ---- refused requests: revoked vs clock vs retry ----
        long now = 784111777000L;  // Sun, 06 Nov 1994 08:49:37 GMT
        expect(LocalRoseAuth.httpDate("Sun, 06 Nov 1994 08:49:37 GMT") == now, "HTTP Date parses");
        expect(LocalRoseAuth.httpDate("yesterday") == 0 && LocalRoseAuth.httpDate(null) == 0, "a bad Date is ignored");
        expect(LocalRoseAuth.refusal(403, null, null, now).equals("revoked"), "a bare 403 is revoked");
        expect(LocalRoseAuth.refusal(401, "device revoked", null, now).equals("revoked"), "revoked reason");
        expect(LocalRoseAuth.refusal(403, "unknown device", null, now).equals("revoked"), "unknown device");
        expect(LocalRoseAuth.refusal(403, "bad signature", null, now).equals("revoked"), "bad signature: the Mac has another key");
        expect(LocalRoseAuth.refusal(403, "stale time", null, now).equals("clock"), "stale time reason");
        expect(LocalRoseAuth.refusal(403, null, "Sun, 06 Nov 1994 08:52:00 GMT", now).equals("clock"), "Date 143 s off");
        expect(LocalRoseAuth.refusal(403, null, "Sun, 06 Nov 1994 08:50:30 GMT", now).equals("revoked"), "Date 53 s off is not the clock");
        expect(LocalRoseAuth.refusal(403, "replayed nonce", null, now).equals("retry"), "replay is retryable");
        expect(LocalRoseAuth.refusal(403, "nonce cache full", null, now).equals("retry"), "full cache is retryable");
        expect(LocalRoseAuth.refusal(403, "missing or malformed auth headers", null, now).equals("retry"), "malformed is retryable");
        expect(LocalRoseAuth.refusal(409, null, null, now).equals("retry") && LocalRoseAuth.refusal(503, "x", null, now).equals("retry"),
                "other statuses are retryable");

        // ---- headers sign and verify with a P-256 key ----
        KeyPair kp = p256();
        String deviceId = LocalRoseAuth.deviceId(kp.getPublic().getEncoded());
        expect(deviceId.matches("[0-9a-f]{64}"), "deviceId is 64 lowercase hex");
        Map<String, String> h = LocalRoseAuth.headers(deviceId, "GET", "/ws/phone", 1767225600123L,
                "AAECAwQFBgcICQoLDA0ODw", PIN, m -> sign(kp, m));
        expect(Arrays.asList(h.keySet().toArray()).equals(Arrays.asList("X-Rose-Device", "X-Rose-Time", "X-Rose-Nonce", "X-Rose-Signature")),
                "header names and order");
        expect(h.get("X-Rose-Time").equals("1767225600123") && h.get("X-Rose-Device").equals(deviceId), "time and device headers");
        expect(h.get("X-Rose-Signature").matches("[A-Za-z0-9+/]+={0,2}"), "signature is standard base64");
        Signature v = Signature.getInstance("SHA256withECDSA");
        v.initVerify(kp.getPublic());
        v.update(LocalRoseAuth.canonical("GET", "/ws/phone", "1767225600123", "AAECAwQFBgcICQoLDA0ODw", PIN));
        expect(v.verify(Base64.getDecoder().decode(h.get("X-Rose-Signature"))), "the signature verifies over the canonical bytes");
        v.initVerify(kp.getPublic());
        v.update(LocalRoseAuth.canonical("GET", "/ws/audio", "1767225600123", "AAECAwQFBgcICQoLDA0ODw", PIN));
        expect(!v.verify(Base64.getDecoder().decode(h.get("X-Rose-Signature"))), "and not for another path");

        System.out.println("LocalRoseAuthTest: " + checks + " checks passed");
    }

    static KeyPair p256() throws Exception {
        KeyPairGenerator g = KeyPairGenerator.getInstance("EC");
        g.initialize(new ECGenParameterSpec("secp256r1"));
        return g.generateKeyPair();
    }

    static byte[] sign(KeyPair kp, byte[] message) throws java.security.GeneralSecurityException {
        Signature s = Signature.getInstance("SHA256withECDSA");
        s.initSign(kp.getPrivate());
        s.update(message);
        return s.sign();
    }

    /** Signs like the phone does, for Python to verify. */
    static void sign(String[] a) throws Exception {
        KeyPair kp = p256();
        Map<String, String> h = LocalRoseAuth.headers(LocalRoseAuth.deviceId(kp.getPublic().getEncoded()),
                a[1], a[2], Long.parseLong(a[3]), a[4], a[5], m -> sign(kp, m));
        Base64.Encoder b64 = Base64.getEncoder();
        System.out.println("spki=" + b64.encodeToString(kp.getPublic().getEncoded()));
        System.out.println("device=" + h.get("X-Rose-Device"));
        System.out.println("signature=" + h.get("X-Rose-Signature"));
        System.out.println("canonical=" + b64.encodeToString(LocalRoseAuth.canonical(a[1], a[2], a[3], a[4], a[5])));
    }

    /** Verifies a signature made elsewhere (the Mac's Python) over LocalRoseAuth's canonical bytes. */
    static boolean verify(String[] a) throws Exception {
        PublicKey key = KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(a[1])));
        Signature v = Signature.getInstance("SHA256withECDSA");
        v.initVerify(key);
        v.update(LocalRoseAuth.canonical(a[2], a[3], a[4], a[5], a[6]));
        return v.verify(Base64.getDecoder().decode(a[7]));
    }
}
