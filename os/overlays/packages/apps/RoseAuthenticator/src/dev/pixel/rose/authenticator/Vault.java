package dev.pixel.rose.authenticator;

import dev.pixel.rosesignin.RoseSigninCore;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.List;

/**
 * The authenticator's accounts, pure Java (no android.*) so os/tests/rose-authenticator runs it on the JVM. Each
 * account's secret is held only sealed: a {@link Sealer} (the Android Keystore key on the phone) seals it with the
 * account's (issuer, account) as associated data, so a sealed secret moved to another entry does not open. The
 * vault's text form holds only names and sealed bytes.
 */
public final class Vault {
    /** Seals and opens secrets; on the phone an Android Keystore AES-GCM key usable only while unlocked. */
    public interface Sealer {
        byte[] seal(byte[] plaintext, byte[] associatedData) throws Exception;

        byte[] open(byte[] sealed, byte[] associatedData) throws Exception;
    }

    public static final class Entry {
        public final String issuer, account;
        final byte[] sealed;

        Entry(String issuer, String account, byte[] sealed) {
            this.issuer = issuer;
            this.account = account;
            this.sealed = sealed;
        }

        boolean same(String i, String a) {
            return issuer.equals(i) && account.equals(a);
        }

        @Override public String toString() { return "Entry(" + issuer + ":" + account + ")"; }
    }

    static final String HEADER = "rose-authenticator-v1";
    private final List<Entry> entries = new ArrayList<>();

    /** The associated data an entry's secret is sealed with: a label, then length-prefixed issuer and account. */
    static byte[] associatedData(String issuer, String account) {
        byte[] i = issuer.getBytes(StandardCharsets.UTF_8), a = account.getBytes(StandardCharsets.UTF_8);
        byte[] label = (HEADER + "\0").getBytes(StandardCharsets.UTF_8);
        return ByteBuffer.allocate(label.length + 8 + i.length + a.length)
                .put(label).putInt(i.length).put(i).putInt(a.length).put(a).array();
    }

    /** Longest issuer or account name kept, in UTF-16 units. */
    static final int MAX_NAME = 128;

    /**
     * Whether a link-supplied issuer or account name can be shown and stored as is: 1 to {@link #MAX_NAME} units of
     * well-formed UTF-16 with no control, format (bidi), line or paragraph separator characters. Anything else could
     * fake lines in a confirm dialog or change identity through the UTF-8 round trip of the vault file.
     */
    public static boolean displayable(String s) {
        if (s == null || s.isEmpty() || s.length() > MAX_NAME) return false;
        for (int i = 0; i < s.length(); ) {
            int cp = s.codePointAt(i);
            int type = Character.getType(cp);
            if (Character.isISOControl(cp) || type == Character.FORMAT || type == Character.SURROGATE
                    || type == Character.LINE_SEPARATOR || type == Character.PARAGRAPH_SEPARATOR) {
                return false;
            }
            i += Character.charCount(cp);
        }
        return true;
    }

    public List<Entry> entries() {
        return Collections.unmodifiableList(entries);
    }

    public Entry find(String issuer, String account) {
        for (Entry e : entries) if (e.same(issuer, account)) return e;
        return null;
    }

    /** Adds an enrollment, replacing an existing entry for the same issuer and account. */
    public Entry add(Sealer sealer, RoseSigninCore.Enrollment enrollment) throws Exception {
        if (!displayable(enrollment.issuer) || !displayable(enrollment.account)) {
            throw new IllegalArgumentException("issuer or account cannot be shown safely");
        }
        byte[] secret = enrollment.secret();
        try {
            Entry e = new Entry(enrollment.issuer, enrollment.account,
                    sealer.seal(secret, associatedData(enrollment.issuer, enrollment.account)));
            remove(e.issuer, e.account);
            entries.add(e);
            return e;
        } finally {
            Arrays.fill(secret, (byte) 0);
        }
    }

    public boolean remove(String issuer, String account) {
        return entries.removeIf(e -> e.same(issuer, account));
    }

    /** The entry's code at unixSeconds. The opened secret is wiped after use. */
    public String code(Sealer sealer, Entry e, long unixSeconds) throws Exception {
        byte[] secret = sealer.open(e.sealed, associatedData(e.issuer, e.account));
        try {
            return RoseSigninCore.totp(secret, unixSeconds, RoseSigninCore.DIGITS);
        } finally {
            Arrays.fill(secret, (byte) 0);
        }
    }

    /** Seconds until the current code changes. */
    public static long secondsLeft(long unixSeconds) {
        return RoseSigninCore.STEP_SECONDS - Math.floorMod(unixSeconds, RoseSigninCore.STEP_SECONDS);
    }

    /** One header line, then one line per entry: base64 issuer, account and sealed secret. */
    public String serialize() {
        StringBuilder b = new StringBuilder(HEADER).append('\n');
        Base64.Encoder enc = Base64.getEncoder();
        for (Entry e : entries) {
            b.append(enc.encodeToString(e.issuer.getBytes(StandardCharsets.UTF_8))).append(' ')
                    .append(enc.encodeToString(e.account.getBytes(StandardCharsets.UTF_8))).append(' ')
                    .append(enc.encodeToString(e.sealed)).append('\n');
        }
        return b.toString();
    }

    /** Parses {@link #serialize}'s form; an empty or missing file is an empty vault; anything else malformed throws. */
    public static Vault parse(String text) {
        Vault v = new Vault();
        if (text == null || text.isEmpty()) return v;
        String[] lines = text.split("\n");
        if (lines.length == 0 || !HEADER.equals(lines[0])) {
            throw new IllegalArgumentException("not a rose-authenticator-v1 vault");
        }
        Base64.Decoder dec = Base64.getDecoder();
        for (int i = 1; i < lines.length; i++) {
            if (lines[i].isEmpty()) continue;
            String[] f = lines[i].split(" ");
            if (f.length != 3) throw new IllegalArgumentException("malformed entry " + i);
            String issuer = new String(dec.decode(f[0]), StandardCharsets.UTF_8);
            String account = new String(dec.decode(f[1]), StandardCharsets.UTF_8);
            if (v.find(issuer, account) != null) throw new IllegalArgumentException("duplicate entry " + i);
            v.entries.add(new Entry(issuer, account, dec.decode(f[2])));
        }
        return v;
    }
}
