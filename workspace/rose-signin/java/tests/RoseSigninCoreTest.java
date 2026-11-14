package dev.pixel.rosesignin;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/** Every line of vectors/rose-signin-v1.txt (shared with the Rust server library), plus the URI and policy rules. */
public final class RoseSigninCoreTest {
    private static int checks, failures;

    private static void check(boolean ok, String what) {
        checks++;
        if (!ok) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    private static byte[] hex(String s) {
        if (s.equals("-")) return new byte[0];
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return b;
    }

    public static void main(String[] args) throws Exception {
        int vectors = 0;
        for (String line : Files.readAllLines(Paths.get(args[0]), StandardCharsets.UTF_8)) {
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] f = line.split(" ");
            vectors++;
            switch (f[0]) {
                case "hotp":
                    check(RoseSigninCore.format(RoseSigninCore.hotp(hex(f[1]), Long.parseLong(f[2]), Integer.parseInt(f[3])), Integer.parseInt(f[3])).equals(f[4]), line);
                    break;
                case "totp":
                    check(RoseSigninCore.totp(hex(f[1]), Long.parseLong(f[2]), Integer.parseInt(f[3])).equals(f[4]), line);
                    break;
                case "verify":
                    long last = f[4].equals("-") ? -1 : Long.parseLong(f[4]);
                    long want = f[6].equals("-") ? -1 : Long.parseLong(f[6]);
                    check(RoseSigninCore.verify(hex(f[1]), f[3], Long.parseLong(f[2]), last) == want, line);
                    break;
                case "base32":
                    String b32 = f[2].equals("-") ? "" : f[2];
                    check(RoseSigninCore.base32Encode(hex(f[1])).equals(b32), line);
                    check(java.util.Arrays.equals(RoseSigninCore.base32Decode(b32), hex(f[1])), line + " (decode)");
                    break;
                default:
                    check(false, "unknown vector kind " + line);
            }
        }
        check(vectors == 43, "every vector line is checked (" + vectors + ")");
        // The URI the Rust library emits (tests/vectors.rs) is accepted, and only the v1 profile is.
        String uri = "otpauth://totp/Rose%20Notes:ana%40example.com?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=Rose%20Notes&algorithm=SHA1&digits=6&period=30";
        RoseSigninCore.Enrollment e = RoseSigninCore.parseOtpAuth(uri);
        check(e != null && e.issuer.equals("Rose Notes") && e.account.equals("ana@example.com")
                && new String(e.secret(), StandardCharsets.US_ASCII).equals("12345678901234567890"), "parses the server's URI");
        check(e != null && !e.toString().contains("GEZD") && e.toString().contains("<redacted>"), "toString never shows the secret");
        check(RoseSigninCore.parseOtpAuth(uri.replace("SHA1", "SHA256")) == null, "another algorithm is refused");
        check(RoseSigninCore.parseOtpAuth(uri.replace("digits=6", "digits=8")) == null, "other digits are refused");
        check(RoseSigninCore.parseOtpAuth(uri.replace("period=30", "period=60")) == null, "another period is refused");
        check(RoseSigninCore.parseOtpAuth(uri.replace("issuer=Rose%20Notes", "issuer=Evil")) == null, "issuer mismatch is refused");
        check(RoseSigninCore.parseOtpAuth(uri.replace("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "GEZDGNBVGY3TQOJQ")) == null, "short secrets are refused");
        check(RoseSigninCore.parseOtpAuth(uri + "&secret=AAAA") == null, "duplicate parameters are refused");
        check(RoseSigninCore.parseOtpAuth(uri.replace("totp", "hotp")) == null, "counter-based enrollment is refused");
        RoseSigninCore.Enrollment sep = RoseSigninCore.parseOtpAuth(uri.replace("Rose%20Notes:ana", "Rose%20Notes%3Aana"));
        check(sep != null && sep.issuer.equals("Rose Notes") && sep.account.equals("ana@example.com"), "an encoded separator is accepted");
        check(RoseSigninCore.parseOtpAuth(uri.replace("Rose%20Notes:ana%40example.com", "Rose%3A%20Notes:bob").replace("issuer=Rose%20Notes", "issuer=Rose%3A%20Notes")) == null,
                "an encoded ':' inside a name is refused, never re-split");
        check(RoseSigninCore.parseOtpAuth(uri.replace("ana%40example.com", "ana%3Aadmin")) == null, "':' in the account is refused");
        check(RoseSigninCore.knowledgeProblem("1234567") != null && RoseSigninCore.knowledgeProblem("12345678") == null, "length policy");
        check(RoseSigninCore.base32Decode("MZXW6YTBO1") == null, "invalid base32");
        System.out.println("RoseSigninCoreTest: " + (checks - failures) + "/" + checks + " checks passed");
        if (failures > 0) System.exit(1);
    }
}
