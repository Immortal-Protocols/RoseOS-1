//! Real HTTP transport for the candidate non-streaming wire shape.
//!
//! Policy:
//! - The request is `POST` to `Endpoint::request_url()` exactly; nothing else is appended.
//! - TLS certificates are verified against the platform trust store. Verification is never disabled.
//! - Plain `http://` is accepted only for loopback hosts (enforced by config validation, and
//!   `https_only` is set for every other host).
//! - Redirects are never followed; a 3xx is reported as `unexpected_status`.
//! - Loopback hosts never use a proxy. Other hosts use the standard proxy environment
//!   (`HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`, honouring `NO_PROXY`).
//! - The connect timeout and the whole-turn deadline cover DNS, connect, TLS, headers and body.
//! - Response bodies larger than `limits.max_response_bytes` fail with `response_too_large`.
//!
//! Each exchange runs on a worker thread. The caller polls for cancellation and the deadline every
//! `CANCEL_POLL` and returns as soon as either fires. The abandoned worker's socket is closed no
//! later than its own deadline, which is the same whole-turn deadline.

use std::io;
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use ureq::Agent;
use ureq::tls::{RootCerts, TlsConfig};

use crate::config::{Config, is_loopback_host, url_authority};
use crate::error::{ChatError, TimeoutPhase};
use crate::model::{ChatRequest, ChatResponse};
use crate::transport::{ChatTransport, SendContext};
use crate::wire;

/// How often a waiting caller checks for cancellation and the deadline.
pub const CANCEL_POLL: Duration = Duration::from_millis(20);

pub struct HttpTransport {
    agent: Agent,
    url: String,
    max_response_bytes: u64,
    /// HTTP status of the most recent exchange, if one was received. For evidence only.
    pub last_status: Option<u16>,
}

impl HttpTransport {
    pub fn new(config: &Config) -> HttpTransport {
        let url = config.endpoint.request_url();
        let loopback = is_loopback_host(url_authority(&url));
        let agent: Agent = Agent::config_builder()
            .http_status_as_error(false)
            .max_redirects(0)
            .max_redirects_will_error(false)
            .https_only(!loopback)
            .proxy(if loopback {
                None
            } else {
                ureq::Proxy::try_from_env()
            })
            .timeout_connect(Some(config.connect_timeout))
            .tls_config(
                TlsConfig::builder()
                    .root_certs(RootCerts::PlatformVerifier)
                    .build(),
            )
            .user_agent(concat!("rose-chat/", env!("CARGO_PKG_VERSION")))
            .build()
            .into();
        HttpTransport {
            agent,
            url,
            max_response_bytes: config.max_response_bytes,
            last_status: None,
        }
    }

    pub fn url(&self) -> &str {
        &self.url
    }
}

impl ChatTransport for HttpTransport {
    fn send(
        &mut self,
        request: &ChatRequest,
        ctx: &SendContext<'_>,
    ) -> Result<ChatResponse, ChatError> {
        self.last_status = None;
        let remaining = ctx.deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(ChatError::Timeout {
                phase: TimeoutPhase::Response,
            });
        }
        let body = wire::encode_request(request);
        let auth = ctx.auth.map(|s| format!("Bearer {}", s.expose()));
        let (agent, url, limit) = (
            self.agent.clone(),
            self.url.clone(),
            self.max_response_bytes,
        );
        let (tx, rx) = mpsc::channel();
        thread::Builder::new()
            .name("rose-chat-http".into())
            .spawn(move || {
                let _ = tx.send(exchange(&agent, &url, auth, body, remaining, limit));
            })
            .map_err(|e| {
                ChatError::Connect(format!("could not start request thread: {}", e.kind()))
            })?;

