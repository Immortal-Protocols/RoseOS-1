//! A generated server: the app's routes, the sign-in routes, readiness, and logs that hold nothing private.
//!
//! - **Routes.** The app's routes come from its contract (§7.3): an exact method and path under `/app/`, `auth`
//!   account (the default) or public, and whether a JSON request body comes with it. Codegen (slice 8) writes the
//!   typed handlers; this module calls them.
//! - **The account check.** An account route runs only with a live session of this app (rose-signin's
//!   `validate_for`, checked again here), and its handler gets the principal. Nothing else names an account.
//! - **Readiness.** `/health` answers `200` only once the sealer is ready, the migrations are done and any restore
//!   has run, and `503` until then; it carries the host's identity fields either way, and nothing else (§7.7):
//!   `app_id`, `revision_sha256` and `deployment_sha256`, all given by the host and the server's start-up, never
//!   made up here. Until it is ready, every other route answers `503 not_ready`.
//! - **Logs** name the method, the route's template (never the path a client sent), the status and the time taken.
//!   No body, header, token, passphrase, code, key, account name or client address reaches a log line.

use std::any::Any;
use std::io::{self as stdio, BufReader, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::panic::{self, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::http::{self, Limits, Request, Response};
use crate::json::{self, Value};
use crate::signin::{self, Admission, Auth, Principal};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RouteAuth {
    /// A signed-in user of this app (the contract's default).
    Account,
    Public,
}

/// What an app handler returns: a JSON value (`200`), or an error word with a client error status.
#[derive(Clone, Debug, PartialEq)]
pub enum Reply {
    Ok(Value),
    /// A 4xx status and a lower-case word (`{"error": word}`); anything else is a server error.
    Refused(u16, &'static str),
}

/// What a handler is given: the signed-in principal on an account route, the time, and the server's data (what
/// the adapter gave `Server::with_data`: per-account and shared databases; the handler downcasts it).
pub struct Ctx<'a> {
    pub principal: Option<&'a Principal>,
    pub now: u64,
    pub data: &'a (dyn Any + Send + Sync),
}

pub type Handler = fn(&Ctx, Option<&Value>) -> Reply;

#[derive(Clone, Copy)]
pub struct Route {
    pub method: &'static str,
    pub path: &'static str,
    pub auth: RouteAuth,
    /// A JSON request body comes with it (the contract names a request type); GET and DELETE never have one.
    pub body: bool,
    pub handler: Handler,
}

/// The host's identity fields for `/health` (§7.7). The server's start-up fills them: `app_id` and
/// `deployment_sha256` from what the host passes, `revision_sha256` by hashing the server's own binary.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity {
    pub app_id: String,
    pub revision_sha256: String,
    pub deployment_sha256: String,
}

/// What readiness waits for besides the sealer: the start-up sets each once it is true.
#[derive(Debug, Default)]
pub struct Readiness {
    pub migrations_done: AtomicBool,
    pub restore_done: AtomicBool,
}

/// One log line's fields: never more than these.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LogLine {
    pub method: &'static str,
    /// The route's template, or `-` for a request that matched none.
    pub route: &'static str,
    pub status: u16,
    pub micros: u128,
}

pub type Log = dyn Fn(&LogLine) + Send + Sync;
pub type Clock = dyn Fn() -> u64 + Send + Sync;

pub struct Server {
    /// The app every session must belong to (rose-signin's app name for this server).
    pub app: String,
    pub admission: Admission,
    pub routes: Vec<Route>,
    pub identity: Identity,
    pub readiness: Readiness,
    pub limits: Limits,
    /// Connections served at once; more are answered `503` and closed.
    pub max_connections: usize,
    pub timeout: Duration,
    auth: Mutex<Box<dyn Auth>>,
    log: Box<Log>,
    clock: Box<Clock>,
    data: Box<dyn Any + Send + Sync>,
    open: AtomicUsize,
}

const METHODS: [&str; 5] = ["GET", "POST", "PUT", "DELETE", "HEAD"];

fn method_label(method: &str) -> &'static str {
    METHODS.iter().copied().find(|m| *m == method).unwrap_or("OTHER")
}

pub fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Writes a log line to standard error.
pub fn stderr_log(line: &LogLine) {
    eprintln!("{} {} {} {}us", line.method, line.route, line.status, line.micros);
}

impl Server {
    /// A server over `auth`. Two routes with one method and path is a codegen fault, refused here.
    pub fn new(
        app: &str,
        admission: Admission,
        routes: Vec<Route>,
        identity: Identity,
        auth: Box<dyn Auth>,
    ) -> Result<Server, String> {
        for (i, a) in routes.iter().enumerate() {
            if !a.path.starts_with("/app/") {
                return Err(format!("route {} is not under /app/", a.path));
            }
            if matches!(a.method, "GET" | "DELETE") && a.body {
                return Err(format!("{} {} cannot take a body", a.method, a.path));
            }
            if !matches!(a.method, "GET" | "POST" | "PUT" | "DELETE") {
                return Err(format!("route {} has an unknown method", a.path));
            }
            if routes[..i].iter().any(|b| b.method == a.method && b.path == a.path) {
                return Err(format!("{} {} is listed twice", a.method, a.path));
            }
        }
        Ok(Server {
            app: app.to_string(),
            admission,
            routes,
            identity,
            readiness: Readiness::default(),
            limits: http::LIMITS,
            max_connections: 64,
            timeout: Duration::from_secs(10),
            auth: Mutex::new(auth),
            log: Box::new(stderr_log),
            clock: Box::new(unix_now),
            data: Box::new(()),
            open: AtomicUsize::new(0),
        })
    }

