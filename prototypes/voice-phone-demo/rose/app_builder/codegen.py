"""Level 2 codegen (docs/APP-BUILDER.md §7.3–7.5, slice 8): from one canonical contract (rose.app_builder.contract),
the server's typed Rust handlers and routes, and the client's Java API and sign-in overlay. Source only; nothing
calls this yet.

Both halves are generated from ``Contract.data`` (canonical: defaults spelled out), so the same contract always gives
the same files, byte for byte. Nothing here invents a URL, a key, an app id or an account: the client takes its base
URL from the caller, and the server takes its app id, port, data directory and deployment hash from the host.

**Server** (``server_files``): files under ``rose/`` for rose.app_builder.server_build (keys match its ``GENERATED``):
- ``rose/api.rs``: one struct per contract type, converting from and to the runtime's ``json::Value``; the
  ``Handlers`` trait the writer implements, one associated function per route; and ``Refusal`` (a 4xx status and an
  error word of lower-case letters and underscores);
- ``rose/routes.rs``: ``routes::<H: Handlers>()``, the ``Vec<Route>`` the runtime's ``Server::new`` takes;
- ``rose/wire.rs``: the JSON helpers they share;
- ``rose/main.rs``: the crate root and start-up (``MAIN_RS``), over OS Core's adapter (rose-app-runtime-signin).

The crate root declares those modules, the builder's own ``rose/meta.rs`` (``APP``, ``ISSUER``, ``ADMISSION``),
``rose/migrations.rs`` (``ACCOUNT``, ``SHARED``) and ``rose/assets.rs`` (``get``, the writer's ``server/assets``),
which codegen never writes, and the writer's code,
``#[path = "../src/handlers/mod.rs"] mod handlers;``, which defines ``pub struct App;`` and
``impl crate::api::Handlers for App`` (the convention is App Core's: rose.app_builder.appprofile ``HANDLERS``).

**Client** (``client_files``): files under the reserved ``src/<package>/rose/`` (rose.app_builder.appprofile):
- ``RoseApi.java``: one class per contract type, a ``Client`` with one method per route over an injectable
  ``Transport``, and ``RoseApi.Error``;
- ``RoseJson.java``: a small strict JSON reader and writer, so the client needs nothing beyond the platform;
- ``RoseSignin.java``: the ``rose-signin-v1`` client (enroll, confirm, sign in, recover, sign out);
- ``RoseInvite.java``: the creator's account and invite from the intent the builder starts the app with, once;
- ``RoseSession.java``: the bearer token in no-backup storage, and opening an ``otpauth://`` URI with
  ``ACTION_VIEW`` (sharing the app's identity on API 34+, §7.5) or drawing it as a QR code;
- ``RoseQr.java``: the QR encoder that drawing uses (a port of rose.qr, tested to give the same modules);
- ``RoseSigninCore.java``: OS Core's client core, byte for byte at the pinned digest.

``RoseApi.serverUrl()`` reads the builder's own ``RoseServer.java`` (the hosting record's base URL, or null before the
app is hosted), which codegen never writes.

Wire rules, the same on both sides (§7.3): an ``optional`` field may be absent or null; fields neither side knows are
ignored; ``max`` bounds a string's characters, bytes' length and a list's or map's entries in requests only (the
server refuses more, with ``400 bad_request``); ``bytes`` travel as standard, padded base64 text, strict on read; ``float``
accepts a JSON integer; ``int`` is a 64-bit integer.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .contract import Contract

SIGNIN_CORE = Path(__file__).resolve().parents[4] / 'workspace/rose-signin/java/src/dev/pixel/rosesignin/RoseSigninCore.java'
SIGNIN_CORE_SHA256 = '6209a9cdab7b82e1cc942b634c011d28cff51b5592174cf0ec76e9edb6ac587f'
PACKAGE = re.compile(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+')
INVALID = 'bad_request'  # the runtime's word for a malformed request, on every route

# ---- names ----------------------------------------------------------------------------------------------------------

RUST_KEYWORDS = {'as', 'async', 'await', 'break', 'const', 'continue', 'crate', 'dyn', 'else', 'enum', 'extern',
                 'false', 'fn', 'for', 'gen', 'if', 'impl', 'in', 'let', 'loop', 'match', 'mod', 'move', 'mut',
                 'pub', 'ref', 'return', 'self', 'static', 'struct', 'super', 'trait', 'true', 'try', 'type',
                 'unsafe', 'use', 'where', 'while', 'abstract', 'become', 'box', 'do', 'final', 'macro', 'override',
                 'priv', 'typeof', 'unsized', 'virtual', 'yield', 'union'}
RUST_NOT_RAW = {'self', 'super', 'crate', 'Self'}
JAVA_KEYWORDS = {'abstract', 'assert', 'boolean', 'break', 'byte', 'case', 'catch', 'char', 'class', 'const',
                 'continue', 'default', 'do', 'double', 'else', 'enum', 'extends', 'final', 'finally', 'float', 'for',
                 'goto', 'if', 'implements', 'import', 'instanceof', 'int', 'interface', 'long', 'native', 'new',
                 'package', 'private', 'protected', 'public', 'return', 'short', 'static', 'strictfp', 'super',
                 'switch', 'synchronized', 'this', 'throw', 'throws', 'transient', 'try', 'void', 'volatile', 'while',
                 'true', 'false', 'null', 'var', 'record', 'yield', 'sealed', 'permits', 'non', '_'}


def snake(name: str) -> str:
    """lowerCamelCase to snake_case: listNotes -> list_notes, getHTTPCode -> get_h_t_t_p_code (stable, not pretty)."""
    return re.sub(r'[A-Z]', lambda m: '_' + m.group(0).lower(), name)


def rust_ident(name: str) -> str:
    if name in RUST_NOT_RAW:
        return name + '_'
    return 'r#' + name if name in RUST_KEYWORDS else name


RUST_RESERVED_TYPES = ('Self', 'Handlers', 'Refusal')  # Self is Rust's; the others live beside the types in api.rs


def rust_type_name(name: str) -> str:
    return name + '_' if name in RUST_RESERVED_TYPES else name


def java_ident(name: str) -> str:
    return name + '_' if name in JAVA_KEYWORDS else name


# ---- server (Rust) --------------------------------------------------------------------------------------------------

RT = '::rose_app_runtime'
STRING, VEC, OPTION, BOX, MAP = ('::std::string::String', '::std::vec::Vec', '::std::option::Option',
                                 '::std::boxed::Box', '::std::collections::BTreeMap')
RUST_PRIMS = {'bool': 'bool', 'int': 'i64', 'float': 'f64', 'string': STRING, 'bytes': f'{VEC}<u8>'}


def _rust_type(f: dict, boxed: bool = False) -> str:
    kind = f['type']
    if kind in RUST_PRIMS:
        return RUST_PRIMS[kind]
    if kind == 'list':
        return f'{VEC}<{_rust_type(f["of"])}>'
    if kind == 'map':
        return f'{MAP}<{STRING}, {_rust_type(f["of"])}>'
    name = f'crate::api::{rust_type_name(kind)}'
    return f'{BOX}<{name}>' if boxed else name


def _max(f: dict) -> str:
    return f'Some({f["max"]})' if 'max' in f else 'None'


def _rust_in(v: str, f: dict, depth: int = 0) -> str:
    """A Rust expression parsing ``v`` (a ``&Value``) as field type ``f``, using ``?`` for refusals."""
    kind, e = f['type'], f'e{depth}'
    if kind in ('bool', 'int', 'float'):
        return f'crate::wire::{kind}_in({v})?'
    if kind in ('string', 'bytes'):
        return f'crate::wire::{kind}_in({v}, {_max(f)})?'
    if kind == 'list':
        return f'crate::wire::list_in({v}, {_max(f)}, |{e}| Ok({_rust_in(e, f["of"], depth + 1)}))?'
    if kind == 'map':
        return f'crate::wire::map_in({v}, {_max(f)}, |{e}| Ok({_rust_in(e, f["of"], depth + 1)}))?'
    return f'crate::api::{rust_type_name(kind)}::from_value({v})?'


def _rust_out(r: str, f: dict, depth: int = 0) -> str:
    """A Rust expression turning ``r`` (a reference to field type ``f``) into a ``Value``."""
    kind, e = f['type'], f'e{depth}'
    if kind == 'bool':
        return f'{RT}::json::Value::Bool(*({r}))'
    if kind == 'int':
        return f'{RT}::json::Value::Int(*({r}))'
    if kind == 'float':
        return f'{RT}::json::Value::Float(*({r}))'
    if kind == 'string':
        return f'{RT}::json::Value::Str(({r}).clone())'
    if kind == 'bytes':
        return f'{RT}::json::Value::Str(crate::wire::base64_encode(({r}).as_slice()))'
    if kind == 'list':
        return f'{RT}::json::Value::Arr(({r}).iter().map(|{e}| {_rust_out(e, f["of"], depth + 1)}).collect())'
    if kind == 'map':
        return (f'{RT}::json::Value::Obj(({r}).iter().map(|(k{depth}, {e})| (k{depth}.clone(), '
                f'{_rust_out(e, f["of"], depth + 1)})).collect())')
    return f'({r}).to_value()'


def _rust_struct(name: str, fields: dict) -> str:
    t = rust_type_name(name)
    lines = ['#[derive(Clone, Debug, PartialEq)]', f'pub struct {t} {{']
    for field, f in sorted(fields.items()):
        boxed = f['optional'] and f['type'] not in RUST_PRIMS and f['type'] not in ('list', 'map')
        ty = _rust_type(f, boxed)
        lines.append(f'    pub {rust_ident(snake(field))}: {OPTION + "<" + ty + ">" if f["optional"] else ty},')
    lines += ['}', '', f'impl {t} {{',
              '    /// Parses a request value; a missing required field, a wrong type or more than `max` is refused.',
              f'    pub fn from_value(v: &{RT}::json::Value) -> ::std::result::Result<Self, crate::wire::Invalid> {{',
              '        crate::wire::object(v)?;', '        Ok(Self {']
    for field, f in sorted(fields.items()):
        ident = rust_ident(snake(field))
        if f['optional']:
            inner = _rust_in('x', f)
            boxed = f['type'] not in RUST_PRIMS and f['type'] not in ('list', 'map')
            some = f'{BOX}::new({inner})' if boxed else inner
            lines.append(f'            {ident}: match crate::wire::member(v, "{field}") {{ None => None, '
                         f'Some(x) => Some({some}) }},')
        else:
            lines.append(f'            {ident}: {_rust_in(f"crate::wire::required(v, \"{field}\")?", f)},')
    lines += ['        })', '    }', '',
              '    /// The value sent: absent optional fields are left out.',
              f'    pub fn to_value(&self) -> {RT}::json::Value {{',
              f'        let {"mut " if fields else ""}out: {VEC}<({STRING}, {RT}::json::Value)> = {VEC}::new();']
    for field, f in sorted(fields.items()):
        ident = rust_ident(snake(field))
        if f['optional']:
            lines.append(f'        if let Some(x) = &self.{ident} {{ out.push(({STRING}::from("{field}"), '
                         f'{_rust_out("x", f)})); }}')
        else:
            lines.append(f'        out.push(({STRING}::from("{field}"), {_rust_out(f"&self.{ident}", f)}));')
    lines += [f'        {RT}::json::Value::Obj(out)', '    }', '}', '']
    return '\n'.join(lines)


HEADER = '// Generated by the Rose app builder from contract/api.json (sha256 {sha}). Do not edit.\n'

WIRE_RS = '''//! JSON helpers the generated types share (docs/APP-BUILDER.md §7.3).
#![allow(dead_code)]

use ::rose_app_runtime::json::Value;

/// A request value that does not match the contract.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Invalid;

pub fn object(v: &Value) -> Result<(), Invalid> {
    match v {
        Value::Obj(_) => Ok(()),
        _ => Err(Invalid),
    }
}

/// A member that is present and not null.
pub fn member<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    match v.get(key) {
        None | Some(Value::Null) => None,
        Some(x) => Some(x),
    }
}

pub fn required<'a>(v: &'a Value, key: &str) -> Result<&'a Value, Invalid> {
    member(v, key).ok_or(Invalid)
}

pub fn bool_in(v: &Value) -> Result<bool, Invalid> {
    match v {
        Value::Bool(b) => Ok(*b),
        _ => Err(Invalid),
    }
}

pub fn int_in(v: &Value) -> Result<i64, Invalid> {
    match v {
        Value::Int(i) => Ok(*i),
        _ => Err(Invalid),
    }
}

pub fn float_in(v: &Value) -> Result<f64, Invalid> {
    match v {
        Value::Int(i) => Ok(*i as f64),
        Value::Float(f) => Ok(*f),
        _ => Err(Invalid),
    }
}

fn within(n: usize, max: Option<u64>) -> Result<(), Invalid> {
    match max {
        Some(m) if n as u64 > m => Err(Invalid),
        _ => Ok(()),
    }
}

/// A string, at most `max` characters (Unicode scalar values).
pub fn string_in(v: &Value, max: Option<u64>) -> Result<String, Invalid> {
    match v {
        Value::Str(s) => {
            within(s.chars().count(), max)?;
            Ok(s.clone())
        }
        _ => Err(Invalid),
    }
}

/// Bytes as standard, padded base64, at most `max` bytes once decoded.
pub fn bytes_in(v: &Value, max: Option<u64>) -> Result<Vec<u8>, Invalid> {
    match v {
        Value::Str(s) => {
            let b = base64_decode(s).ok_or(Invalid)?;
            within(b.len(), max)?;
            Ok(b)
        }
        _ => Err(Invalid),
    }
}

pub fn list_in<T>(v: &Value, max: Option<u64>, each: impl Fn(&Value) -> Result<T, Invalid>) -> Result<Vec<T>, Invalid> {
    match v {
        Value::Arr(items) => {
            within(items.len(), max)?;
            items.iter().map(each).collect()
        }
        _ => Err(Invalid),
    }
}

pub fn map_in<T>(
    v: &Value,
    max: Option<u64>,
    each: impl Fn(&Value) -> Result<T, Invalid>,
) -> Result<std::collections::BTreeMap<String, T>, Invalid> {
    match v {
        Value::Obj(members) => {
            within(members.len(), max)?;
            let mut out = std::collections::BTreeMap::new();
            for (k, x) in members {
                out.insert(k.clone(), each(x)?);
            }
            Ok(out)
        }
        _ => Err(Invalid),
    }
}

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn base64_encode(data: &[u8]) -> String {
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = (b[0] as u32) << 16 | (b[1] as u32) << 8 | b[2] as u32;
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(ALPHABET[(n >> (18 - 6 * i) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// Strict standard base64: padded to a multiple of 4, `=` only at the end, no unused bits set.
pub fn base64_decode(text: &str) -> Option<Vec<u8>> {
    let s = text.as_bytes();
    if s.len() % 4 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(s.len() / 4 * 3);
    for (q, quad) in s.chunks(4).enumerate() {
        let last = q == s.len() / 4 - 1;
        let pad = quad.iter().rev().take_while(|&&c| c == b'=').count();
        if pad > 2 || (pad > 0 && !last) {
            return None;
        }
        let mut n: u32 = 0;
        for (i, &c) in quad.iter().enumerate() {
            let d = if i >= 4 - pad {
                0
            } else {
                ALPHABET.iter().position(|&a| a == c)? as u32
            };
            n = n << 6 | d;
        }
        let bytes = [(n >> 16) as u8, (n >> 8) as u8, n as u8];
        if (pad == 1 && bytes[2] != 0) || (pad == 2 && (bytes[1] != 0 || bytes[2] != 0)) {
            return None;
        }
        out.extend_from_slice(&bytes[..3 - pad]);
    }
    Some(out)
}
'''


MAIN_RS = '''//! The server's start-up (docs/APP-BUILDER.md §7.4, §7.7): OS Core's adapter opens and migrates the data, the
//! runtime serves the contract's routes. The host passes `PORT` (read here) and, read by the adapter, `DATA_DIR`,
//! `CHARIOT_APP_ID`, `CHARIOT_DEPLOYMENT_SHA256`, the optional `CHARIOT_REVISION_SHA256`, the sealing keyring
//! `CHARIOT_SEALING_KEY_FILE` and the optional `CHARIOT_RESTORE_STATE_PATH`; the build passes `meta` and
//! `migrations`. Nothing is made up.
//!
//! Readiness is the adapter's: `/health` answers 200 only with the keys loaded, the migrations done and no restore
//! indicated (nothing at the restore marker's path, by default `DATA_DIR/.chariot-restore.json`). A marker, or a
//! lookup that fails, keeps it at 503; nothing here or in the adapter runs, reads or removes a restore.
//!
//! `invite <account>` (§7.4): asks the adapter for one single-use invite for the account, prints it once on stdout and
//! exits. It reads no keyring and binds no port. The invite is a credential: stdout goes only to whoever ran the
//! command, never into the service's log capture.

mod api;
mod assets;
#[path = "../src/handlers/mod.rs"]
mod handlers;
mod meta;
mod migrations;
mod routes;
mod wire;

use std::ffi::OsString;
use std::io::Write;
use std::net::TcpListener;
use std::sync::Arc;

use rose_app_runtime_signin::{invite, start, Config, Keys, Migrations};

/// The header Chariot's gateway overwrites with the client's address on every request; guest ingress admits only
/// the gateway (§7.7 item 6). Rate limits count by it when it holds exactly one IP address, by the peer otherwise.
const CLIENT_HEADER: &str = "X-Chariot-Client-Address";
/// The variable the host passes the port in, and the port when it passes none (§7.7).
const PORT_ENV: &str = "PORT";
const DEFAULT_PORT: u16 = 8080;

/// An environment variable: absent is `None`; present but not text is refused (its value is never shown).
fn var(name: &str) -> Result<Option<String>, String> {
    match std::env::var(name) {
        Ok(value) => Ok(Some(value)),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => Err(format!("{name} is not valid")),
    }
}

/// The port: decimal digits only, 1 to 65535.
fn port(value: Option<String>) -> Result<u16, String> {
    let Some(text) = value else { return Ok(DEFAULT_PORT) };
    match text.parse::<u16>() {
        Ok(port) if port != 0 && text.bytes().all(|b| b.is_ascii_digit()) => Ok(port),
        _ => Err(format!("{PORT_ENV} is not a port")),
    }
}

/// The variable naming the address to listen on (§7.7a: an agent container's gateway reaches the app on loopback,
/// so its start script sets 127.0.0.1), and the address when it names none.
const LISTEN_HOST_ENV: &str = "ROSE_LISTEN_HOST";
const DEFAULT_LISTEN_HOST: &str = "0.0.0.0";

/// The listen address: exactly `127.0.0.1` or `0.0.0.0`, nothing else.
fn listen_host(value: Option<String>) -> Result<&'static str, String> {
    match value.as_deref() {
        None => Ok(DEFAULT_LISTEN_HOST),
        Some("127.0.0.1") => Ok("127.0.0.1"),
        Some("0.0.0.0") => Ok("0.0.0.0"),
        Some(_) => Err(format!("{LISTEN_HOST_ENV} is not 127.0.0.1 or 0.0.0.0")),
    }
}

/// What the program was asked to do.
#[derive(Debug, PartialEq)]
enum Command {
    /// No arguments: serve.
    Serve,
    /// `invite <account>`: print one new invite for the account, as typed.
    Invite(String),
}

/// The arguments after the program's name: nothing, or exactly `invite <account>` with a text account.
fn command(args: &[OsString]) -> Option<Command> {
    match args {
        [] => Some(Command::Serve),
        [verb, account] if verb == "invite" => account.to_str().map(|a| Command::Invite(a.to_string())),
        _ => None,
    }
}

const USAGE: &str = "usage: no arguments to serve, or invite <account>";

/// The same configuration serving uses, from the host's environment and the build.
fn config() -> Result<Config, String> {
    let migrations = Migrations { account: migrations::ACCOUNT, shared: migrations::SHARED };
    Config::from_env(meta::APP, meta::ISSUER, meta::ADMISSION, migrations).map_err(|e| e.to_string())
}

/// One single-use invite (the adapter's: 7 days, bound to the account), printed once on stdout and nowhere else.
/// No keyring is read and no port is bound; a refusal is one word (`no invite: <word>`), never the account or token.
fn invite_one(account: &str) -> Result<(), String> {
    let config = config()?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| "the clock is before 1970".to_string())?
        .as_secs();
    let token = invite(&config, account, now).map_err(|e| e.to_string())?;
    let mut out = std::io::stdout().lock();
    writeln!(out, "{}", token.as_str())
        .and_then(|()| out.flush())
        .map_err(|_| "the invite could not be written".to_string())
}

fn run() -> Result<(), String> {
    let port = port(var(PORT_ENV)?)?;
    let host = listen_host(var(LISTEN_HOST_ENV)?)?;
    let config = config()?;
    // The adapter loads the host's keyring. Any problem leaves no key, never a stop: sign-in answers
    // `auth_unavailable` and `/health` answers 503, and the reason is one word, never the path or a byte of the file.
    let (keys, why) = Keys::from_env();
    if let Some(why) = why {
        eprintln!("rose server: {why}");
    }
    let started = start(config, keys).map_err(|e| e.to_string())?;
    let server = started
        .server(routes::routes::<handlers::App>())
        .map_err(|e| format!("routes: {e}"))?
        .with_client_header(CLIENT_HEADER);
    let listener = TcpListener::bind((host, port)).map_err(|e| format!("listening on {PORT_ENV}: {e}"))?;
    Arc::new(server).serve(listener).map_err(|e| format!("serving: {e}"))
}

fn main() {
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    let done = match command(&args) {
        Some(Command::Serve) => run(),
        Some(Command::Invite(account)) => invite_one(&account),
        None => {
            eprintln!("rose server: {USAGE}");
            std::process::exit(2);
        }
    };
    if let Err(message) = done {
        eprintln!("rose server: {message}");
        std::process::exit(1);
    }
}
'''


def _route_types(route: dict) -> tuple[str | None, str]:
    req = route['request'] and f'crate::api::{rust_type_name(route["request"])}'
    return req, f'crate::api::{rust_type_name(route["response"])}'


def server_files(contract: Contract) -> dict[str, str]:
    """The server's generated Rust, keyed by path under the server crate (rose.app_builder.server_build
    ``GENERATED``; ``rose/main.rs`` is its ``ENTRY``). The builder adds ``rose/meta.rs``, ``rose/migrations.rs``
    and ``rose/assets.rs`` itself (docs/APP-BUILDER.md §7.5)."""
    data = contract.data
    head = HEADER.format(sha=contract.sha256)
    types = '\n'.join(_rust_struct(n, t['fields']) for n, t in sorted(data['types'].items()))
    trait = [head + '//! The contract\'s types, and what the writer implements: one function per route '
             '(docs/APP-BUILDER.md §7.3).', '#![allow(dead_code, clippy::all)]', '',
             '/// A refusal a handler returns: a 4xx status and an error word (`{"error": word}`) of lower-case letters',
             '/// and underscores; the runtime answers any other word with a 500.',
             '#[derive(Clone, Copy, Debug, PartialEq, Eq)]',
             'pub struct Refusal {', '    pub status: u16,', "    pub word: &'static str,", '}', '',
             'impl Refusal {', "    pub const fn new(status: u16, word: &'static str) -> Refusal {",
             '        Refusal { status, word }', '    }', '}', '',
             '/// The app\'s routes. Account routes are called only with a signed-in user of this app in `ctx`.',
             'pub trait Handlers {']
    routes = [head, '//! The contract\'s routes as the runtime serves them (docs/APP-BUILDER.md §7.4).',
              '#![allow(dead_code)]', '', f'use {RT}::json::Value;',
              f'use {RT}::server::{{Ctx, Reply, Route, RouteAuth}};', '', 'use crate::api::Handlers;', '']
    table = ['/// Every route, for `Server::new`.', 'pub fn routes<H: Handlers>() -> Vec<Route> {', '    vec![']
    for name, route in sorted(data['routes'].items()):
        fn = rust_ident(snake(name))
        req, resp = _route_types(route)
        params = f'ctx: &{RT}::server::Ctx' + (f', request: {req}' if req else '')
        trait.append(f'    /// {route["method"]} {route["path"]} ({route["auth"]})')
        trait.append(f'    fn {fn}({params}) -> ::std::result::Result<{resp}, Refusal>;')
        wrapper = f'route_{snake(name)}'
        body = [f'fn {wrapper}<H: Handlers>(ctx: &Ctx, body: Option<&Value>) -> Reply {{']
        if req:
            body += ['    let request = match body.map(' + req + '::from_value) {', '        Some(Ok(r)) => r,',
                     f'        _ => return Reply::Refused(400, "{INVALID}"),', '    };',
                     f'    match H::{fn}(ctx, request) {{']
        else:
            body += ['    let _ = body;', f'    match H::{fn}(ctx) {{']
        body += ['        Ok(response) => Reply::Ok(response.to_value()),',
                 '        Err(refusal) => Reply::Refused(refusal.status, refusal.word),', '    }', '}', '']
        routes += body
        auth = 'RouteAuth::Account' if route['auth'] == 'account' else 'RouteAuth::Public'
        table.append(f'        Route {{ method: "{route["method"]}", path: "{route["path"]}", auth: {auth}, '
                     f'body: {"true" if req else "false"}, handler: {wrapper}::<H> }},')
    trait += ['}', '']
    table += ['    ]', '}', '']
    return {'rose/api.rs': '\n'.join(trait) + '\n' + types, 'rose/routes.rs': '\n'.join(routes + table),
            'rose/wire.rs': head + WIRE_RS, 'rose/main.rs': head + MAIN_RS}


# ---- client (Java) --------------------------------------------------------------------------------------------------

JAVA_OBJECT_METHODS = {'getClass', 'hashCode', 'equals', 'clone', 'toString', 'notify', 'notifyAll', 'wait',
                       'finalize'}
JAVA_PRIMS = {'bool': ('boolean', 'java.lang.Boolean'), 'int': ('long', 'java.lang.Long'),
              'float': ('double', 'java.lang.Double'), 'string': ('java.lang.String', 'java.lang.String'),
              'bytes': ('byte[]', 'byte[]')}
TYPES_CLASS = 'RoseTypes'


def java_type_name(name: str) -> str:
    return name + '_' if name == TYPES_CLASS else name


def java_method(name: str) -> str:
    return name + '_' if name in JAVA_KEYWORDS or name in JAVA_OBJECT_METHODS else name


def _java_type(f: dict, boxed: bool, pkg: str) -> str:
    kind = f['type']
    if kind in JAVA_PRIMS:
        return JAVA_PRIMS[kind][1 if boxed else 0]
    if kind == 'list':
        return f'java.util.List<{_java_type(f["of"], True, pkg)}>'
    if kind == 'map':
        return f'java.util.Map<java.lang.String, {_java_type(f["of"], True, pkg)}>'
    return f'{pkg}.rose.{TYPES_CLASS}.{java_type_name(kind)}'


def _java_in(e: str, f: dict, pkg: str, depth: int = 0) -> str:
    """A Java expression reading ``e`` (a parsed JSON value) as field type ``f``; throws RoseJson.Bad."""
    kind, j, x = f['type'], f'{pkg}.rose.RoseJson', f'x{depth}'
    if kind in ('bool', 'int', 'float', 'string', 'bytes'):
        return f'{j}.{kind}_({e})'
    if kind == 'list':
        return f'{j}.list({e}, {x} -> {_java_in(x, f["of"], pkg, depth + 1)})'
    if kind == 'map':
        return f'{j}.map({e}, {x} -> {_java_in(x, f["of"], pkg, depth + 1)})'
    return f'{pkg}.rose.{TYPES_CLASS}.{java_type_name(kind)}.fromJson({e})'


def _java_out(e: str, f: dict, pkg: str, depth: int = 0) -> str:
    """A Java expression turning ``e`` (a non-null value of field type ``f``) into a JSON value for RoseJson."""
    kind, j, x = f['type'], f'{pkg}.rose.RoseJson', f'x{depth}'
    if kind == 'bytes':
        return f'java.util.Base64.getEncoder().encodeToString({e})'
    if kind in JAVA_PRIMS:
        return e
    if kind == 'list':
        return f'{j}.listOut({e}, {x} -> {_java_out(x, f["of"], pkg, depth + 1)})'
    if kind == 'map':
        return f'{j}.mapOut({e}, {x} -> {_java_out(x, f["of"], pkg, depth + 1)})'
    return f'{e}.toJson()'


def _java_class(name: str, fields: dict, pkg: str) -> str:
    t, j = java_type_name(name), f'{pkg}.rose.RoseJson'
    out = [f'    public static final class {t} {{']
    for field, f in sorted(fields.items()):
        out.append(f'        public {_java_type(f, f["optional"], pkg)} {java_ident(field)};')
    out += [f'', f'        public {t}() {{}}', '',
            '        /** Reads a response value: unknown fields are ignored, a missing required field is refused. */',
            f'        public static {t} fromJson(java.lang.Object v) throws {j}.Bad {{',
            f'            java.util.Map<java.lang.String, java.lang.Object> o = {j}.object(v);',
            f'            {t} r = new {t}();']
    for field, f in sorted(fields.items()):
        ident = java_ident(field)
        if f['optional']:
            out.append(f'            java.lang.Object m_{field} = {j}.member(o, "{field}");')
            out.append(f'            r.{ident} = m_{field} == null ? null : {_java_in(f"m_{field}", f, pkg)};')
        else:
            out.append(f'            r.{ident} = {_java_in(f"{j}.required(o, {chr(34)}{field}{chr(34)})", f, pkg)};')
    out += ['            return r;', '        }', '',
            '        /** The value sent: null optional fields are left out; a null required field is refused. */',
            f'        public java.util.Map<java.lang.String, java.lang.Object> toJson() {{',
            '            java.util.Map<java.lang.String, java.lang.Object> o = new java.util.LinkedHashMap<>();']
    for field, f in sorted(fields.items()):
        ident = java_ident(field)
        if f['optional']:
            out.append(f'            if ({ident} != null) o.put("{field}", {_java_out(ident, f, pkg)});')
        elif f['type'] in ('bool', 'int', 'float'):
            out.append(f'            o.put("{field}", {ident});')
        else:
            out.append(f'            o.put("{field}", {_java_out(f"java.util.Objects.requireNonNull({ident}, {chr(34)}{field}{chr(34)})", f, pkg)});')
    out += ['            return o;', '        }', '    }', '']
    return '\n'.join(out)


JAVA_HEADER = '// Generated by the Rose app builder from contract/api.json (sha256 {sha}). Do not edit.\n'

ROSE_JSON = r'''package {pkg}.rose;

/**
 * A small strict JSON reader and writer for the generated client (RFC 8259): objects are LinkedHashMaps, arrays
 * Lists, integers Longs, other numbers Doubles, plus Strings, Booleans and null. Repeated keys, trailing data, lone
 * surrogates and nesting deeper than 64 are refused.
 */
public final class RoseJson {
    private RoseJson() {}

    public static final int MAX_DEPTH = 64;

    /** A value that is not JSON, or not the shape the contract says. */
    public static final class Bad extends Exception {
        private static final long serialVersionUID = 1L;
        public Bad(String m) { super(m); }
    }

    public interface Read<T> { T read(Object v) throws Bad; }
    public interface Write<T> { Object write(T v); }

    public static Object parse(String s) throws Bad {
        Reader r = new Reader(s);
        r.ws();
        Object v = r.value(0);
        r.ws();
        if (r.i != s.length()) throw new Bad("trailing data");
        return v;
    }

    private static final class Reader {
        final String s; int i;
        Reader(String s) { this.s = s; }
        void ws() { while (i < s.length() && " \t\r\n".indexOf(s.charAt(i)) >= 0) i++; }
        char peek() throws Bad { if (i >= s.length()) throw new Bad("unexpected end"); return s.charAt(i); }
        void expect(char c) throws Bad { if (peek() != c) throw new Bad("expected " + c); i++; }
        Object value(int depth) throws Bad {
            if (depth > MAX_DEPTH) throw new Bad("too deep");
            char c = peek();
            if (c == '{') return object(depth);
            if (c == '[') return array(depth);
            if (c == '"') return string();
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            return number();
        }
        java.util.Map<String, Object> object(int depth) throws Bad {
            java.util.Map<String, Object> m = new java.util.LinkedHashMap<>();
            expect('{'); ws();
            if (peek() == '}') { i++; return m; }
            while (true) {
                ws();
                if (peek() != '"') throw new Bad("expected key");
                String k = string();
                ws(); expect(':'); ws();
                if (m.containsKey(k)) throw new Bad("repeated key");
                m.put(k, value(depth + 1));
                ws();
                char c = peek(); i++;
                if (c == '}') return m;
                if (c != ',') throw new Bad("expected , or }");
            }
        }
        java.util.List<Object> array(int depth) throws Bad {
            java.util.List<Object> a = new java.util.ArrayList<>();
            expect('['); ws();
            if (peek() == ']') { i++; return a; }
            while (true) {
                ws(); a.add(value(depth + 1)); ws();
                char c = peek(); i++;
                if (c == ']') return a;
                if (c != ',') throw new Bad("expected , or ]");
            }
        }
        String string() throws Bad {
            expect('"');
            StringBuilder b = new StringBuilder();
            while (true) {
                char c = peek(); i++;
                if (c == '"') break;
                if (c < 0x20) throw new Bad("control character");
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
                        if (i + 4 > s.length()) throw new Bad("bad escape");
                        try { b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); }
                        catch (NumberFormatException x) { throw new Bad("bad escape"); }
                        i += 4; break;
                    default: throw new Bad("bad escape");
                }
            }
            String out = b.toString();
            for (int k = 0; k < out.length(); k++) {
                char c = out.charAt(k);
                if (Character.isHighSurrogate(c) && k + 1 < out.length() && Character.isLowSurrogate(out.charAt(k + 1))) k++;
                else if (Character.isSurrogate(c)) throw new Bad("lone surrogate");
            }
            return out;
        }
        Object number() throws Bad {
            int start = i;
            if (i < s.length() && s.charAt(i) == '-') i++;
            if (i >= s.length() || !Character.isDigit(s.charAt(i))) throw new Bad("bad value");
            if (s.charAt(i) == '0') i++; else while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
            boolean integral = true;
            if (i < s.length() && s.charAt(i) == '.') {
                integral = false; i++;
                if (i >= s.length() || !Character.isDigit(s.charAt(i))) throw new Bad("bad number");
                while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
            }
            if (i < s.length() && (s.charAt(i) == 'e' || s.charAt(i) == 'E')) {
                integral = false; i++;
                if (i < s.length() && (s.charAt(i) == '+' || s.charAt(i) == '-')) i++;
                if (i >= s.length() || !Character.isDigit(s.charAt(i))) throw new Bad("bad number");
                while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
            }
            String t = s.substring(start, i);
            if (integral) {
                try { return Long.parseLong(t); } catch (NumberFormatException x) { /* beyond a long: a double */ }
            }
            double d = Double.parseDouble(t);
            if (Double.isInfinite(d) || Double.isNaN(d)) throw new Bad("number out of range");
            return d;
        }
    }

    public static String write(Object v) {
        StringBuilder b = new StringBuilder();
        write(b, v);
        return b.toString();
    }

    @SuppressWarnings("unchecked")
    private static void write(StringBuilder b, Object v) {
        if (v == null) b.append("null");
        else if (v instanceof String) quote(b, (String) v);
        else if (v instanceof Boolean || v instanceof Long || v instanceof Integer) b.append(v);
        else if (v instanceof Double) {
            double d = (Double) v;
            if (Double.isNaN(d) || Double.isInfinite(d)) throw new IllegalArgumentException("not a JSON number");
            b.append(d);
        } else if (v instanceof java.util.Map) {
            b.append('{'); boolean first = true;
            for (java.util.Map.Entry<String, Object> e : ((java.util.Map<String, Object>) v).entrySet()) {
                if (!first) b.append(','); first = false;
                quote(b, e.getKey()); b.append(':'); write(b, e.getValue());
            }
            b.append('}');
        } else if (v instanceof java.util.List) {
            b.append('['); boolean first = true;
            for (Object o : (java.util.List<Object>) v) { if (!first) b.append(','); first = false; write(b, o); }
            b.append(']');
        } else throw new IllegalArgumentException("not a JSON value");
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

    // ---- contract shapes (§7.3) ----

    @SuppressWarnings("unchecked")
    public static java.util.Map<String, Object> object(Object v) throws Bad {
        if (!(v instanceof java.util.Map)) throw new Bad("expected an object");
        return (java.util.Map<String, Object>) v;
    }

    /** A member that is present and not null, or null. */
    public static Object member(java.util.Map<String, Object> o, String key) { return o.get(key); }

    public static Object required(java.util.Map<String, Object> o, String key) throws Bad {
        Object v = o.get(key);
        if (v == null) throw new Bad("missing " + key);
        return v;
    }

    public static boolean bool_(Object v) throws Bad {
        if (v instanceof Boolean) return (Boolean) v;
        throw new Bad("expected a bool");
    }

    public static long int_(Object v) throws Bad {
        if (v instanceof Long) return (Long) v;
        throw new Bad("expected an int");
    }

    /** A float also accepts a JSON integer. */
    public static double float_(Object v) throws Bad {
        if (v instanceof Long) return (Long) v;
        if (v instanceof Double) return (Double) v;
        throw new Bad("expected a float");
    }

    public static String string_(Object v) throws Bad {
        if (v instanceof String) return (String) v;
        throw new Bad("expected a string");
    }

    /** Bytes travel as standard, padded base64. */
    public static byte[] bytes_(Object v) throws Bad {
        String s = string_(v);
        if (s.length() % 4 != 0) throw new Bad("bad base64");
        try { return java.util.Base64.getDecoder().decode(s); }
        catch (IllegalArgumentException e) { throw new Bad("bad base64"); }
    }

    public static <T> java.util.List<T> list(Object v, Read<T> each) throws Bad {
        if (!(v instanceof java.util.List)) throw new Bad("expected a list");
        java.util.List<T> out = new java.util.ArrayList<>();
        for (Object e : (java.util.List<?>) v) {
            if (e == null) throw new Bad("null in a list");
            out.add(each.read(e));
        }
        return out;
    }

    public static <T> java.util.Map<String, T> map(Object v, Read<T> each) throws Bad {
        java.util.Map<String, T> out = new java.util.LinkedHashMap<>();
        for (java.util.Map.Entry<String, Object> e : object(v).entrySet()) {
            if (e.getValue() == null) throw new Bad("null in a map");
            out.put(e.getKey(), each.read(e.getValue()));
        }
        return out;
    }

    public static <T> java.util.List<Object> listOut(java.util.List<T> v, Write<T> each) {
        java.util.List<Object> out = new java.util.ArrayList<>();
        for (T e : v) out.add(each.write(java.util.Objects.requireNonNull(e, "null in a list")));
        return out;
    }

    public static <T> java.util.Map<String, Object> mapOut(java.util.Map<String, T> v, Write<T> each) {
        java.util.Map<String, Object> out = new java.util.LinkedHashMap<>();
        for (java.util.Map.Entry<String, T> e : v.entrySet())
            out.put(e.getKey(), each.write(java.util.Objects.requireNonNull(e.getValue(), "null in a map")));
        return out;
    }
}
'''

ROSE_API = r'''package {pkg}.rose;

/**
 * The app's API client (docs/APP-BUILDER.md §7.3): one method per contract route. The base URL is the builder's
 * hosting record, {@link #serverUrl()}, which the caller hands to {@link HttpsTransport}; nothing here invents one.
 * Account routes send the signed-in session's bearer token; a 401 {"error":"session_invalid"} forgets it.
 */
public final class RoseApi {
    private RoseApi() {}

    /**
     * The app server's base URL from the builder's hosting record, or null while the app is not hosted yet (then
     * say the server is not set up; never guess one). It is fixed when the app is built, so the first hosted
     * version of the app needs a build after the record exists.
     */
    public static String serverUrl() {
        return RoseServer.URL;
    }

    /** A refusal or failure: the HTTP status (0 when the server could not be reached) and an error word. */
    public static final class Error extends Exception {
        private static final long serialVersionUID = 1L;
        public final int status;
        public final String word;
        public final long retryAfterSeconds;
        public Error(int status, String word, long retryAfterSeconds) {
            super(status + " " + word);
            this.status = status; this.word = word; this.retryAfterSeconds = retryAfterSeconds;
        }
    }

    /** One HTTP exchange's result. */
    public static final class Response {
        public final int status;
        public final byte[] body;
        public final long retryAfterSeconds;
        public Response(int status, byte[] body, long retryAfterSeconds) {
            this.status = status; this.body = body == null ? new byte[0] : body; this.retryAfterSeconds = retryAfterSeconds;
        }
    }

    /** Sends one request; tests and the platform supply it. */
    public interface Transport {
        Response send(String method, String path, String bearer, byte[] body) throws java.io.IOException;
    }

    /** Where the signed-in session's token lives (RoseSession on Android). */
    public interface Tokens {
        String bearer();
        void keep(String token);
        void forget();
    }

    /** HTTPS to the given base URL, which must be an absolute https URL with no credentials, query or fragment. */
    public static final class HttpsTransport implements Transport {
        private final String base;
        public HttpsTransport(String baseUrl) {
            java.net.URI u;
            try { u = new java.net.URI(baseUrl); } catch (java.net.URISyntaxException e) { throw new IllegalArgumentException("not a URL"); }
            if (!"https".equals(u.getScheme()) || u.getHost() == null || u.getRawUserInfo() != null
                    || u.getRawQuery() != null || u.getRawFragment() != null)
                throw new IllegalArgumentException("the server's URL must be plain https");
            this.base = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        }
        @Override public Response send(String method, String path, String bearer, byte[] body) throws java.io.IOException {
            java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(base + path).openConnection();
            try {
                c.setRequestMethod(method);
                c.setInstanceFollowRedirects(false);
                c.setUseCaches(false);
                c.setConnectTimeout(10000);
                c.setReadTimeout(30000);
                if (bearer != null) c.setRequestProperty("Authorization", "Bearer " + bearer);
                if (body != null) {
                    c.setDoOutput(true);
                    c.setRequestProperty("Content-Type", "application/json");
                    c.setFixedLengthStreamingMode(body.length);
                    try (java.io.OutputStream out = c.getOutputStream()) { out.write(body); }
                }
                int status = c.getResponseCode();
                long retry = -1;
                String ra = c.getHeaderField("Retry-After");
                if (ra != null && ra.matches("[0-9]{1,9}")) retry = Long.parseLong(ra);
                java.io.InputStream in = status / 100 == 2 ? c.getInputStream() : c.getErrorStream();
                return new Response(status, in == null ? new byte[0] : read(in), retry);
            } finally {
                c.disconnect();
            }
        }
        private static byte[] read(java.io.InputStream in) throws java.io.IOException {
            try (java.io.InputStream i = in) {
                java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                for (int n; (n = i.read(buf)) > 0; ) {
                    b.write(buf, 0, n);
                    if (b.size() > (1 << 20)) throw new java.io.IOException("response too large");
                }
                return b.toByteArray();
            }
        }
    }

    /** Sends one call and returns its parsed 2xx body (null for an empty one); anything else is an Error. */
    static Object exchange(Transport t, String method, String path, String bearer, Object body, Tokens tokens) throws Error {
        Response r;
        try {
            r = t.send(method, path, bearer, body == null ? null
                    : RoseJson.write(body).getBytes(java.nio.charset.StandardCharsets.UTF_8));
        } catch (java.io.IOException e) {
            throw new Error(0, "unreachable", -1);
        }
        String text = new String(r.body, java.nio.charset.StandardCharsets.UTF_8);
        if (r.status / 100 == 2) {
            if (r.body.length == 0) return null;
            try { return RoseJson.parse(text); } catch (RoseJson.Bad e) { throw new Error(r.status, "bad_response", -1); }
        }
        String word = "http_" + r.status;
        try {
            Object e = RoseJson.object(RoseJson.parse(text)).get("error");
            if (e instanceof String && ((String) e).matches("[a-z][a-z_]{0,63}")) word = (String) e;
        } catch (RoseJson.Bad ignored) { /* the status alone */ }
        if (r.status == 401 && "session_invalid".equals(word) && tokens != null) tokens.forget();
        throw new Error(r.status, word, r.retryAfterSeconds);
    }

    /** One method per contract route. */
    public static final class Client {
        private final Transport transport;
        private final Tokens tokens;
        public Client(Transport transport, Tokens tokens) { this.transport = transport; this.tokens = tokens; }

        private String bearer() throws Error {
            String b = tokens == null ? null : tokens.bearer();
            if (b == null) throw new Error(401, "signed_out", -1);
            return b;
        }

        private static <T> T read(Object v, RoseJson.Read<T> how) throws Error {
            try { return how.read(v); } catch (RoseJson.Bad e) { throw new Error(200, "bad_response", -1); }
        }
{methods}    }
}
'''


def _java_methods(contract: Contract, pkg: str) -> str:
    out = []
    types = f'{pkg}.rose.{TYPES_CLASS}'
    for name, route in sorted(contract.data['routes'].items()):
        resp = f'{types}.{java_type_name(route["response"])}'
        req = route['request'] and f'{types}.{java_type_name(route["request"])}'
        params = f'{req} request' if req else ''
        bearer = 'bearer()' if route['auth'] == 'account' else 'null'
        body = 'request.toJson()' if req else 'null'
        out += [f'        /** {route["method"]} {route["path"]} ({route["auth"]}) */',
                f'        public {resp} {java_method(name)}({params}) throws Error {{']
        if req:
            out.append('            java.util.Objects.requireNonNull(request, "request");')
        out += [f'            Object v = exchange(transport, "{route["method"]}", "{route["path"]}", {bearer}, {body}, tokens);',
                f'            return read(v, {resp}::fromJson);', '        }', '']
    return '\n'.join(out)


ROSE_SIGNIN = r'''package {pkg}.rose;

/**
 * The rose-signin-v1 client (docs/ROSE-SIGNIN-V1.md, §7.5) against this app's server: enroll with an authenticator
 * and a passphrase, confirm, sign in, recover, sign out. Only the bearer token is kept (RoseSession); passphrases,
 * codes and recovery codes pass through and are never stored. Every refusal is a RoseApi.Error with the server's
 * word: passphrase_too_short, passphrase_too_long, invalid_name, not_admitted, enrollment_pending, already_enrolled,
 * not_confirmed, sign_in_failed, recovery_failed, session_invalid, try_later (with retryAfterSeconds) or
 * auth_unavailable.
 */
public final class RoseSignin {
    private final RoseApi.Transport transport;
    private final RoseApi.Tokens tokens;

    public RoseSignin(RoseApi.Transport transport, RoseApi.Tokens tokens) { this.transport = transport; this.tokens = tokens; }

    /** What enrollment (or authenticator recovery) hands back once: show the URI, keep the recovery codes safe. */
    public static final class Enrollment {
        public final String otpauthUri, handle;
        public final java.util.List<String> recoveryCodes;
        Enrollment(String otpauthUri, String handle, java.util.List<String> recoveryCodes) {
            this.otpauthUri = otpauthUri; this.handle = handle; this.recoveryCodes = recoveryCodes;
        }
        @Override public String toString() { return "Enrollment(<redacted>)"; }
    }

    /** A signed-in session: the account, and new recovery codes when a restore issued them (else empty). */
    public static final class Session {
        public final String account;
        public final java.util.List<String> recoveryCodes;
        Session(String account, java.util.List<String> recoveryCodes) { this.account = account; this.recoveryCodes = recoveryCodes; }
    }

    /** The client-side passphrase check (the server decides): null if acceptable, else a reason. */
    public static String passphraseProblem(String passphrase) {
        return dev.pixel.rosesignin.RoseSigninCore.knowledgeProblem(passphrase);
    }

    private static java.util.Map<String, Object> body(String... kv) {
        java.util.Map<String, Object> m = new java.util.LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) if (kv[i + 1] != null) m.put(kv[i], kv[i + 1]);
        return m;
    }

    private static String text(java.util.Map<String, Object> o, String key) throws RoseApi.Error {
        Object v = o.get(key);
        if (!(v instanceof String)) throw new RoseApi.Error(200, "bad_response", -1);
        return (String) v;
    }

    private static java.util.List<String> codes(java.util.Map<String, Object> o, boolean required) throws RoseApi.Error {
        Object v = o.get("recovery_codes");
        if (v == null && !required) return java.util.Collections.emptyList();
        try { return java.util.Collections.unmodifiableList(RoseJson.list(v, RoseJson::string_)); }
        catch (RoseJson.Bad e) { throw new RoseApi.Error(200, "bad_response", -1); }
    }

    private static java.util.Map<String, Object> obj(Object v) throws RoseApi.Error {
        try { return RoseJson.object(v); } catch (RoseJson.Bad e) { throw new RoseApi.Error(200, "bad_response", -1); }
    }

    private Enrollment enrollment(Object v) throws RoseApi.Error {
        java.util.Map<String, Object> o = obj(v);
        String uri = text(o, "otpauth_uri");
        if (dev.pixel.rosesignin.RoseSigninCore.parseOtpAuth(uri) == null) throw new RoseApi.Error(200, "bad_response", -1);
        return new Enrollment(uri, text(o, "handle"), codes(o, true));
    }

    /** Starts enrollment; invite is the creator's invite code (null under open sign-up), handle resumes one. */
    public Enrollment enroll(String account, String passphrase, String invite, String handle) throws RoseApi.Error {
        return enrollment(RoseApi.exchange(transport, "POST", "/signin/v1/enroll", null,
                body("account", account, "passphrase", passphrase, "invite", invite, "handle", handle), null));
    }

    /** Confirms enrollment with the first code from the authenticator. */
    public void confirm(String account, String handle, String code) throws RoseApi.Error {
        RoseApi.exchange(transport, "POST", "/signin/v1/enroll/confirm", null,
                body("account", account, "handle", handle, "code", code), null);
    }

    /** Signs in; the token is kept in the session store and never returned. */
    public Session signIn(String account, String code, String passphrase) throws RoseApi.Error {
        java.util.Map<String, Object> o = obj(RoseApi.exchange(transport, "POST", "/signin/v1/session", null,
                body("account", account, "code", code, "passphrase", passphrase), null));
        tokens.keep(text(o, "token"));
        return new Session(text(o, "account"), codes(o, false));
    }

    /** A new authenticator, with a recovery code and the passphrase. */
    public Enrollment recoverAuthenticator(String account, String recoveryCode, String passphrase) throws RoseApi.Error {
        return enrollment(RoseApi.exchange(transport, "POST", "/signin/v1/recover/authenticator", null,
                body("account", account, "recovery_code", recoveryCode, "passphrase", passphrase), null));
    }

    /** A new passphrase, with a recovery code and an authenticator code; returns the new recovery codes. */
    public java.util.List<String> recoverPassphrase(String account, String recoveryCode, String code, String newPassphrase) throws RoseApi.Error {
        return codes(obj(RoseApi.exchange(transport, "POST", "/signin/v1/recover/passphrase", null,
                body("account", account, "recovery_code", recoveryCode, "code", code, "new_passphrase", newPassphrase), null)), true);
    }

    /** Signs this session out (everywhere: all of the account's sessions); the token is forgotten either way. */
    public void signOut(boolean everywhere) throws RoseApi.Error {
        String bearer = tokens.bearer();
        if (bearer == null) return;
        try {
            RoseApi.exchange(transport, "DELETE", everywhere ? "/signin/v1/sessions" : "/signin/v1/session", bearer, null, tokens);
        } finally {
            tokens.forget();
        }
    }
}
'''

ROSE_INVITE = r'''package {pkg}.rose;

/**
 * The creator's invite handed over by the Rose builder (docs/APP-BUILDER.md §7.7a). When the builder's "Sign in"
 * starts this app, the launching intent carries the account and its single-use invite. take() returns them once and
 * removes them from that intent; nothing is stored or logged, and toString() is redacted. Typing them in still works.
 */
public final class RoseInvite {
    public static final String EXTRA_ACCOUNT = "rose.signin.account";
    public static final String EXTRA_INVITE = "rose.signin.invite";
    public final String account, invite;

    private RoseInvite(String account, String invite) { this.account = account; this.invite = invite; }

    /** The account and invite the launching intent carries, once; null when it carries none (or not both). */
    public static RoseInvite take(android.app.Activity activity) {
        android.content.Intent intent = activity.getIntent();
        if (intent == null) return null;
        String account, invite;
        try {
            account = intent.getStringExtra(EXTRA_ACCOUNT);
            invite = intent.getStringExtra(EXTRA_INVITE);
        } catch (RuntimeException e) {
            return null;  // extras that cannot be read are no invite
        }
        intent.removeExtra(EXTRA_ACCOUNT);
        intent.removeExtra(EXTRA_INVITE);
        if (account == null || account.isEmpty() || invite == null || !invite.matches("[A-Z2-7]{52}")) return null;
        return new RoseInvite(account, invite);
    }

    @Override public String toString() { return "RoseInvite(<redacted>)"; }
}
'''

ROSE_SESSION = r'''package {pkg}.rose;

/**
 * The signed-in session on the phone (§7.5): only the bearer token is kept, in no-backup storage, so it never leaves
 * the phone in a backup. Also opens an enrollment's otpauth:// URI with ACTION_VIEW, for the RoseOS authenticator or
 * any authenticator app, sharing this app's identity on API 34+ so the authenticator can name it.
 */
public final class RoseSession implements RoseApi.Tokens {
    private final java.io.File file;

    public RoseSession(android.content.Context context) {
        this.file = new java.io.File(context.getNoBackupFilesDir(), "rose-session");
    }

    @Override public synchronized String bearer() {
        try {
            byte[] b = java.nio.file.Files.readAllBytes(file.toPath());
            String t = new String(b, java.nio.charset.StandardCharsets.UTF_8).trim();
            return t.isEmpty() ? null : t;
        } catch (java.io.IOException e) {
            return null;
        }
    }

    @Override public synchronized void keep(String token) {
        try {
            java.io.File tmp = new java.io.File(file.getPath() + ".tmp");
            java.nio.file.Files.write(tmp.toPath(), token.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            if (!tmp.renameTo(file)) throw new java.io.IOException("rename");
        } catch (java.io.IOException e) {
            throw new IllegalStateException("the session could not be kept");
        }
    }

    @Override public synchronized void forget() {
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }

    /** Opens the URI in an authenticator (false if it isn't a v1 enrollment URI or nothing can open it). */
    public static boolean openAuthenticator(android.app.Activity activity, String otpauthUri) {
        if (dev.pixel.rosesignin.RoseSigninCore.parseOtpAuth(otpauthUri) == null) return false;
        android.content.Intent view = new android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(otpauthUri));
        try {
            if (android.os.Build.VERSION.SDK_INT >= 34) {
                android.app.ActivityOptions o = android.app.ActivityOptions.makeBasic().setShareIdentityEnabled(true);
                activity.startActivity(view, o.toBundle());
            } else {
                activity.startActivity(view);
            }
            return true;
        } catch (android.content.ActivityNotFoundException e) {
            return false;
        }
    }

    /**
     * The URI as a QR code to show, for an authenticator on another device to scan: black modules on white with a
     * 4-module quiet zone, pixelsPerModule (1 to 16) pixels each. Null if it isn't a v1 enrollment URI.
     */
    public static android.graphics.Bitmap qr(String otpauthUri, int pixelsPerModule) {
        if (pixelsPerModule < 1 || pixelsPerModule > 16 || dev.pixel.rosesignin.RoseSigninCore.parseOtpAuth(otpauthUri) == null) return null;
        boolean[][] m;
        try {
            m = RoseQr.matrix(otpauthUri);
        } catch (IllegalArgumentException e) {
            return null;
        }
        int border = 4, side = (m.length + 2 * border) * pixelsPerModule;
        int[] px = new int[side * side];
        java.util.Arrays.fill(px, 0xFFFFFFFF);
        for (int y = 0; y < m.length; y++) {
            for (int x = 0; x < m.length; x++) {
                if (!m[y][x]) continue;
                for (int dy = 0; dy < pixelsPerModule; dy++) {
                    int row = ((y + border) * pixelsPerModule + dy) * side + (x + border) * pixelsPerModule;
                    java.util.Arrays.fill(px, row, row + pixelsPerModule, 0xFF000000);
                }
            }
        }
        return android.graphics.Bitmap.createBitmap(px, side, side, android.graphics.Bitmap.Config.ARGB_8888);
    }

    /** Puts the URI on the clipboard as text, for an authenticator on another device. */
    public static void copy(android.content.Context context, String otpauthUri) {
        android.content.ClipboardManager c = (android.content.ClipboardManager) context.getSystemService(android.content.Context.CLIPBOARD_SERVICE);
        if (c != null) c.setPrimaryClip(android.content.ClipData.newPlainText("Authenticator setup", otpauthUri));
    }
}
'''

ROSE_QR = r'''package {pkg}.rose;

/**
 * A QR code for the enrollment URI (docs/ROSE-SIGNIN-V1.md: "shows the enrollment URI as a QR and as copyable text"),
 * so an authenticator on another device can scan it. Byte mode, error correction level M, versions 1 to 20 (up to
 * 666 bytes), per ISO/IEC 18004; generated by the builder as a port of Rose's ZXing-checked encoder (rose/qr.py),
 * and tested to give the same modules. RoseSession.qr draws it.
 */
public final class RoseQr {
    private RoseQr() {}

    private static final int MAX_VERSION = 20;
    private static final int[] ECC_PER_BLOCK = {0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26};
    private static final int[] NUM_BLOCKS = {0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16};
    private static final String[] FINDER_LIKE = {"10111010000", "00001011101"};
    private static final int FALSE_FINDER_PENALTY = 10000;

    /** The symbol for text (UTF-8), without a quiet zone: [y][x], true = dark. Throws if it holds over 666 bytes. */
    public static boolean[][] matrix(String text) {
        byte[] data = text.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        int ver = chooseVersion(data.length);
        int[] words = codewords(data, ver);
        boolean[][] best = null;
        long bestScore = Long.MAX_VALUE;
        for (int mask = 0; mask < 8; mask++) {
            Symbol s = new Symbol(ver);
            s.drawFunctionPatterns();
            s.drawCodewords(words);
            s.applyMask(mask);
            s.drawFormat(mask);
            long score = penalty(s.mod);
            if (score < bestScore) { bestScore = score; best = s.mod; }
        }
        return best;
    }

    private static int gfMul(int x, int y) {
        int z = 0;
        for (int i = 7; i >= 0; i--) {
            z = (z << 1) ^ ((z >>> 7) * 0x11D);
            z ^= ((y >>> i) & 1) * x;
        }
        return z;
    }

    private static int[] rsDivisor(int degree) {
        int[] result = new int[degree];
        result[degree - 1] = 1;
        int root = 1;
        for (int k = 0; k < degree; k++) {
            for (int j = 0; j < degree; j++) {
                result[j] = gfMul(result[j], root);
                if (j + 1 < degree) result[j] ^= result[j + 1];
            }
            root = gfMul(root, 0x02);
        }
        return result;
    }

    private static int[] rsRemainder(int[] data, int from, int n, int[] divisor) {
        int[] result = new int[divisor.length];
        for (int k = from; k < from + n; k++) {
            int factor = data[k] ^ result[0];
            System.arraycopy(result, 1, result, 0, result.length - 1);
            result[result.length - 1] = 0;
            for (int i = 0; i < divisor.length; i++) result[i] ^= gfMul(divisor[i], factor);
        }
        return result;
    }

    private static int rawDataModules(int ver) {
        int result = (16 * ver + 128) * ver + 64;
        if (ver >= 2) {
            int numAlign = ver / 7 + 2;
            result -= (25 * numAlign - 10) * numAlign - 55;
            if (ver >= 7) result -= 36;
        }
        return result;
    }

    private static int dataCodewords(int ver) {
        return rawDataModules(ver) / 8 - ECC_PER_BLOCK[ver] * NUM_BLOCKS[ver];
    }

    private static int countBits(int ver) { return ver <= 9 ? 8 : 16; }

    private static int chooseVersion(int nBytes) {
        for (int ver = 1; ver <= MAX_VERSION; ver++) {
            if (4 + countBits(ver) + 8L * nBytes <= dataCodewords(ver) * 8L) return ver;
        }
        throw new IllegalArgumentException(nBytes + " bytes do not fit a version " + MAX_VERSION + " QR code at level M");
    }

    private static int[] alignmentPositions(int ver) {
        if (ver == 1) return new int[0];
        int numAlign = ver / 7 + 2;
        int step = (ver * 8 + numAlign * 3 + 5) / (numAlign * 4 - 4) * 2;
        int[] result = new int[numAlign];
        result[0] = 6;
        for (int i = numAlign - 1, pos = ver * 4 + 17 - 7; i >= 1; i--, pos -= step) result[i] = pos;
        return result;
    }

    private static int[] codewords(byte[] data, int ver) {
        int capacity = dataCodewords(ver) * 8;
        java.util.BitSet bits = new java.util.BitSet();
        int[] n = {0};
        java.util.function.BiConsumer<Integer, Integer> put = (value, count) -> {
            for (int i = count - 1; i >= 0; i--) { if (((value >>> i) & 1) != 0) bits.set(n[0]); n[0]++; }
        };
        put.accept(0b0100, 4);
        put.accept(data.length, countBits(ver));
        for (byte b : data) put.accept(b & 0xFF, 8);
        put.accept(0, Math.min(4, capacity - n[0]));
        put.accept(0, (8 - n[0] % 8) % 8);
        int[] words = new int[capacity / 8];
        int count = n[0] / 8;
        for (int i = 0; i < count; i++) {
            int w = 0;
            for (int j = 0; j < 8; j++) w = (w << 1) | (bits.get(i * 8 + j) ? 1 : 0);
            words[i] = w;
        }
        for (int i = count, pad = 0xEC; i < words.length; i++, pad ^= 0xEC ^ 0x11) words[i] = pad;

        int numBlocks = NUM_BLOCKS[ver], eccLen = ECC_PER_BLOCK[ver];
        int raw = rawDataModules(ver) / 8;
        int numShort = numBlocks - raw % numBlocks;
        int shortLen = raw / numBlocks;
        int[] divisor = rsDivisor(eccLen);
        int[][] blocks = new int[numBlocks][];
        for (int i = 0, k = 0; i < numBlocks; i++) {
            int len = shortLen - eccLen + (i < numShort ? 0 : 1);
            int[] ecc = rsRemainder(words, k, len, divisor);
            int[] blk = new int[shortLen + 1];
            System.arraycopy(words, k, blk, 0, len);
            k += len;
            System.arraycopy(ecc, 0, blk, shortLen + 1 - eccLen, eccLen);  // a short block keeps a placeholder
            blocks[i] = blk;
        }
        int[] out = new int[raw];
        int o = 0;
        for (int i = 0; i < shortLen + 1; i++) {
            for (int j = 0; j < numBlocks; j++) {
                if (i != shortLen - eccLen || j >= numShort) out[o++] = blocks[j][i];
            }
        }
        return out;
    }

    private static final class Symbol {
        final int ver, size;
        final boolean[][] mod, fn;

        Symbol(int ver) {
            this.ver = ver;
            this.size = ver * 4 + 17;
            this.mod = new boolean[size][size];
            this.fn = new boolean[size][size];
        }

        void setFn(int x, int y, boolean dark) { mod[y][x] = dark; fn[y][x] = true; }

        void drawFunctionPatterns() {
            for (int i = 0; i < size; i++) { setFn(6, i, i % 2 == 0); setFn(i, 6, i % 2 == 0); }
            int[][] centres = {{3, 3}, {size - 4, 3}, {3, size - 4}};
            for (int[] c : centres) {
                for (int dy = -4; dy <= 4; dy++) {
                    for (int dx = -4; dx <= 4; dx++) {
                        int x = c[0] + dx, y = c[1] + dy;
                        int d = Math.max(Math.abs(dx), Math.abs(dy));
                        if (x >= 0 && x < size && y >= 0 && y < size) setFn(x, y, d != 2 && d != 4);
                    }
                }
            }
            int[] pos = alignmentPositions(ver);
            int last = pos.length - 1;
            for (int i = 0; i < pos.length; i++) {
                for (int j = 0; j < pos.length; j++) {
                    if ((i == 0 && j == 0) || (i == 0 && j == last) || (i == last && j == 0)) continue;
                    for (int dy = -2; dy <= 2; dy++) {
                        for (int dx = -2; dx <= 2; dx++) setFn(pos[i] + dx, pos[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) != 1);
                    }
                }
            }
            drawFormat(0);
            drawVersion();
        }

        void drawFormat(int mask) {
            int data = mask;  // level M's format bits are 00
            int rem = data;
            for (int i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
            int bits = (data << 10 | rem) ^ 0x5412;
            for (int i = 0; i < 6; i++) setFn(8, i, bit(bits, i));
            setFn(8, 7, bit(bits, 6));
            setFn(8, 8, bit(bits, 7));
            setFn(7, 8, bit(bits, 8));
            for (int i = 9; i < 15; i++) setFn(14 - i, 8, bit(bits, i));
            for (int i = 0; i < 8; i++) setFn(size - 1 - i, 8, bit(bits, i));
            for (int i = 8; i < 15; i++) setFn(8, size - 15 + i, bit(bits, i));
            setFn(8, size - 8, true);
        }

        void drawVersion() {
            if (ver < 7) return;
            int rem = ver;
            for (int i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25);
            int bits = ver << 12 | rem;
            for (int i = 0; i < 18; i++) {
                boolean dark = bit(bits, i);
                int a = size - 11 + i % 3, b = i / 3;
                setFn(a, b, dark);
                setFn(b, a, dark);
            }
        }

        void drawCodewords(int[] words) {
            int i = 0, total = words.length * 8;
            for (int right = size - 1; right >= 1; right -= 2) {
                if (right == 6) right = 5;
                boolean upward = ((right + 1) & 2) == 0;
                for (int vert = 0; vert < size; vert++) {
                    int y = upward ? size - 1 - vert : vert;
                    for (int x = right; x >= right - 1; x--) {
                        if (!fn[y][x] && i < total) {
                            mod[y][x] = ((words[i >>> 3] >>> (7 - (i & 7))) & 1) == 1;
                            i++;
                        }
                    }
                }
            }
        }

        void applyMask(int mask) {
            for (int y = 0; y < size; y++) {
                for (int x = 0; x < size; x++) {
                    if (!fn[y][x] && masked(mask, x, y)) mod[y][x] = !mod[y][x];
                }
            }
        }
    }

    private static boolean bit(int bits, int i) { return ((bits >>> i) & 1) == 1; }

    private static boolean masked(int mask, int x, int y) {
        switch (mask) {
            case 0: return (x + y) % 2 == 0;
            case 1: return y % 2 == 0;
            case 2: return x % 3 == 0;
            case 3: return (x + y) % 3 == 0;
            case 4: return (x / 3 + y / 2) % 2 == 0;
            case 5: return x * y % 2 + x * y % 3 == 0;
            case 6: return (x * y % 2 + x * y % 3) % 2 == 0;
            default: return ((x + y) % 2 + x * y % 3) % 2 == 0;
        }
    }

    private static long penalty(boolean[][] mod) {
        int size = mod.length;
        long score = 0;
        String[] lines = new String[2 * size];
        for (int y = 0; y < size; y++) {
            StringBuilder row = new StringBuilder(size), col = new StringBuilder(size);
            for (int x = 0; x < size; x++) { row.append(mod[y][x] ? '1' : '0'); col.append(mod[x][y] ? '1' : '0'); }
            lines[y] = row.toString();
            lines[size + y] = col.toString();
        }
        for (String line : lines) {
            int run = 0;
            char prev = 0;
            for (int k = 0; k < line.length(); k++) {
                char c = line.charAt(k);
                if (c == prev) {
                    run++;
                } else {
                    if (run >= 5) score += 3 + run - 5;
                    run = 1;
                    prev = c;
                }
            }
            if (run >= 5) score += 3 + run - 5;
            String padded = "0000" + line + "0000";
            for (String pat : FINDER_LIKE) {
                for (int start = padded.indexOf(pat); start != -1; start = padded.indexOf(pat, start + 1)) score += 40;
            }
        }
        for (int y = 0; y < size - 1; y++) {
            for (int x = 0; x < size - 1; x++) {
                boolean c = mod[y][x];
                if (c == mod[y][x + 1] && c == mod[y + 1][x] && c == mod[y + 1][x + 1]) score += 3;
            }
        }
        long dark = 0;
        for (boolean[] row : mod) for (boolean v : row) if (v) dark++;
        long total = (long) size * size;
        long k = (Math.abs(dark * 20 - total * 10) + total - 1) / total - 1;
        return score + Math.max(0, k) * 10 + (long) FALSE_FINDER_PENALTY * falseFinders(mod);
    }

    // Not an ISO rule: ZXing's detector takes any 2D cross whose runs are about 1:1:3:1:1 for a finder, so a mask
    // leaving one in the data area is avoided whenever another mask does not (as rose/qr.py does).
    private static double crossOk(int[] counts) {  // counts[0..4]; [5] is the centre, not a run
        int total = 0;
        for (int i = 0; i < 5; i++) { if (counts[i] == 0) return -1; total += counts[i]; }
        double m = total / 7.0, v = m / 2.0;
        if (Math.abs(m - counts[0]) < v && Math.abs(m - counts[1]) < v && Math.abs(3 * m - counts[2]) < 3 * v
                && Math.abs(m - counts[3]) < v && Math.abs(m - counts[4]) < v) return m;
        return -1;
    }

    /** dark/light/DARK/light/dark run lengths around the dark module line[i], with the centre in [5]; or null. */
    private static int[] runsThrough(boolean[] line, int i) {
        if (!line[i]) return null;
        int lo = i, hi = i;
        while (lo > 0 && line[lo - 1]) lo--;
        while (hi < line.length - 1 && line[hi + 1]) hi++;
        int[] counts = {0, 0, hi - lo + 1, 0, 0, (lo + hi + 1) / 2};
        int k = lo - 1;
        while (k >= 0 && !line[k]) { counts[1]++; k--; }
        while (k >= 0 && line[k]) { counts[0]++; k--; }
        k = hi + 1;
        while (k < line.length && !line[k]) { counts[3]++; k++; }
        while (k < line.length && line[k]) { counts[4]++; k++; }
        return counts;
    }

    private static int sum5(int[] c) { return c[0] + c[1] + c[2] + c[3] + c[4]; }

    private static int falseFinders(boolean[][] mod) {
        int size = mod.length;
        boolean[][] cols = new boolean[size][size];
        for (int y = 0; y < size; y++) for (int x = 0; x < size; x++) cols[x][y] = mod[y][x];
        java.util.Set<Long> found = new java.util.HashSet<>();
        for (int y = 0; y < size; y++) {
            boolean[] row = mod[y];
            for (int x = 0; x < size; x++) {
                if (!row[x] || (x > 0 && row[x - 1])) continue;
                int[] h = runsThrough(row, x);
                if (h == null || crossOk(h) < 0) continue;
                int cx = h[5];
                int[] v = runsThrough(cols[cx], y);
                if (v == null || crossOk(v) < 0) continue;
                if (5 * Math.abs(sum5(v) - sum5(h)) >= 2 * sum5(h)) continue;
                int cy = v[5];
                int[] h2 = runsThrough(mod[cy], cx);
                double m = h2 != null ? crossOk(h2) : -1;
                if (m < 0 || m < 0.75 || m > 1.35) continue;
                if ((cx < 9 && cy < 9) || (cx >= size - 9 && cy < 9) || (cx < 9 && cy >= size - 9)) continue;
                found.add(((long) cx << 32) | cy);
            }
        }
        return found.size();
    }
}
'''


def client_files(contract: Contract, package: str) -> dict[str, str]:
    """The client's generated Java, keyed by path under the app (``src/<package>/rose/``, reserved by
    rose.app_builder.appprofile). ``RoseSigninCore.java`` is OS Core's core, byte for byte at the pinned digest."""
    if not isinstance(package, str) or not PACKAGE.fullmatch(package) or any(p in JAVA_KEYWORDS for p in package.split('.')):
        raise ValueError(f'{package!r} is not a Java package name')
    core = SIGNIN_CORE.read_bytes()
    if hashlib.sha256(core).hexdigest() != SIGNIN_CORE_SHA256:
        raise RuntimeError('RoseSigninCore.java is not the pinned copy (docs/APP-BUILDER.md §7.2)')
    head = JAVA_HEADER.format(sha=contract.sha256)
    base = 'src/' + package.replace('.', '/') + '/rose/'
    types = [head + f'package {package}.rose;', '',
             '/** The contract\'s types (docs/APP-BUILDER.md §7.3). */',
             f'public final class {TYPES_CLASS} {{', f'    private {TYPES_CLASS}() {{}}', '']
    types += [_java_class(n, t['fields'], package) for n, t in sorted(contract.data['types'].items())]
    types.append('}\n')
    fill = lambda template: head + template.replace('{pkg}', package)
    return {base + 'RoseJson.java': fill(ROSE_JSON),
            base + 'RoseApi.java': fill(ROSE_API).replace('{methods}', _java_methods(contract, package)),
            base + f'{TYPES_CLASS}.java': '\n'.join(types),
            base + 'RoseSignin.java': fill(ROSE_SIGNIN),
            base + 'RoseInvite.java': fill(ROSE_INVITE),
            base + 'RoseSession.java': fill(ROSE_SESSION),
            base + 'RoseQr.java': fill(ROSE_QR),
            base + 'RoseSigninCore.java': core.decode('utf-8')}
