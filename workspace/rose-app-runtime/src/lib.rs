//! The runtime a Rose-generated server is built on (docs/APP-BUILDER.md §7.4). Source only: nothing builds a server
//! on it yet.
//!
//! This crate is the standard-library core, with no dependencies, so it builds and tests offline anywhere:
//! - [`http`]: a bounded HTTP/1.1 request reader and response writer (one request per connection, no chunked
//!   bodies, so nothing can be smuggled between requests);
//! - [`json`]: strict JSON (no repeated keys, bounded depth, finite numbers);
//! - [`signin`]: the `rose-signin-v1` routes (docs/ROSE-SIGNIN-V1.md) mapped onto an injected [`signin::Auth`];
//! - [`server`]: the app's routes, the account check on every account route, readiness at `/health` and logs
//!   that never hold bodies, tokens, passphrases, codes, keys or account names.
//!
//! The authenticator itself is rose-signin's (OS Core's), bound in by an adapter that uses rose-signin's own
//! locked dependencies; the sealing key, the hosted app id and the deployment hash come from the host (§7.7). This
//! crate invents none of them.

pub mod http;
pub mod json;
pub mod server;
pub mod signin;
