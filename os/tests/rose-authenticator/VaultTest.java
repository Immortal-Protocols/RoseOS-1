package dev.pixel.rose.authenticator;

import dev.pixel.rosesignin.RoseSigninCore;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/** The authenticator's vault on the JVM: codes, sealing bound to the entry, replace/remove and its text form. */
public final class VaultTest {
    private static int checks, failures;

    private static void check(boolean ok, String what) {
        checks++;
        if (!ok) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    /** A software AES-GCM sealer standing in for the Keystore key (same construction as KeystoreSealer). */
    static final class SoftSealer implements Vault.Sealer {
        private final SecretKeySpec key = new SecretKeySpec(new byte[32], "AES");
        private int nonce;

        @Override public byte[] seal(byte[] p, byte[] aad) throws Exception {
            byte[] iv = new byte[12];
            iv[0] = (byte) ++nonce;
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(128, iv));
            c.updateAAD(aad);
            byte[] ct = c.doFinal(p);
            byte[] out = Arrays.copyOf(iv, 12 + ct.length);
            System.arraycopy(ct, 0, out, 12, ct.length);
            return out;
        }

        @Override public byte[] open(byte[] s, byte[] aad) throws Exception {
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, s, 0, 12));
            c.updateAAD(aad);
            return c.doFinal(s, 12, s.length - 12);
        }
    }

    public static void main(String[] args) throws Exception {
        SoftSealer sealer = new SoftSealer();
        // The rose-signin server's URI for the RFC 6238 secret (tests/vectors.rs in workspace/rose-signin).
        String uri = "otpauth://totp/Rose%20Notes:ana%40example.com?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=Rose%20Notes&algorithm=SHA1&digits=6&period=30";
        RoseSigninCore.Enrollment e = RoseSigninCore.parseOtpAuth(uri);
        Vault v = new Vault();
        Vault.Entry entry = v.add(sealer, e);
        // RFC 6238 Appendix B (SHA-1), 6 digits: what the server expects at these times.
        check(v.code(sealer, entry, 59).equals("287082"), "code at 59");
        check(v.code(sealer, entry, 1111111109).equals("081804"), "code at 1111111109");
        check(v.code(sealer, entry, 2000000000).equals("279037"), "code at 2000000000");
        check(Vault.secondsLeft(59) == 1 && Vault.secondsLeft(60) == 30, "seconds left");
        // The text form holds names and sealed bytes, never the secret, and round-trips.
        String text = v.serialize();
        check(!text.contains("GEZDGNBV") && !text.contains("12345678901234567890"), "no plaintext secret in the vault");
        Vault back = Vault.parse(text);
        check(back.entries().size() == 1 && back.code(sealer, back.entries().get(0), 59).equals("287082"), "round trip");
        // A sealed secret moved to another entry does not open (associated data binds issuer and account).
        String[] lines = text.split("\n");
        String forged = lines[0] + "\n" + java.util.Base64.getEncoder().encodeToString("Evil".getBytes(StandardCharsets.UTF_8))
                + " " + lines[1].split(" ")[1] + " " + lines[1].split(" ")[2] + "\n";
        Vault moved = Vault.parse(forged);
        boolean opened;
        try {
            moved.code(sealer, moved.entries().get(0), 59);
            opened = true;
        } catch (Exception expected) {
            opened = false;
        }
        check(!opened, "a sealed secret does not open under another issuer");
        // Replacing an account keeps one entry; removing drops it.
        v.add(sealer, e);
        check(v.entries().size() == 1, "same issuer and account replace");
        RoseSigninCore.Enrollment other = RoseSigninCore.parseOtpAuth(uri.replace("ana%40example.com", "bo"));
        v.add(sealer, other);
        check(v.entries().size() == 2 && v.find("Rose Notes", "bo") != null, "another account is added");
        check(v.remove("Rose Notes", "bo") && v.entries().size() == 1 && !v.remove("Rose Notes", "bo"), "remove");
        // Malformed or foreign files are refused, never half-read; empty is an empty vault.
        check(Vault.parse("").entries().isEmpty() && Vault.parse(null).entries().isEmpty(), "empty");
        for (String bad : new String[] {"not-a-vault\n", "\n", "\n\n\n", "\n" + Vault.HEADER + "\n", Vault.HEADER + "\nonly two\n", text + lines[1] + "\n"}) {
            boolean refused;
            try {
                Vault.parse(bad);
                refused = false;
            } catch (IllegalArgumentException expected) {
                refused = true;
            }
            check(refused, "refuses " + bad.replace("\n", "|"));
        }
        // Names a link supplies must be safe to show and to store: no control, bidi or separator characters, no lone
        // surrogate (it would turn into '?' in the UTF-8 file and so become a second account), and not too long.
        check(Vault.displayable("Example") && Vault.displayable("alice@example.com") && Vault.displayable("Ünïcode 名前 😀"),
                "ordinary names are shown");
        for (String bad : new String[] {"", "alice\n\nSent by Google", "a\rb", "a\u0000", "a\u202Eb", "a\u2028b",
                "a\u2029b", "A\uD800", "\uDC00x", "x".repeat(Vault.MAX_NAME + 1)}) {
            check(!Vault.displayable(bad), "refuses name " + bad.replace("\n", "|"));
        }
        boolean addRefused;
        try {
            new Vault().add(sealer, RoseSigninCore.parseOtpAuth(uri.replace("ana%40", "ana%0A%0ASent%20by%20Google%40")));
            addRefused = false;
        } catch (IllegalArgumentException expected) {
            addRefused = true;
        }
        check(addRefused, "the vault itself refuses a name with a newline");
        // Only the v1 profile enrolls (the shared core's rule).
        check(RoseSigninCore.parseOtpAuth(uri.replace("SHA1", "SHA256")) == null, "another algorithm is refused");
        System.out.println("VaultTest: " + (checks - failures) + "/" + checks + " checks passed");
        if (failures > 0) System.exit(1);
    }
}