    pub fn with_log(mut self, log: Box<Log>) -> Self {
        self.log = log;
        self
    }

    pub fn with_clock(mut self, clock: Box<Clock>) -> Self {
        self.clock = clock;
        self
    }

    /// What handlers reach as `ctx.data` (the adapter's databases).
    pub fn with_data(mut self, data: Box<dyn Any + Send + Sync>) -> Self {
        self.data = data;
        self
    }

    /// Connections being served now.
    pub fn open_connections(&self) -> usize {
        self.open.load(Ordering::SeqCst)
    }

    fn auth(&self) -> std::sync::MutexGuard<'_, Box<dyn Auth>> {
        // A handler panic never holds this lock (handlers do not see it), so poisoning means an Auth panicked;
        // its state is rose-signin's, transactional, so serving on is safe.
        self.auth.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Ready: the sealer's key is loaded, the migrations are done and any restore has run.
    pub fn ready(&self) -> bool {
        self.readiness.migrations_done.load(Ordering::SeqCst)
            && self.readiness.restore_done.load(Ordering::SeqCst)
            && self.auth().ready()
    }

    fn health(&self) -> Response {
        let body = Value::obj([
            ("app_id", Value::str(self.identity.app_id.clone())),
            ("revision_sha256", Value::str(self.identity.revision_sha256.clone())),
            ("deployment_sha256", Value::str(self.identity.deployment_sha256.clone())),
        ]);
        let status = if self.ready() { 200 } else { 503 };
        Response::json(status, json::to_string(&body).unwrap_or_default().into_bytes())
    }

    /// One request's response and the route template it is logged under. `client` is what rate limits count by.
    pub fn respond(&self, req: &Request, client: &str) -> (Response, &'static str) {
        let path = req.target.as_str();
        if path == "/health" {
            return if req.method == "GET" {
                (self.health(), "/health")
            } else {
                (signin::error(405, "method_not_allowed"), "/health")
            };
        }
        if !self.ready() {
            return (signin::error(503, "not_ready"), "-");
        }
        let now = (self.clock)();
        if signin::is_signin_path(path) {
            // Read and check the request first: the authenticator is held only for the call itself.
            let r = match signin::parse(&req.method, path, &req.body, req.header("authorization")) {
                Ok(call) => signin::run(&mut **self.auth(), &self.app, self.admission, call, client, now),
                Err(r) => r,
            };
            return (r, "/signin/v1");
        }
        let Some(route) = self.routes.iter().find(|r| r.path == path && r.method == req.method) else {
            let status = if self.routes.iter().any(|r| r.path == path) {
                405
            } else {
                404
            };
            let word = if status == 405 {
                "method_not_allowed"
            } else {
                "not_found"
            };
            return (signin::error(status, word), "-");
        };
        let principal = match route.auth {
            RouteAuth::Public => None,
            RouteAuth::Account => {
                match signin::principal(&mut **self.auth(), &self.app, req.header("authorization"), now) {
                    Ok(p) => Some(p),
                    Err(r) => return (r, route.path),
                }
            }
        };
        let body = if route.body {
            if req
                .header("content-type")
                .map(|v| v.split(';').next().unwrap_or("").trim().to_ascii_lowercase())
                .as_deref()
                != Some("application/json")
            {
                return (signin::error(415, "json_required"), route.path);
            }
            match json::parse(&req.body) {
                Ok(v) => Some(v),
                Err(_) => return (signin::error(400, "bad_request"), route.path),
            }
        } else {
            if !req.body.is_empty() {
                return (signin::error(400, "bad_request"), route.path);
            }
            None
        };
        let ctx = Ctx {
            principal: principal.as_ref(),
            now,
            data: &*self.data,
        };
        let reply = panic::catch_unwind(AssertUnwindSafe(|| (route.handler)(&ctx, body.as_ref())));
        let response = match reply {
            Ok(Reply::Ok(v)) => match json::to_string(&v) {
                Some(text) => Response::json(200, text.into_bytes()),
                None => signin::error(500, "internal"),
            },
            Ok(Reply::Refused(status, word))
                if (400..500).contains(&status)
                    && !word.is_empty()
                    && word.bytes().all(|b| b.is_ascii_lowercase() || b == b'_') =>
            {
                signin::error(status, word)
            }
            Ok(Reply::Refused(..)) | Err(_) => signin::error(500, "internal"),
        };
        (response, route.path)
    }

