package dev.pixel.builder;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A small strict JSON reader and writer for the builder API (RFC 8259), with no Android types so the wire mapping is
 * host-tested. Objects are Maps, arrays Lists, numbers Doubles (or Longs when integral and exact), plus String,
 * Boolean and null. Nesting and input size are bounded; anything malformed is a ParseError, never a partial value.
 */
final class Json {
 private Json() {}

 static final int MAX_DEPTH = 32, MAX_CHARS = 4 * 1024 * 1024;

 static final class ParseError extends Exception { ParseError(String m) { super(m); } }

 static Object parse(String s) throws ParseError {
  if (s == null) throw new ParseError("empty");
  if (s.length() > MAX_CHARS) throw new ParseError("too large");
  Reader r = new Reader(s);
  r.ws();
  Object v = r.value(0);
  r.ws();
  if (r.i != s.length()) throw new ParseError("trailing data");
  return v;
 }

 private static final class Reader {
  final String s; int i;
  Reader(String s) { this.s = s; }

  void ws() { while (i < s.length() && " \t\r\n".indexOf(s.charAt(i)) >= 0) i++; }
  char peek() throws ParseError { if (i >= s.length()) throw new ParseError("unexpected end"); return s.charAt(i); }
  void expect(char c) throws ParseError { if (peek() != c) throw new ParseError("expected " + c); i++; }

  Object value(int depth) throws ParseError {
   if (depth > MAX_DEPTH) throw new ParseError("too deep");
   char c = peek();
   if (c == '{') return object(depth);
   if (c == '[') return array(depth);
   if (c == '"') return string();
   if (c == 't') { word("true"); return Boolean.TRUE; }
   if (c == 'f') { word("false"); return Boolean.FALSE; }
   if (c == 'n') { word("null"); return null; }
   return number();
  }

  void word(String w) throws ParseError {
   if (!s.startsWith(w, i)) throw new ParseError("bad literal");
   i += w.length();
  }

  Map<String, Object> object(int depth) throws ParseError {
   Map<String, Object> m = new LinkedHashMap<>();
   expect('{'); ws();
   if (peek() == '}') { i++; return m; }
   while (true) {
    ws();
    if (peek() != '"') throw new ParseError("expected key");
    String k = string();
    ws(); expect(':'); ws();
    if (m.containsKey(k)) throw new ParseError("duplicate key");
    m.put(k, value(depth + 1));
    ws();
    char c = peek(); i++;
    if (c == '}') return m;
    if (c != ',') throw new ParseError("expected , or }");
   }
  }

  List<Object> array(int depth) throws ParseError {
   List<Object> a = new ArrayList<>();
   expect('['); ws();
   if (peek() == ']') { i++; return a; }
   while (true) {
    ws(); a.add(value(depth + 1)); ws();
    char c = peek(); i++;
    if (c == ']') return a;
    if (c != ',') throw new ParseError("expected , or ]");
   }
  }

  String string() throws ParseError {
   expect('"');
   StringBuilder b = new StringBuilder();
   while (true) {
    char c = peek(); i++;
    if (c == '"') return b.toString();
    if (c < 0x20) throw new ParseError("control character in string");
    if (c != '\\') { b.append(c); continue; }
    char e = peek(); i++;
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
      if (i + 4 > s.length()) throw new ParseError("bad escape");
      try { b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); } catch (NumberFormatException x) { throw new ParseError("bad escape"); }
      i += 4; break;
     default: throw new ParseError("bad escape");
    }
   }
  }

  Object number() throws ParseError {
   int start = i;
   if (i < s.length() && s.charAt(i) == '-') i++;
   if (i >= s.length() || !Character.isDigit(s.charAt(i))) throw new ParseError("bad value");
   if (s.charAt(i) == '0') i++; else while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
   boolean integral = true;
   if (i < s.length() && s.charAt(i) == '.') {
    integral = false; i++;
    if (i >= s.length() || !Character.isDigit(s.charAt(i))) throw new ParseError("bad number");
    while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
   }
   if (i < s.length() && (s.charAt(i) == 'e' || s.charAt(i) == 'E')) {
    integral = false; i++;
    if (i < s.length() && (s.charAt(i) == '+' || s.charAt(i) == '-')) i++;
    if (i >= s.length() || !Character.isDigit(s.charAt(i))) throw new ParseError("bad number");
    while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
   }
   String t = s.substring(start, i);
   if (integral && t.length() <= 18) return Long.parseLong(t);
   double d = Double.parseDouble(t);
   if (Double.isInfinite(d) || Double.isNaN(d)) throw new ParseError("number out of range");
   return d;
  }
 }

 /** Writes a request body: Maps (insertion order), Lists, Strings, Numbers, Booleans and null only. */
 static String write(Object v) {
  StringBuilder b = new StringBuilder();
  write(b, v);
  return b.toString();
 }

 @SuppressWarnings("unchecked")
 private static void write(StringBuilder b, Object v) {
  if (v == null) b.append("null");
  else if (v instanceof String) quote(b, (String) v);
  else if (v instanceof Boolean || v instanceof Integer || v instanceof Long) b.append(v);
  else if (v instanceof Map) {
   b.append('{'); boolean first = true;
   for (Map.Entry<String, Object> e : ((Map<String, Object>) v).entrySet()) {
    if (!first) b.append(','); first = false;
    quote(b, e.getKey()); b.append(':'); write(b, e.getValue());
   }
   b.append('}');
  } else if (v instanceof List) {
   b.append('['); boolean first = true;
   for (Object o : (List<Object>) v) { if (!first) b.append(','); first = false; write(b, o); }
   b.append(']');
  } else throw new IllegalArgumentException("unsupported JSON value");
 }

 private static void quote(StringBuilder b, String s) {
  b.append('"');
  for (int i = 0; i < s.length(); i++) {
   char c = s.charAt(i);
   if (c == '"' || c == '\\') b.append('\\').append(c);
   else if (c == '\n') b.append("\\n");
   else if (c == '\r') b.append("\\r");
   else if (c == '\t') b.append("\\t");
   else if (c < 0x20 || c == 0x2028 || c == 0x2029) b.append(String.format("\\u%04x", (int) c));
   else b.append(c);
  }
  b.append('"');
 }
}
