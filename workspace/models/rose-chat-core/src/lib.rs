//! Rose Models chat core.
//!
//! Configuration, ordered multi-turn history and an injected transport boundary for a later
//! real chat with the selected served model. This build has no network transport: its tests
//! and demo use the in-process FIXTURE transport, which proves client behaviour only.

pub mod auth;
pub mod config;
pub mod error;
pub mod fixture;
pub mod model;
pub mod session;
pub mod transport;
pub mod wire;

pub use auth::{AuthMode, Secret};
pub use config::{Config, Overrides, Protocol};
pub use error::{ChatError, TimeoutPhase};
pub use model::{ChatRequest, ChatResponse, FinishReason, Message, ModelId, Reply, Role, Usage};
pub use session::ChatSession;
pub use transport::{CancelToken, ChatTransport, SendContext};
