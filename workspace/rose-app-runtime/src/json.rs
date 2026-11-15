//! Strict JSON (RFC 8259) for request and response bodies.
//!
//! A body parses only if it is one JSON value in UTF-8 with no repeated object key, nested at most
//! [`MAX_DEPTH`] deep, with every number finite. Integers that fit an `i64` stay integers. Output escapes every
//! control character and keeps object keys in the order given.

use std::fmt::Write as _;

pub const MAX_DEPTH: usize = 64;

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Arr(Vec<Value>),
    /// Keys in order, each once.
    Obj(Vec<(String, Value)>),
}

impl Value {
    /// The member named `key` of an object.
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Obj(members) => members.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }
    /// A member that must be a string when present: `Ok(None)` when absent or null, `Err` when another type.
    pub fn optional_str(&self, key: &str) -> Result<Option<&str>, Invalid> {
        match self.get(key) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::Str(s)) => Ok(Some(s)),
            Some(_) => Err(Invalid),
        }
    }
    pub fn obj<const N: usize>(members: [(&str, Value); N]) -> Value {
        Value::Obj(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }
    pub fn str(s: impl Into<String>) -> Value {
        Value::Str(s.into())
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Invalid;

pub fn parse(bytes: &[u8]) -> Result<Value, Invalid> {
    let text = std::str::from_utf8(bytes).map_err(|_| Invalid)?;
    let mut p = Parser {
        s: text.as_bytes(),
        i: 0,
    };
    p.ws();
    let v = p.value(0)?;
    p.ws();
    if p.i != p.s.len() {
        return Err(Invalid);
    }
    Ok(v)
}

struct Parser<'a> {
    s: &'a [u8],
    i: usize,
}

impl Parser<'_> {
    fn ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn eat(&mut self, lit: &[u8]) -> Result<(), Invalid> {
        if self.s[self.i..].starts_with(lit) {
            self.i += lit.len();
            Ok(())
        } else {
            Err(Invalid)
        }
    }

    fn value(&mut self, depth: usize) -> Result<Value, Invalid> {
        if depth > MAX_DEPTH {
            return Err(Invalid);
        }
        match self.s.get(self.i).copied().ok_or(Invalid)? {
            b'n' => self.eat(b"null").map(|_| Value::Null),
            b't' => self.eat(b"true").map(|_| Value::Bool(true)),
            b'f' => self.eat(b"false").map(|_| Value::Bool(false)),
            b'"' => self.string().map(Value::Str),
            b'[' => {
                self.i += 1;
                let mut items = Vec::new();
                self.ws();
                if self.s.get(self.i) == Some(&b']') {
                    self.i += 1;
                    return Ok(Value::Arr(items));
                }
                loop {
                    self.ws();
                    items.push(self.value(depth + 1)?);
                    self.ws();
                    match self.s.get(self.i) {
                        Some(b',') => self.i += 1,
                        Some(b']') => {
                            self.i += 1;
                            return Ok(Value::Arr(items));
                        }
                        _ => return Err(Invalid),
                    }
                }
            }
            b'{' => {
                self.i += 1;
                let mut members: Vec<(String, Value)> = Vec::new();
                self.ws();
                if self.s.get(self.i) == Some(&b'}') {
                    self.i += 1;
                    return Ok(Value::Obj(members));
                }
                loop {
                    self.ws();
                    if self.s.get(self.i) != Some(&b'"') {
                        return Err(Invalid);
                    }
                    let key = self.string()?;
                    if members.iter().any(|(k, _)| *k == key) {
                        return Err(Invalid);
                    }
                    self.ws();
                    self.eat(b":")?;
                    self.ws();
                    let v = self.value(depth + 1)?;
                    members.push((key, v));
                    self.ws();
                    match self.s.get(self.i) {
                        Some(b',') => self.i += 1,
                        Some(b'}') => {
                            self.i += 1;
                            return Ok(Value::Obj(members));
                        }
                        _ => return Err(Invalid),
                    }
                }
            }
            b'-' | b'0'..=b'9' => self.number(),
            _ => Err(Invalid),
        }
    }

    fn hex4(&mut self) -> Result<u32, Invalid> {
        let digits = self.s.get(self.i..self.i + 4).ok_or(Invalid)?;
        let text = std::str::from_utf8(digits).map_err(|_| Invalid)?;
        if !text.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(Invalid);
        }
        self.i += 4;
        u32::from_str_radix(text, 16).map_err(|_| Invalid)
    }

    fn string(&mut self) -> Result<String, Invalid> {
        self.i += 1; // the opening quote
        let mut out = String::new();
        loop {
            let start = self.i;
            while self.i < self.s.len() && self.s[self.i] != b'"' && self.s[self.i] != b'\\' && self.s[self.i] >= 0x20 {
                self.i += 1;
            }
            // The input is valid UTF-8 and the stops are ASCII, so this slice is too.
            out.push_str(std::str::from_utf8(&self.s[start..self.i]).map_err(|_| Invalid)?);
            match self.s.get(self.i).copied() {
                Some(b'"') => {
                    self.i += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.i += 1;
                    let c = self.s.get(self.i).copied().ok_or(Invalid)?;
                    self.i += 1;
                    match c {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let first = self.hex4()?;
                            let code = if (0xd800..0xdc00).contains(&first) {
                                self.eat(b"\\u")?;
                                let second = self.hex4()?;
                                if !(0xdc00..0xe000).contains(&second) {
                                    return Err(Invalid);
                                }
                                0x10000 + ((first - 0xd800) << 10) + (second - 0xdc00)
                            } else if (0xdc00..0xe000).contains(&first) {
                                return Err(Invalid); // a lone low surrogate
                            } else {
                                first
                            };
                            out.push(char::from_u32(code).ok_or(Invalid)?);
                        }
                        _ => return Err(Invalid),
                    }
                }
                _ => return Err(Invalid), // end of input or a raw control character
            }
        }
    }

    fn number(&mut self) -> Result<Value, Invalid> {
        let start = self.i;
        if self.s[self.i] == b'-' {
            self.i += 1;
        }
        match self.s.get(self.i) {
            Some(b'0') => self.i += 1,
            Some(b'1'..=b'9') => {
                while matches!(self.s.get(self.i), Some(b'0'..=b'9')) {
                    self.i += 1;
                }
            }
            _ => return Err(Invalid),
        }
        let mut integer = true;
        if self.s.get(self.i) == Some(&b'.') {
            integer = false;
            self.i += 1;
            if !matches!(self.s.get(self.i), Some(b'0'..=b'9')) {
                return Err(Invalid);
            }
            while matches!(self.s.get(self.i), Some(b'0'..=b'9')) {
                self.i += 1;
            }
        }
        if matches!(self.s.get(self.i), Some(b'e' | b'E')) {
            integer = false;
            self.i += 1;
            if matches!(self.s.get(self.i), Some(b'+' | b'-')) {
                self.i += 1;
            }
            if !matches!(self.s.get(self.i), Some(b'0'..=b'9')) {
                return Err(Invalid);
            }
            while matches!(self.s.get(self.i), Some(b'0'..=b'9')) {
                self.i += 1;
            }
        }
        let text = std::str::from_utf8(&self.s[start..self.i]).map_err(|_| Invalid)?;
        if integer {
            if let Ok(n) = text.parse::<i64>() {
                return Ok(Value::Int(n));
            }
        }
        let f: f64 = text.parse().map_err(|_| Invalid)?;
        if f.is_finite() {
            Ok(Value::Float(f))
        } else {
            Err(Invalid) // 1e400 and the like
        }
    }
}

