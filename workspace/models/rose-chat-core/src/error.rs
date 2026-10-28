use std::fmt;
use std::time::Duration;

/// Which part of a request ran out of time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeoutPhase {
    /// No connection was established within `timeouts.connect_ms`.
    Connect,
    /// The request was sent but no complete reply arrived before the request deadline.
    Response,
}

/// Every way a chat turn can fail. A failed turn never changes the conversation history.
///
/// Messages carried by these variants are structural descriptions written by this crate.
/// They never contain credential values or response bodies.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ChatError {
    /// Configuration is missing, invalid or unsupported in this build. Raised before any I/O.
    Config(String),
    /// The caller's input was rejected before any I/O (for example, a blank user message).
    InvalidInput(String),
    /// The client-side character guard rejected the request before sending it.
    ContextLimit {
        used_chars: usize,
        budget_chars: usize,
    },
    /// The server rejected the request as exceeding the model's context window.
    ServerContextLimit {
        status: u16,
    },
    /// A configured credential could not be resolved locally.
    Auth(String),
    /// The endpoint could not be reached (refused, DNS failure, reset before a response).
    Connect(String),
    Timeout {
        phase: TimeoutPhase,
    },
    /// The caller cancelled the turn; any reply that arrived is discarded.
    Cancelled,
    /// HTTP 401.
    Unauthorized {
        status: u16,
    },
    /// HTTP 403.
    Forbidden {
        status: u16,
    },
    /// HTTP 429. `retry_after` is set only when the server sent `Retry-After` in seconds.
    RateLimited {
        retry_after: Option<Duration>,
    },
    /// HTTP 400, 413 or 422 that is not a context-length rejection.
    BadRequest {
        status: u16,
    },
    /// HTTP 5xx.
    Server {
        status: u16,
    },
    /// HTTP 404: the request URL does not exist on the server (check the endpoint URL).
    NotFound {
        status: u16,
    },
    /// Any other status, including 3xx: redirects are never followed.
    UnexpectedStatus {
        status: u16,
    },
    /// A success status whose body does not have the expected shape, or a body that ended early.
    MalformedResponse(String),
    /// The response body exceeded `limits.max_response_bytes`.
    ResponseTooLarge {
        limit: u64,
    },
    /// TLS setup or certificate verification failed. Verification is never disabled.
    Tls(String),
    /// The reply had no assistant text (missing, null, empty or whitespace only).
    EmptyCompletion,
    /// The endpoint reported serving a model other than the configured `model.id`.
    ModelIdentityMismatch {
        configured: String,
        served: String,
    },
}

impl ChatError {
    /// Stable machine-readable name, used in CLI output and test reports.
    pub fn kind(&self) -> &'static str {
        match self {
            ChatError::Config(_) => "config",
            ChatError::InvalidInput(_) => "invalid_input",
            ChatError::ContextLimit { .. } => "context_limit",
            ChatError::ServerContextLimit { .. } => "server_context_limit",
            ChatError::Auth(_) => "auth",
            ChatError::Connect(_) => "connect",
            ChatError::Timeout {
                phase: TimeoutPhase::Connect,
            } => "timeout_connect",
            ChatError::Timeout {
                phase: TimeoutPhase::Response,
            } => "timeout_response",
            ChatError::Cancelled => "cancelled",
            ChatError::Unauthorized { .. } => "unauthorized",
            ChatError::Forbidden { .. } => "forbidden",
            ChatError::RateLimited { .. } => "rate_limited",
            ChatError::BadRequest { .. } => "bad_request",
            ChatError::Server { .. } => "server",
            ChatError::NotFound { .. } => "not_found",
            ChatError::ResponseTooLarge { .. } => "response_too_large",
            ChatError::Tls(_) => "tls",
            ChatError::UnexpectedStatus { .. } => "unexpected_status",
            ChatError::MalformedResponse(_) => "malformed_response",
            ChatError::EmptyCompletion => "empty_completion",
            ChatError::ModelIdentityMismatch { .. } => "model_identity_mismatch",
        }
    }

    /// Whether repeating the same turn later could succeed without changing the request.
    /// This is classification only: the session never retries on its own.
    pub fn is_retryable(&self) -> bool {
        matches!(
            self,
            ChatError::Connect(_)
                | ChatError::Timeout { .. }
                | ChatError::RateLimited { .. }
                | ChatError::Server { .. }
        )
    }
}

impl fmt::Display for ChatError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ChatError::Config(m) => write!(f, "configuration error: {m}"),
            ChatError::InvalidInput(m) => write!(f, "invalid input: {m}"),
            ChatError::ContextLimit {
                used_chars,
                budget_chars,
            } => write!(
                f,
                "request has {used_chars} characters, over the client budget of {budget_chars}"
            ),
            ChatError::ServerContextLimit { status } => {
                write!(f, "server rejected the request as too long (HTTP {status})")
            }
            ChatError::Auth(m) => write!(f, "credential unavailable: {m}"),
            ChatError::Connect(m) => write!(f, "could not reach the endpoint: {m}"),
            ChatError::Timeout {
                phase: TimeoutPhase::Connect,
            } => {
                write!(f, "timed out connecting to the endpoint")
            }
            ChatError::Timeout {
                phase: TimeoutPhase::Response,
            } => {
                write!(f, "timed out waiting for the reply")
            }
            ChatError::Cancelled => write!(f, "cancelled"),
            ChatError::Unauthorized { status } => write!(f, "unauthorized (HTTP {status})"),
            ChatError::Forbidden { status } => write!(f, "forbidden (HTTP {status})"),
            ChatError::RateLimited {
                retry_after: Some(d),
            } => {
                write!(f, "rate limited; retry after {}s", d.as_secs())
            }
            ChatError::RateLimited { retry_after: None } => write!(f, "rate limited"),
            ChatError::BadRequest { status } => write!(f, "request rejected (HTTP {status})"),
            ChatError::Server { status } => write!(f, "server error (HTTP {status})"),
            ChatError::UnexpectedStatus { status } => write!(f, "unexpected HTTP status {status}"),
            ChatError::NotFound { status } => {
                write!(
                    f,
                    "request URL not found (HTTP {status}); check the endpoint URL"
                )
            }
            ChatError::ResponseTooLarge { limit } => {
                write!(f, "response body exceeded the {limit}-byte limit")
            }
            ChatError::Tls(m) => write!(f, "TLS failure: {m}"),
            ChatError::MalformedResponse(m) => write!(f, "malformed response: {m}"),
            ChatError::EmptyCompletion => write!(f, "the reply contained no assistant text"),
            ChatError::ModelIdentityMismatch { configured, served } => write!(
                f,
                "endpoint reported model {served:?} but the configured model is {configured:?}"
            ),
        }
    }
}

impl std::error::Error for ChatError {}
