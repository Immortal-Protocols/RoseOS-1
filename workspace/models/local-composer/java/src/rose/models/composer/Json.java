package rose.models.composer;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A strict, bounded JSON reader with the same accept/reject behaviour as Python's {@code json.loads} under the
 * composer's settings: duplicate object keys, NaN/Infinity, control characters in strings, trailing text and
 * excessive nesting are all rejected. Objects keep their key order.
 *
 * <p>Values: {@code Map<String,Object>}, {@code List<Object>}, {@code String}, {@code Long} (integers that fit),
 * {@code BigInteger} (larger integers), {@code BigDecimal} (fractions/exponents), {@code Boolean}, and
 * {@link #NULL}. No dependency on Android or org.json, so the same code runs in the app and in host tests.
 */
public final class Json {
    /** JSON null. Distinct from a missing key, which reads as Java null. */
    public static final Object NULL = new Object() {
        @Override public String toString() { return "null"; }
    };

    static final int MAX_DEPTH = 512;

    /** Thrown for any malformed input; carries no input text. */
    public static final class ParseError extends Exception {
        private static final long serialVersionUID = 1L;

        ParseError() { super("malformed JSON"); }
    }

    private final String s;
    private int i;

    private Json(String s) { this.s = s; }

    public static Object parse(String text) throws ParseError {
        if (text == null) throw new ParseError();
        Json p = new Json(text);
        p.ws();
        Object v = p.value(0);
        p.ws();
        if (p.i != p.s.length()) throw new ParseError();
        return v;
    }

    private void ws() {
        while (i < s.length()) {
            char c = s.charAt(i);
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') i++; else break;
        }
    }

    private Object value(int depth) throws ParseError {
        if (depth > MAX_DEPTH || i >= s.length()) throw new ParseError();
        char c = s.charAt(i);
        switch (c) {
            case '{': return object(depth + 1);
            case '[': return array(depth + 1);
            case '"': return string();
            case 't': return literal("true", Boolean.TRUE);
            case 'f': return literal("false", Boolean.FALSE);
            case 'n': return literal("null", NULL);
            default:
                if (c == '-' || (c >= '0' && c <= '9')) return number();
                throw new ParseError();
        }
    }

    private Object literal(String word, Object v) throws ParseError {
        if (!s.startsWith(word, i)) throw new ParseError();
        i += word.length();
        return v;
    }

    private Map<String, Object> object(int depth) throws ParseError {
        i++;
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        ws();
        if (i < s.length() && s.charAt(i) == '}') { i++; return m; }
        while (true) {
            ws();
            if (i >= s.length() || s.charAt(i) != '"') throw new ParseError();
            String k = string();
            ws();
            if (i >= s.length() || s.charAt(i) != ':') throw new ParseError();
            i++;
            ws();
            Object v = value(depth);
            if (m.containsKey(k)) throw new ParseError();
            m.put(k, v);
            ws();
            if (i >= s.length()) throw new ParseError();
            char c = s.charAt(i++);
            if (c == '}') return m;
            if (c != ',') throw new ParseError();
        }
    }

    private List<Object> array(int depth) throws ParseError {
        i++;
        List<Object> a = new ArrayList<Object>();
        ws();
        if (i < s.length() && s.charAt(i) == ']') { i++; return a; }
        while (true) {
            ws();
            a.add(value(depth));
            ws();
            if (i >= s.length()) throw new ParseError();
            char c = s.charAt(i++);
            if (c == ']') return a;
            if (c != ',') throw new ParseError();
        }
    }

    private String string() throws ParseError {
        i++;
        StringBuilder b = new StringBuilder();
        while (true) {
            if (i >= s.length()) throw new ParseError();
            char c = s.charAt(i++);
            if (c == '"') return b.toString();
            if (c < 0x20) throw new ParseError();
            if (c != '\\') { b.append(c); continue; }
            if (i >= s.length()) throw new ParseError();
            char e = s.charAt(i++);
            switch (e) {
                case '"': b.append('"'); break;
                case '\\': b.append('\\'); break;
                case '/': b.append('/'); break;
                case 'b': b.append('\b'); break;
                case 'f': b.append('\f'); break;
                case 'n': b.append('\n'); break;
                case 'r': b.append('\r'); break;
                case 't': b.append('\t'); break;
                case 'u':
                    if (i + 4 > s.length()) throw new ParseError();
                    int cp = 0;
                    for (int k = 0; k < 4; k++) {
                        int d = Character.digit(s.charAt(i + k), 16);
                        if (d < 0) throw new ParseError();
                        cp = cp * 16 + d;
                    }
                    i += 4;
                    b.append((char) cp);
                    break;
                default: throw new ParseError();
            }
        }
    }

    private Object number() throws ParseError {
        int start = i;
        if (s.charAt(i) == '-') i++;
        if (i >= s.length()) throw new ParseError();
        if (s.charAt(i) == '0') {
            i++;
        } else if (s.charAt(i) >= '1' && s.charAt(i) <= '9') {
            while (i < s.length() && Character.isDigit(s.charAt(i)) && s.charAt(i) < 128) i++;
        } else {
            throw new ParseError();
        }
        boolean integral = true;
        if (i < s.length() && s.charAt(i) == '.') {
            integral = false;
            i++;
            int d = i;
            while (i < s.length() && s.charAt(i) >= '0' && s.charAt(i) <= '9') i++;
            if (i == d) throw new ParseError();
        }
        if (i < s.length() && (s.charAt(i) == 'e' || s.charAt(i) == 'E')) {
            integral = false;
            i++;
            if (i < s.length() && (s.charAt(i) == '+' || s.charAt(i) == '-')) i++;
            int d = i;
            while (i < s.length() && s.charAt(i) >= '0' && s.charAt(i) <= '9') i++;
            if (i == d) throw new ParseError();
        }
        String t = s.substring(start, i);
        if (!integral) return new BigDecimal(t);
        BigInteger n = new BigInteger(t);
        return n.bitLength() < 64 ? (Object) Long.valueOf(n.longValue()) : n;
    }

    // ---- small typed accessors shared by the composer ------------------------------------------------------

    static boolean isString(Object o) { return o instanceof String; }

    /** A JSON integer (never a boolean), as Python's {@code isinstance(v, int) and not isinstance(v, bool)}. */
    static boolean isInt(Object o) { return o instanceof Long || o instanceof BigInteger; }

    static boolean isNull(Object o) { return o == null || o == NULL; }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asMap(Object o) { return o instanceof Map ? (Map<String, Object>) o : null; }

    @SuppressWarnings("unchecked")
    static List<Object> asList(Object o) { return o instanceof List ? (List<Object>) o : null; }
}