/// The value as compact JSON; None when it holds a number JSON cannot carry (NaN or an infinity).
pub fn to_string(v: &Value) -> Option<String> {
    let mut out = String::new();
    write(v, &mut out)?;
    Some(out)
}

fn write(v: &Value, out: &mut String) -> Option<()> {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Int(n) => write!(out, "{n}").ok()?,
        Value::Float(f) => {
            if !f.is_finite() {
                return None;
            }
            // Debug is the shortest form that reads back as the same f64 (1.0, 1e300, 0.1).
            write!(out, "{f:?}").ok()?
        }
        Value::Str(s) => quote(s, out),
        Value::Arr(items) => {
            out.push('[');
            for (n, item) in items.iter().enumerate() {
                if n > 0 {
                    out.push(',');
                }
                write(item, out)?;
            }
            out.push(']');
        }
        Value::Obj(members) => {
            out.push('{');
            for (n, (k, item)) in members.iter().enumerate() {
                if n > 0 {
                    out.push(',');
                }
                quote(k, out);
                out.push(':');
                write(item, out)?;
            }
            out.push('}');
        }
    }
    Some(())
}

fn quote(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c == '\u{7f}' || c == '\u{2028}' || c == '\u{2029}' => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn values_round_trip() {
        let text = r#"{"a":[1,-2,0.5,1e300,true,false,null],"b":"x\"\\\n\u0001\u00e9😀","c":{}}"#;
        let v = parse(text.as_bytes()).unwrap();
        assert_eq!(
            v.get("a").unwrap(),
            &Value::Arr(vec![
                Value::Int(1),
                Value::Int(-2),
                Value::Float(0.5),
                Value::Float(1e300),
                Value::Bool(true),
                Value::Bool(false),
                Value::Null
            ])
        );
        assert_eq!(v.get("b").unwrap().as_str(), Some("x\"\\\n\u{1}é😀"));
        let again = parse(to_string(&v).unwrap().as_bytes()).unwrap();
        assert_eq!(again, v);
        assert_eq!(parse(br#""\ud83d\ude00""#).unwrap(), Value::str("😀"));
        assert_eq!(parse(b" 7 ").unwrap(), Value::Int(7));
        assert_eq!(
            parse(b"9223372036854775808").unwrap(),
            Value::Float(9223372036854775808.0)
        );
    }

    #[test]
    fn invalid_json_is_refused() {
        let deep = "[".repeat(MAX_DEPTH + 2) + &"]".repeat(MAX_DEPTH + 2);
        let cases: Vec<&[u8]> = vec![
            b"",
            b" ",
            b"{",
            b"[1,]",
            b"{\"a\":1,}",
            b"{\"a\":1,\"a\":2}",
            b"01",
            b"-",
            b"1.",
            b".5",
            b"1e",
            b"+1",
            b"1e400",
            b"NaN",
            b"Infinity",
            b"\"\\x\"",
            b"\"\\ud800\"",
            b"\"\\udc00\"",
            b"\"\\ud800\\u0041\"",
            b"\"a\nb\"",
            b"\"\xff\"",
            b"tru",
            b"nul",
            b"1 2",
            b"{1:2}",
            b"[1 2]",
            b"\"\\u12\"",
            b"\"\\u12g4\"",
            b"\"abc",
            deep.as_bytes(),
        ];
        for raw in cases {
            assert_eq!(parse(raw), Err(Invalid), "{:?}", String::from_utf8_lossy(raw));
        }
        let ok = "[".repeat(MAX_DEPTH) + &"]".repeat(MAX_DEPTH);
        assert!(parse(ok.as_bytes()).is_ok());
    }

    #[test]
    fn output_escapes_controls_and_refuses_non_finite() {
        assert_eq!(
            to_string(&Value::str("a\u{0}\u{1f}\u{7f}\u{2028}")).unwrap(),
            r#""a\u0000\u001f\u007f\u2028""#
        );
        assert_eq!(to_string(&Value::Float(f64::NAN)), None);
        assert_eq!(to_string(&Value::Arr(vec![Value::Float(f64::INFINITY)])), None);
        assert_eq!(to_string(&Value::Float(1.0)).unwrap(), "1.0");
        assert_eq!(to_string(&Value::obj([("k", Value::Int(1))])).unwrap(), r#"{"k":1}"#);
    }

    #[test]
    fn optional_members() {
        let v = parse(br#"{"a":"x","b":null,"c":1}"#).unwrap();
        assert_eq!(v.optional_str("a"), Ok(Some("x")));
        assert_eq!(v.optional_str("b"), Ok(None));
        assert_eq!(v.optional_str("missing"), Ok(None));
        assert_eq!(v.optional_str("c"), Err(Invalid));
    }
}
