package rose.models.composer;

/** String helpers that count and trim exactly as the Python reference does (code points, Python whitespace). */
final class Text {
    private Text() {}

    /** Python {@code len(s)}: Unicode code points, not UTF-16 units. */
    static int len(String s) { return s.codePointCount(0, s.length()); }

    /** Python {@code str.isspace()} for one code point. */
    static boolean isSpace(int cp) {
        if (cp >= 0x09 && cp <= 0x0d) return true;
        if (cp >= 0x1c && cp <= 0x20) return true;
        if (cp == 0x85 || cp == 0xa0 || cp == 0x1680 || (cp >= 0x2000 && cp <= 0x200a)) return true;
        return cp == 0x2028 || cp == 0x2029 || cp == 0x202f || cp == 0x205f || cp == 0x3000;
    }

    /** Python {@code s.strip()}. */
    static String strip(String s) {
        int a = 0, b = s.length();
        while (a < b) {
            int cp = s.codePointAt(a);
            if (!isSpace(cp)) break;
            a += Character.charCount(cp);
        }
        while (b > a) {
            int cp = s.codePointBefore(b);
            if (!isSpace(cp)) break;
            b -= Character.charCount(cp);
        }
        return s.substring(a, b);
    }

    /** Any code point below 0x20 (Python {@code any(ord(ch) < 0x20 for ch in s)}). */
    static boolean hasControl(String s) {
        for (int k = 0; k < s.length(); k++) if (s.charAt(k) < 0x20) return true;
        return false;
    }
}