    /// Serves one connection: one request, one response, then close. A panic anywhere in answering it (an
    /// authenticator or a log sink, as well as a handler) answers `500 internal` and is contained here.
    pub fn serve_stream<S: Read + Write>(&self, stream: S, client: &str) {
        let started = Instant::now();
        let mut reader = BufReader::new(stream);
        let (response, method, route) = match http::read_request(&mut reader, self.limits) {
            Ok(req) => match panic::catch_unwind(AssertUnwindSafe(|| self.respond(&req, client))) {
                Ok((r, route)) => (r, method_label(&req.method), route),
                Err(_) => (signin::error(500, "internal"), method_label(&req.method), "-"),
            },
            Err(e) => match e.status() {
                Some(status) => (signin::error(status, "bad_request"), "OTHER", "-"),
                None => return,
            },
        };
        let mut stream = reader.into_inner();
        let _ = http::write_response(&mut stream, &response);
        let line = LogLine {
            method,
            route,
            status: response.status,
            micros: started.elapsed().as_micros(),
        };
        let _ = panic::catch_unwind(AssertUnwindSafe(|| (self.log)(&line)));
    }

    /// Accepts connections, each on its own thread, at most `max_connections` at once. A request must arrive
    /// within `timeout` of the connection, and its response leave within `timeout` (a deadline for the whole of
    /// each, not each read). Failing to accept one connection (out of descriptors, say) waits briefly and goes on;
    /// it never stops the server. Rate limits count by the peer's address (§7.7 item 6 is open).
    pub fn serve(self: Arc<Self>, listener: TcpListener) -> stdio::Result<()> {
        let mut backoff = Duration::from_millis(10);
        loop {
            let stream = match listener.accept() {
                Ok((s, _)) => {
                    backoff = Duration::from_millis(10);
                    s
                }
                Err(e) if e.kind() == stdio::ErrorKind::Interrupted => continue,
                Err(_) => {
                    std::thread::sleep(backoff);
                    backoff = (backoff * 2).min(Duration::from_secs(1));
                    continue;
                }
            };
            if self.open.fetch_add(1, Ordering::SeqCst) >= self.max_connections {
                self.open.fetch_sub(1, Ordering::SeqCst);
                let mut s = Timed::new(&stream, Duration::from_millis(200));
                let _ = http::write_response(&mut s, &signin::error(503, "busy"));
                close(&stream, Duration::from_millis(50));
                continue;
            }
            let slot = Slot(Arc::clone(&self));
            let spawned = std::thread::Builder::new().spawn(move || {
                let server = &slot.0;
                let client = stream.peer_addr().map(|a| a.ip().to_string()).unwrap_or_default();
                server.serve_stream(Timed::new(&stream, server.timeout), &client);
                close(&stream, Duration::from_secs(1));
            });
            drop(spawned); // a thread that could not start drops its closure, and with it the slot
        }
    }
}

/// A connection slot, given back when the connection's thread ends, however it ends.
struct Slot(Arc<Server>);

impl Drop for Slot {
    fn drop(&mut self) {
        self.0.open.fetch_sub(1, Ordering::SeqCst);
    }
}

/// A socket with a deadline for all its reads together, and one for all its writes (from the first write).
struct Timed<'a> {
    stream: &'a TcpStream,
    span: Duration,
    read_by: Instant,
    write_by: Option<Instant>,
}

impl<'a> Timed<'a> {
    fn new(stream: &'a TcpStream, span: Duration) -> Self {
        Timed {
            stream,
            span,
            read_by: Instant::now() + span,
            write_by: None,
        }
    }
}

fn left(by: Instant) -> stdio::Result<Duration> {
    let left = by.saturating_duration_since(Instant::now());
    if left.is_zero() {
        Err(stdio::Error::new(stdio::ErrorKind::TimedOut, "deadline"))
    } else {
        Ok(left)
    }
}

impl Read for Timed<'_> {
    fn read(&mut self, buf: &mut [u8]) -> stdio::Result<usize> {
        self.stream.set_read_timeout(Some(left(self.read_by)?))?;
        (&*self.stream).read(buf)
    }
}

impl Write for Timed<'_> {
    fn write(&mut self, buf: &[u8]) -> stdio::Result<usize> {
        let by = *self.write_by.get_or_insert_with(|| Instant::now() + self.span);
        self.stream.set_write_timeout(Some(left(by)?))?;
        (&*self.stream).write(buf)
    }
    fn flush(&mut self) -> stdio::Result<()> {
        (&*self.stream).flush()
    }
}

/// Ends the connection so the response is not lost to a reset: no more writes, then what the client still sends
/// (an unread body, extra bytes) is read and dropped, up to 64 KB or `wait`.
fn close(stream: &TcpStream, wait: Duration) {
    let _ = stream.shutdown(Shutdown::Write);
    let by = Instant::now() + wait;
    let mut buf = [0u8; 4096];
    let mut drained = 0usize;
    while drained < 64 * 1024 {
        let Ok(left) = left(by) else { break };
        if stream.set_read_timeout(Some(left)).is_err() {
            break;
        }
        match (&*stream).read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => drained += n,
        }
    }
}
