//! The rose-signin adapter for the Rose app runtime (docs/APP-BUILDER.md §7.4, slice 7b). Source only: nothing
//! builds a server on it yet.
//!
//! - [`auth`]: [`auth::SigninAuth`], the runtime's [`rose_app_runtime::signin::Auth`] over rose-signin's
//!   `Authenticator<SqliteStore, AeadSealer>`, fixed to one app (`validate_for` / `revoke_for`). Handles, tokens and
//!   invites cross HTTP as rose-signin base32 strings.
//! - [`data`]: [`data::AppData`], one SQLite file per account named by a hash of (app, account) under `DATA_DIR`,
//!   plus the declared shared database, each migrated forward only from the builder-embedded list; a database from
//!   a newer schema is refused, never downgraded.
//! - [`identity`]: `revision_sha256` measured from the server's own binary and the host's `CHARIOT_DEPLOYMENT_SHA256`
//!   echoed (§7.7), the host's `CHARIOT_APP_ID`, and an optional `CHARIOT_REVISION_SHA256` the binary must match.
//! - [`keys`]: the sealing keyring the host delivers in `CHARIOT_SEALING_KEY_FILE` (`rose-signin-keyring/v1`),
//!   parsed into `AeadSealer` keys; any problem leaves the server without a key and not ready.
//! - [`mod@start`]: opens the store, applies every migration and returns what a generated `main` hands the runtime.
//!
//! Readiness: the server is ready only with a delivered keyring ([`keys`]), its migrations done and no restore
//! indicated (Chariot's restore marker absent, [`start::Restore`]). A present marker keeps it not ready: no restore
//! executor or completion is agreed, so the adapter never runs, reads or removes one. The adapter invents no key,
//! app id or deployment hash.

pub mod auth;
pub mod data;
pub mod identity;
pub mod invite;
pub mod keys;
pub mod start;

/// The SQLite binding [`AppData`]'s connections come from (rusqlite 0.32, bundled; the version in rose-signin's lock).
/// Generated handlers name it through this path, `rose_app_runtime_signin::rusqlite::{Connection, params}`, so a
/// server never declares, or mismatches, the version itself.
pub use rusqlite;

pub use auth::SigninAuth;
pub use data::{AppData, DataError, Migrations};
pub use invite::{InviteError, invite};
pub use keys::KeyError;
pub use start::{Config, Keys, Restore, StartError, Started, start};