        loop {
            if ctx.cancel.is_cancelled() {
                return Err(ChatError::Cancelled);
            }
            let now = Instant::now();
            if now >= ctx.deadline {
                return Err(ChatError::Timeout {
                    phase: TimeoutPhase::Response,
                });
            }
            match rx.recv_timeout(CANCEL_POLL.min(ctx.deadline - now)) {
                Ok((status, result)) => {
                    self.last_status = status;
                    return result;
                }
                Err(RecvTimeoutError::Timeout) => continue,
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(ChatError::Connect(
                        "request thread ended without a result".into(),
                    ));
                }
            }
        }
    }
}

/// One POST and bounded body read. Returns the status if a response head arrived.
fn exchange(
    agent: &Agent,
    url: &str,
    auth: Option<String>,
    body: String,
    timeout: Duration,
    limit: u64,
) -> (Option<u16>, Result<ChatResponse, ChatError>) {
    let mut request = agent
        .post(url)
        .config()
        .timeout_global(Some(timeout))
        .build()
        .header("Content-Type", "application/json")
        .header("Accept", "application/json");
    if let Some(value) = auth {
        request = request.header("Authorization", value);
    }
    let mut response = match request.send(body.as_bytes()) {
        Ok(r) => r,
        Err(e) => return (None, Err(map_error(e, limit))),
    };
    let status = response.status().as_u16();
    let retry_after = response
        .headers()
        .get("retry-after")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let read = response.body_mut().with_config().limit(limit).read_to_vec();
    let bytes = match read {
        Ok(b) => b,
        // A failed body read matters for success bodies; an error status is reported as such.
        Err(e) if (200..300).contains(&status) => {
            return (Some(status), Err(map_body_error(e, limit)));
        }
        Err(_) => Vec::new(),
    };
    (
        Some(status),
        wire::decode_response(status, retry_after.as_deref(), &bytes),
    )
}

/// Errors before a response head arrived. Messages name a cause, never headers or bodies.
fn map_error(e: ureq::Error, limit: u64) -> ChatError {
    use ureq::Error as E;
    match e {
        E::Timeout(t) => ChatError::Timeout {
            phase: match t {
                ureq::Timeout::Resolve | ureq::Timeout::Connect => TimeoutPhase::Connect,
                _ => TimeoutPhase::Response,
            },
        },
        E::HostNotFound => ChatError::Connect("host not found".into()),
        E::ConnectionFailed => ChatError::Connect("connection failed".into()),
        E::ConnectProxyFailed(_) => ChatError::Connect("proxy CONNECT failed".into()),
        E::Io(io) => map_io(&io, "before a response"),
        E::Tls(m) => ChatError::Tls(m.to_string()),
        E::Rustls(_) => ChatError::Tls("handshake or certificate verification failed".into()),
        E::RequireHttpsOnly(_) | E::TlsRequired => {
            ChatError::Config("plain http:// is only allowed to loopback hosts".into())
        }
        E::BodyExceedsLimit(_) => ChatError::ResponseTooLarge { limit },
        E::LargeResponseHeader(..) => {
            ChatError::MalformedResponse("response headers too large".into())
        }
        E::Protocol(_) => ChatError::MalformedResponse("invalid HTTP response".into()),
        _ => ChatError::Connect("HTTP transport error".into()),
    }
}

/// Errors while reading a success body.
fn map_body_error(e: ureq::Error, limit: u64) -> ChatError {
    match e {
        ureq::Error::BodyExceedsLimit(_) => ChatError::ResponseTooLarge { limit },
        ureq::Error::Timeout(_) => ChatError::Timeout {
            phase: TimeoutPhase::Response,
        },
        ureq::Error::Io(io) if io.kind() == io::ErrorKind::TimedOut => ChatError::Timeout {
            phase: TimeoutPhase::Response,
        },
        _ => ChatError::MalformedResponse("response body ended early or was invalid".into()),
    }
}

fn map_io(e: &io::Error, when: &str) -> ChatError {
    match e.kind() {
        io::ErrorKind::ConnectionRefused => ChatError::Connect("connection refused".into()),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => ChatError::Timeout {
            phase: TimeoutPhase::Response,
        },
        kind => ChatError::Connect(format!("connection closed {when} ({kind})")),
    }
}
