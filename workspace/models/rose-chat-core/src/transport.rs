use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use crate::auth::Secret;
use crate::error::ChatError;
use crate::model::{ChatRequest, ChatResponse};

/// Shared flag a caller sets to abandon an in-flight turn.
#[derive(Clone, Debug, Default)]
pub struct CancelToken(Arc<AtomicBool>);

impl CancelToken {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn cancel(&self) {
        self.0.store(true, Ordering::SeqCst);
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
}

/// Per-request limits and inputs handed to a transport.
pub struct SendContext<'a> {
    /// The whole request must complete by this instant.
    pub deadline: Instant,
    pub connect_timeout: Duration,
    pub cancel: &'a CancelToken,
    /// Bearer credential, if configured. Transports must not log it.
    pub auth: Option<&'a Secret>,
}

/// The boundary between the chat core and whatever carries a request to a model.
///
/// Implementations should return `Timeout`, `Cancelled` and HTTP-class errors themselves. The
/// session still discards replies that arrive after the deadline or after cancellation, so a
/// transport that ignores them cannot corrupt the history.
pub trait ChatTransport {
    fn send(
        &mut self,
        request: &ChatRequest,
        ctx: &SendContext<'_>,
    ) -> Result<ChatResponse, ChatError>;
}
