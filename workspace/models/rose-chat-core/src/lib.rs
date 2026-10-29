//! Rose Models chat core.
//!
//! Configuration, ordered multi-turn history, an injected transport boundary and a real
//! non-streaming HTTP transport for the candidate wire shape. Tests run against in-process and
//! loopback FIXTURES, which prove client behaviour only, not any model or endpoint.

pub mod agent;
pub mod auth;
pub mod config;
pub mod error;
pub mod fixture;
#[cfg(feature = "http")]
pub mod http;
pub mod model;
pub mod session;
pub mod transcript;
pub mod transport;
pub mod wire;

pub use auth::{AuthMode, Secret};
pub use config::{Config, Endpoint, Overrides, Protocol};
pub use error::{ChatError, TimeoutPhase};
#[cfg(feature = "http")]
pub use http::HttpTransport;
pub use model::{ChatRequest, ChatResponse, FinishReason, Message, ModelId, Reply, Role, Usage};
pub use session::ChatSession;
pub use transport::{CancelToken, ChatTransport, SendContext};
