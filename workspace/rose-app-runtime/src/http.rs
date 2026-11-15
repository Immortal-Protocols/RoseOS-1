//! One bounded HTTP/1.1 request per connection.
//!
//! The reader accepts exactly what a generated client sends: a request line, headers, and a body only with a
//! `Content-Length`. It refuses `Transfer-Encoding` (so no chunked body, and no way to disagree with another
//! parser about where a request ends), a repeated or malformed `Content-Length`, folded or malformed headers, bare
//! line feeds, and anything over its limits. Every response closes the connection.

use std::io::{self, BufRead, Write};

/// What one request may use.
#[derive(Clone, Copy, Debug)]
pub struct Limits {
    /// Bytes of the request line and headers together.
    pub max_head: usize,
    pub max_headers: usize,
    pub max_body: usize,
}

pub const LIMITS: Limits = Limits {
    max_head: 16 * 1024,
    max_headers: 64,
    max_body: 1024 * 1024,
};

/// A request. Its `Debug` shows the method, the target and the header names only: a header value or the body can
/// hold a token, a passphrase or a code.
#[derive(PartialEq, Eq)]
pub struct Request {
    pub method: String,
    /// The request target as sent: origin form, starting with `/`.
    pub target: String,
    /// Header names in lower case, in the order sent.
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl std::fmt::Debug for Request {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let names: Vec<&str> = self.headers.iter().map(|(n, _)| n.as_str()).collect();
        write!(
            f,
            "Request({} {} headers={names:?} body={} bytes)",
            self.method,
            self.target,
            self.body.len()
        )
    }
}

