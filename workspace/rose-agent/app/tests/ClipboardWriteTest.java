package dev.pixel.voice;

/** F07: the rules of Rose's explicit phone-clipboard write (ClipboardWrite). Host test, no Android. */
public final class ClipboardWriteTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    static String repeat(char c, int n) { StringBuilder b = new StringBuilder(); for (int i = 0; i < n; i++) b.append(c); return b.toString(); }

    public static void main(String[] args) {
        expect("clipboard_write".equals(ClipboardWrite.ACTION), "the phone command's action");
        // The same digests the Python producer computes (rose/tests/test_codex.py, ClipboardContract): UTF-8 SHA-256.
        String[][] vectors = {
            {"hello", "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"},
            {"  Leading and trailing  \n", "64d2885b881530b8d94ee79bd99c8f8c088059959b92f74478da940971d4531c"},
            {"Café — 東京 👋🏽", "c2aee1934bea3aadedcd7e062ea6bd7d3da53fe178c5b476b17cb4749adb08fe"},
            {"line1\r\nline2\ttab", "9482e91e3d68743aa2719aaf71d6964cb7c22e25ae9b8ce61c6e86e298b5c3d9"},
        };
        for (String[] v : vectors) {
            expect(v[1].equals(ClipboardWrite.sha256(v[0])), "cross-language digest: " + v[0]);
            expect(ClipboardWrite.refusal(v[0], v[1]) == null, "exact text with its digest may be written: " + v[0]);
        }
        // Exact text: whitespace, line endings and Unicode are never trimmed or normalized, so any change refuses.
        expect(ClipboardWrite.MISMATCH.equals(ClipboardWrite.refusal("  Leading and trailing  ", vectors[1][1])), "a trimmed newline refuses");
        expect(ClipboardWrite.MISMATCH.equals(ClipboardWrite.refusal("Café — 東京 👋🏽", vectors[2][1])),
            "a decomposed é is not the owner's text");
        expect(ClipboardWrite.MISMATCH.equals(ClipboardWrite.refusal("line1\nline2\ttab", vectors[3][1])), "CRLF is kept");
        expect(ClipboardWrite.MISMATCH.equals(ClipboardWrite.refusal("hello", null)), "no digest, no write");
        expect(ClipboardWrite.MISMATCH.equals(ClipboardWrite.refusal("hello", vectors[0][1].toUpperCase())), "a digest is lowercase hex");
        expect(ClipboardWrite.MISMATCH.equals(ClipboardWrite.refusal("hello", "abc")), "a short digest refuses");
        // Bounds and well-formedness.
        expect(ClipboardWrite.EMPTY.equals(ClipboardWrite.refusal("", ClipboardWrite.sha256(""))), "nothing to copy");
        expect(ClipboardWrite.EMPTY.equals(ClipboardWrite.refusal(null, null)), "a missing text");
        String max = repeat('x', ClipboardWrite.MAX_BYTES);
        expect("1f8745f0d2d1387ec1af2211a3cf417b2e9e885e853472649c1d979d0e9370e3".equals(ClipboardWrite.sha256(max)), "the limit's digest");
        expect(ClipboardWrite.refusal(max, ClipboardWrite.sha256(max)) == null, "65536 UTF-8 bytes fit");
        String over = max + "x";
        expect(ClipboardWrite.TOO_LARGE.equals(ClipboardWrite.refusal(over, ClipboardWrite.sha256(over))), "one byte over refuses");
        String wide = repeat('é', ClipboardWrite.MAX_BYTES / 2 + 1);
        expect(ClipboardWrite.TOO_LARGE.equals(ClipboardWrite.refusal(wide, ClipboardWrite.sha256(wide))), "the bound counts UTF-8 bytes, not characters");
        for (String lone : new String[] {"a\ud83d", "\udc4bb", "\ud83d\ud83d"}) {
            expect(!ClipboardWrite.wellFormed(lone), "lone surrogate: " + lone.length());
            expect(ClipboardWrite.MALFORMED.equals(ClipboardWrite.refusal(lone, ClipboardWrite.sha256(lone))), "a lone surrogate refuses");
        }
        expect(ClipboardWrite.wellFormed("👋"), "a pair is well formed");
        // After setPrimaryClip returned.
        String id = "5b0f3c1e-8a7d-4f2b-9c6e-1d2a3b4c5d6e";
        expect(ClipboardWrite.COPIED.equals(ClipboardWrite.outcome(true, false, true, id, id, "hello", "hello")), "Rose's own clip, read back exactly");
        expect(ClipboardWrite.READBACK.equals(ClipboardWrite.verification(true)), "read back");
        expect(ClipboardWrite.COPIED.equals(ClipboardWrite.outcome(false, false, true, id, null, "hello", null)), "not readable without focus: the write was accepted");
        // ClipboardService silently drops a default-device write while one of Rose's activities is on a virtual display.
        expect(ClipboardWrite.UNCONFIRMED.equals(ClipboardWrite.outcome(false, false, false, id, null, "hello", null)), "unreadable and possibly dropped: unconfirmed");
        expect(ClipboardWrite.COPIED.equals(ClipboardWrite.outcome(true, false, false, id, id, "hello", "hello")), "a readback of Rose's own clip settles it anyway");
        expect(ClipboardWrite.REPLACED.equals(ClipboardWrite.outcome(true, false, false, id, "other", "hello", null)), "replaced whatever the displays");
        expect(ClipboardWrite.ACCEPTED.equals(ClipboardWrite.verification(false)), "accepted unseen is reported as such");
        expect(ClipboardWrite.REPLACED.equals(ClipboardWrite.outcome(true, false, true, id, "other", "hello", null)), "another write replaced it");
        expect(ClipboardWrite.REPLACED.equals(ClipboardWrite.outcome(true, false, true, id, null, "hello", null)), "an unmarked clip is another app's");
        expect(!ClipboardWrite.ours(id, null) && !ClipboardWrite.ours(id, "other") && ClipboardWrite.ours(id, id), "content is read only for Rose's marker");
        expect(ClipboardWrite.FAILED.equals(ClipboardWrite.outcome(true, false, true, id, id, "hello", "hell")), "Rose's marker with other text is a failure");
        expect(ClipboardWrite.FAILED.equals(ClipboardWrite.outcome(true, false, true, id, id, "hello", null)), "Rose's marker without text is a failure");
        // Focused, Rose may read: an empty clipboard after the write means it did not land, never "accepted unseen".
        expect(ClipboardWrite.FAILED.equals(ClipboardWrite.outcome(false, true, true, id, null, "hello", null)), "focused and empty: failed");
        expect(ClipboardWrite.FAILED.equals(ClipboardWrite.outcome(false, true, false, id, null, "hello", null)), "focused and empty off-display: failed");
        expect(ClipboardWrite.COPIED.equals(ClipboardWrite.outcome(true, true, true, id, id, "hello", "hello")), "focused readback");
        // The fetched clip's own marker decides: another app's clip fetched after Rose's description is replaced, not failed.
        expect(ClipboardWrite.REPLACED.equals(ClipboardWrite.outcome(true, true, true, id, "other-app", "hello", null)), "fetched a foreign clip");
        System.out.println("ClipboardWriteTest: " + checks + " checks passed");
    }
}
