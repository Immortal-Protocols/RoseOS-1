package dev.pixel.models;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON for the local-agent-v1 boundary, with no Android dependency so it runs in JVM tests.
 * parse() returns Map (objects), List (arrays), String, Long or Double, Boolean or null.
 */
final class Json {
    private final String s;
    private int i;

    private Json(String s) {
        this.s = s;
    }

    static Object parse(String text) {
        Json p = new Json(text);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.i != p.s.length()) {
            throw p.err("trailing data");
        }
        return v;
    }

    /** A JSON string literal for any Java string, including non-BMP text. */
    static String quote(String value) {
        StringBuilder out = new StringBuilder(value.length() + 2).append('"');
        for (int k = 0; k < value.length(); k++) {
            char c = value.charAt(k);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.append('"').toString();
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> object(Object v) {
        if (!(v instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object");
        }
        return (Map<String, Object>) v;
    }

    static String string(Map<String, Object> o, String key) {
        Object v = o.get(key);
        if (!(v instanceof String)) {
            throw new IllegalArgumentException("expected string field " + key);
        }
        return (String) v;
    }

    static long number(Map<String, Object> o, String key) {
        Object v = o.get(key);
        if (!(v instanceof Long)) {
            throw new IllegalArgumentException("expected integer field " + key);
        }
        return (Long) v;
    }

    private Object value() {
        if (i >= s.length()) {
            throw err("unexpected end");
        }
        char c = s.charAt(i);
        if (c == '{') {
            return obj();
        }
        if (c == '[') {
            return arr();
        }
        if (c == '"') {
            return str();
        }
        if (s.startsWith("true", i)) {
            i += 4;
            return Boolean.TRUE;
        }
        if (s.startsWith("false", i)) {
            i += 5;
            return Boolean.FALSE;
        }
        if (s.startsWith("null", i)) {
            i += 4;
            return null;
        }
        return num();
    }

    private Map<String, Object> obj() {
        Map<String, Object> m = new LinkedHashMap<>();
        i++;
        ws();
        if (peek('}')) {
            i++;
            return m;
        }
        while (true) {
            ws();
            String key = str();
            ws();
            expect(':');
            ws();
            m.put(key, value());
            ws();
            if (peek(',')) {
                i++;
                continue;
            }
            expect('}');
            return m;
        }
    }

    private List<Object> arr() {
        List<Object> l = new ArrayList<>();
        i++;
        ws();
        if (peek(']')) {
            i++;
            return l;
        }
        while (true) {
            ws();
            l.add(value());
            ws();
            if (peek(',')) {
                i++;
                continue;
            }
            expect(']');
            return l;
        }
    }

    private String str() {
        expect('"');
        StringBuilder out = new StringBuilder();
        while (true) {
            if (i >= s.length()) {
                throw err("unterminated string");
            }
            char c = s.charAt(i++);
            if (c == '"') {
                return out.toString();
            }
            if (c != '\\') {
                out.append(c);
                continue;
            }
            char e = s.charAt(i++);
            switch (e) {
                case '"': out.append('"'); break;
                case '\\': out.append('\\'); break;
                case '/': out.append('/'); break;
                case 'b': out.append('\b'); break;
                case 'f': out.append('\f'); break;
                case 'n': out.append('\n'); break;
                case 'r': out.append('\r'); break;
                case 't': out.append('\t'); break;
                case 'u':
                    out.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                    i += 4;
                    break;
                default:
                    throw err("bad escape");
            }
        }
    }

    private Object num() {
        int start = i;
        while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) {
            i++;
        }
        String n = s.substring(start, i);
        if (n.isEmpty()) {
            throw err("unexpected character");
        }
        return n.matches("-?\\d+") ? (Object) Long.parseLong(n) : (Object) Double.parseDouble(n);
    }

    private void ws() {
        while (i < s.length() && " \t\r\n".indexOf(s.charAt(i)) >= 0) {
            i++;
        }
    }

    private boolean peek(char c) {
        return i < s.length() && s.charAt(i) == c;
    }

    private void expect(char c) {
        if (!peek(c)) {
            throw err("expected '" + c + "'");
        }
        i++;
    }

    private IllegalArgumentException err(String what) {
        return new IllegalArgumentException("JSON " + what + " at " + i);
    }
}