impl Request {
    /// The only value of a header; None when it is absent or sent more than once.
    pub fn header(&self, name: &str) -> Option<&str> {
        let mut found = self.headers.iter().filter(|(n, _)| n == name);
        match (found.next(), found.next()) {
            (Some((_, v)), None) => Some(v),
            _ => None,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ParseError {
    /// The peer closed the connection before sending a request.
    Closed,
    /// Malformed: 400.
    Bad,
    /// Head over its limit: 431.
    HeadTooLarge,
    /// Body over its limit: 413.
    BodyTooLarge,
    /// A transfer coding: 501.
    Unsupported,
    /// The socket failed or timed out.
    Io,
}

impl ParseError {
    /// The status to answer with, if any (none when the connection is already gone).
    pub fn status(&self) -> Option<u16> {
        match self {
            ParseError::Closed | ParseError::Io => None,
            ParseError::Bad => Some(400),
            ParseError::HeadTooLarge => Some(431),
            ParseError::BodyTooLarge => Some(413),
            ParseError::Unsupported => Some(501),
        }
    }
}

fn is_token(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&b)
}

/// One CRLF-terminated line, within `budget` bytes (which it spends). A bare LF is malformed.
fn read_line<R: BufRead>(r: &mut R, budget: &mut usize) -> Result<Option<Vec<u8>>, ParseError> {
    let mut line = Vec::new();
    loop {
        let available = match r.fill_buf() {
            Ok(b) => b,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(ParseError::Io),
        };
        if available.is_empty() {
            return if line.is_empty() {
                Ok(None)
            } else {
                Err(ParseError::Bad)
            };
        }
        let (chunk, done) = match available.iter().position(|&b| b == b'\n') {
            Some(i) => (&available[..=i], true),
            None => (available, false),
        };
        if chunk.len() > *budget {
            return Err(ParseError::HeadTooLarge);
        }
        *budget -= chunk.len();
        line.extend_from_slice(chunk);
        let used = chunk.len();
        r.consume(used);
        if done {
            if line.len() < 2 || line[line.len() - 2] != b'\r' {
                return Err(ParseError::Bad);
            }
            line.truncate(line.len() - 2);
            return Ok(Some(line));
        }
    }
}

/// Reads one request, or says why not.
pub fn read_request<R: BufRead>(r: &mut R, limits: Limits) -> Result<Request, ParseError> {
    let mut budget = limits.max_head;
    let line = match read_line(r, &mut budget)? {
        Some(l) => l,
        None => return Err(ParseError::Closed),
    };
    let text = std::str::from_utf8(&line).map_err(|_| ParseError::Bad)?;
    let mut parts = text.split(' ');
    let (method, target, version) = match (parts.next(), parts.next(), parts.next(), parts.next()) {
        (Some(m), Some(t), Some(v), None) => (m, t, v),
        _ => return Err(ParseError::Bad),
    };
    if method.is_empty() || !method.bytes().all(|b| b.is_ascii_uppercase()) {
        return Err(ParseError::Bad);
    }
    if !target.starts_with('/') || !target.bytes().all(|b| (0x21..0x7f).contains(&b)) {
        return Err(ParseError::Bad);
    }
    if version != "HTTP/1.1" && version != "HTTP/1.0" {
        return Err(ParseError::Bad);
    }
    let mut headers = Vec::new();
    loop {
        let line = read_line(r, &mut budget)?.ok_or(ParseError::Bad)?;
        if line.is_empty() {
            break;
        }
        if headers.len() == limits.max_headers {
            return Err(ParseError::HeadTooLarge);
        }
        let colon = line.iter().position(|&b| b == b':').ok_or(ParseError::Bad)?;
        let (name, value) = (&line[..colon], &line[colon + 1..]);
        if name.is_empty() || !name.iter().all(|&b| is_token(b)) {
            return Err(ParseError::Bad); // also a folded line, which starts with a space
        }
        if value.iter().any(|&b| (b < 0x20 && b != b'\t') || b == 0x7f) {
            return Err(ParseError::Bad);
        }
        let value = std::str::from_utf8(value).map_err(|_| ParseError::Bad)?;
        let name = std::str::from_utf8(name).map_err(|_| ParseError::Bad)?;
        headers.push((
            name.to_ascii_lowercase(),
            value.trim_matches(|c| c == ' ' || c == '\t').to_string(),
        ));
    }
    if headers.iter().any(|(n, _)| n == "transfer-encoding") {
        return Err(ParseError::Unsupported);
    }
    let lengths: Vec<&str> = headers
        .iter()
        .filter(|(n, _)| n == "content-length")
        .map(|(_, v)| v.as_str())
        .collect();
    let length = match lengths.as_slice() {
        [] => 0,
        [one] => {
            if one.is_empty() || !one.bytes().all(|b| b.is_ascii_digit()) {
                return Err(ParseError::Bad);
            }
            one.parse::<u64>().unwrap_or(u64::MAX) // all digits: only too large can fail
        }
        _ => return Err(ParseError::Bad),
    };
    if length > limits.max_body as u64 {
        return Err(ParseError::BodyTooLarge);
    }
    let mut body = vec![0u8; length as usize];
    r.read_exact(&mut body).map_err(|e| {
        if e.kind() == io::ErrorKind::UnexpectedEof {
            ParseError::Bad
        } else {
            ParseError::Io
        }
    })?;
    Ok(Request {
        method: method.to_string(),
        target: target.to_string(),
        headers,
        body,
    })
}

/// A response. Its `Debug` shows the status and header names only: a body can hold an issued token or codes.
#[derive(Clone, PartialEq, Eq)]
pub struct Response {
    pub status: u16,
    /// Extra headers (Content-Length, Content-Type, Cache-Control and Connection are written by `write`).
    pub headers: Vec<(&'static str, String)>,
    /// A JSON body, or empty.
    pub body: Vec<u8>,
}

impl std::fmt::Debug for Response {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let names: Vec<&str> = self.headers.iter().map(|(n, _)| *n).collect();
        write!(
            f,
            "Response({} headers={names:?} body={} bytes)",
            self.status,
            self.body.len()
        )
    }
}

impl Response {
    pub fn json(status: u16, body: Vec<u8>) -> Self {
        Response {
            status,
            headers: Vec::new(),
            body,
        }
    }
    pub fn empty(status: u16) -> Self {
        Response {
            status,
            headers: Vec::new(),
            body: Vec::new(),
        }
    }
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        413 => "Content Too Large",
        415 => "Unsupported Media Type",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        503 => "Service Unavailable",
        _ => "Status",
    }
}

/// Writes the response and nothing else; the connection is closed after it.
pub fn write_response<W: Write>(w: &mut W, response: &Response) -> io::Result<()> {
    let mut head = format!("HTTP/1.1 {} {}\r\n", response.status, reason(response.status));
    if !response.body.is_empty() {
        head.push_str("Content-Type: application/json\r\n");
    }
    head.push_str(&format!("Content-Length: {}\r\n", response.body.len()));
    head.push_str("Cache-Control: no-store\r\nConnection: close\r\n");
    for (name, value) in &response.headers {
        // Values are the runtime's own (numbers and fixed words); a line break would split the response.
        if value.bytes().any(|b| b < 0x20 || b == 0x7f) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "header value"));
        }
        head.push_str(&format!("{name}: {value}\r\n"));
    }
    head.push_str("\r\n");
    w.write_all(head.as_bytes())?;
    w.write_all(&response.body)?;
    w.flush()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn parse(raw: &[u8]) -> Result<Request, ParseError> {
        read_request(&mut Cursor::new(raw.to_vec()), LIMITS)
    }

    #[test]
    fn a_request_with_a_body() {
        let r = parse(b"POST /app/notes HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}").unwrap();
        assert_eq!(
            (r.method.as_str(), r.target.as_str(), r.body.as_slice()),
            ("POST", "/app/notes", &b"{}"[..])
        );
        assert_eq!(r.header("host"), Some("x"));
        assert_eq!(parse(b"GET /health HTTP/1.0\r\n\r\n").unwrap().body, b"");
    }

    #[test]
    fn malformed_requests_are_refused() {
        let cases: &[&[u8]] = &[
            b"GET /x HTTP/1.1\n\n",
            b"GET /x HTTP/1.1\r\nHost: x\n\r\n",
            b"GET  /x HTTP/1.1\r\n\r\n",
            b"get /x HTTP/1.1\r\n\r\n",
            b"GET x HTTP/1.1\r\n\r\n",
            b"GET /x HTTP/2\r\n\r\n",
            b"GET /a b HTTP/1.1\r\n\r\n",
            b"GET /x HTTP/1.1\r\n folded: x\r\n\r\n",
            b"GET /x HTTP/1.1\r\nBad Name: x\r\n\r\n",
            b"GET /x HTTP/1.1\r\nNoColon\r\n\r\n",
            b"GET /x HTTP/1.1\r\nX: a\x00b\r\n\r\n",
            b"POST /x HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
            b"POST /x HTTP/1.1\r\nContent-Length: +1\r\n\r\nx",
            b"POST /x HTTP/1.1\r\nContent-Length: 1,1\r\n\r\nx",
            b"POST /x HTTP/1.1\r\nContent-Length: 5\r\n\r\nab",
            b"GET /x HTTP/1.1\r\n",
            b"GET /\xff HTTP/1.1\r\n\r\n",
            // Each check on its own, with an input only it refuses.
            b"GET /a\tb HTTP/1.1\r\n\r\n",
            b"GET /a\x7fb HTTP/1.1\r\n\r\n",
            b"GET /\xc3\xa9 HTTP/1.1\r\n\r\n",
            b"GET /x HTTP/1.1\r\nX: a\rContent-Length: 5\r\n\r\n",
            b"GET /x HTTP/1.1\r\nX: a\x7fb\r\n\r\n",
            b"GET /x HTTP/1.1\r\n: x\r\n\r\n",
            b"GET /x HTTP/1.2\r\n\r\n",
            b"GET /x HTTP/1.1 junk\r\n\r\n",
            b" /x HTTP/1.1\r\n\r\n",
            b"GET /x HTTP/1.1\r\nHost: x",
            b"POST /x HTTP/1.1\r\nContent-Length: \r\n\r\n",
        ];
        for raw in cases {
            assert_eq!(parse(raw), Err(ParseError::Bad), "{:?}", String::from_utf8_lossy(raw));
        }
    }

    #[test]
    fn limits_and_codings() {
        assert_eq!(parse(b""), Err(ParseError::Closed));
        assert_eq!(
            parse(b"POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"),
            Err(ParseError::Unsupported)
        );
        for coding in ["chunked", "Chunked", "gzip, chunked", "identity", ""] {
            let raw = format!("POST /x HTTP/1.1\r\nContent-Length: 1\r\nTransfer-Encoding: {coding}\r\n\r\nx");
            assert_eq!(parse(raw.as_bytes()), Err(ParseError::Unsupported), "{coding}");
        }
        assert_eq!(
            parse(b"POST /x HTTP/1.1\r\nContent-Length:\t2\t\r\n\r\nab")
                .unwrap()
                .body,
            b"ab"
        );
        let statuses = [
            (ParseError::Closed, None),
            (ParseError::Io, None),
            (ParseError::Bad, Some(400)),
            (ParseError::HeadTooLarge, Some(431)),
            (ParseError::BodyTooLarge, Some(413)),
            (ParseError::Unsupported, Some(501)),
        ];
        for (e, status) in statuses {
            assert_eq!(e.status(), status);
        }
        let big = format!("POST /x HTTP/1.1\r\nContent-Length: {}\r\n\r\n", LIMITS.max_body + 1);
        assert_eq!(parse(big.as_bytes()), Err(ParseError::BodyTooLarge));
        let long = format!("GET /x HTTP/1.1\r\nX: {}\r\n\r\n", "a".repeat(LIMITS.max_head));
        assert_eq!(parse(long.as_bytes()), Err(ParseError::HeadTooLarge));
        let many: String = (0..=LIMITS.max_headers).map(|i| format!("X{i}: a\r\n")).collect();
        assert_eq!(
            parse(format!("GET /x HTTP/1.1\r\n{many}\r\n").as_bytes()),
            Err(ParseError::HeadTooLarge)
        );
        assert_eq!(
            parse(b"POST /x HTTP/1.1\r\nContent-Length: 99999999999999999999\r\n\r\n"),
            Err(ParseError::BodyTooLarge)
        );
    }

    #[test]
    fn debug_never_shows_header_values_or_bodies() {
        let r = parse(b"POST /x HTTP/1.1\r\nAuthorization: Bearer SECRETTOKEN\r\nContent-Length: 9\r\n\r\nhunter222")
            .unwrap();
        let shown = format!("{r:?}");
        assert!(
            !shown.contains("SECRETTOKEN") && !shown.contains("hunter") && shown.contains("authorization"),
            "{shown}"
        );
        let mut out = Response::json(200, br#"{"token":"SECRETTOKEN"}"#.to_vec());
        out.headers.push(("Retry-After", "SECRETTOKEN".into()));
        assert!(!format!("{out:?}").contains("SECRETTOKEN"));
    }

    #[test]
    fn a_header_sent_twice_has_no_single_value() {
        let r = parse(b"GET /x HTTP/1.1\r\nAuthorization: a\r\nAuthorization: b\r\n\r\n").unwrap();
        assert_eq!(r.header("authorization"), None);
    }

    #[test]
    fn responses_close_and_never_split() {
        let mut out = Vec::new();
        let mut r = Response::json(429, b"{}".to_vec());
        r.headers.push(("Retry-After", "30".into()));
        write_response(&mut out, &r).unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\
             Cache-Control: no-store\r\nConnection: close\r\nRetry-After: 30\r\n\r\n{}"
        );
        for value in ["a\r\nInjected: 1", "a\nInjected: 1", "a\rInjected: 1", "a\x7f"] {
            let mut bad = Response::empty(200);
            bad.headers.push(("X", value.into()));
            let mut out = Vec::new();
            assert!(write_response(&mut out, &bad).is_err(), "{value:?}");
            assert!(out.is_empty(), "nothing is written for a refused header");
        }
    }
}
