use std::time::{Duration, Instant};

use crate::auth::Secret;
use crate::config::Config;
use crate::error::{ChatError, TimeoutPhase};
use crate::model::{ChatRequest, FinishReason, Message, ModelId, Reply, Role};
use crate::transport::{CancelToken, ChatTransport, SendContext};

/// A multi-turn conversation over one transport.
///
/// Each `send` builds a request from the full history plus the new user message. The user
/// message and assistant reply are committed together only when the turn succeeds; any error
/// leaves the history exactly as it was, so a failed turn can be repeated without duplicating
/// or reordering context. The session never retries on its own.
pub struct ChatSession<T: ChatTransport> {
    model: ModelId,
    max_tokens: u32,
    temperature: Option<f32>,
    context_budget_chars: usize,
    connect_timeout: Duration,
    request_timeout: Duration,
    auth: Option<Secret>,
    history: Vec<Message>,
    transport: T,
}

impl<T: ChatTransport> ChatSession<T> {
    /// `auth` is the already-resolved credential (see `AuthMode::resolve`), if any.
    pub fn new(config: &Config, transport: T, auth: Option<Secret>) -> Self {
        let history = config
            .system_prompt
            .iter()
            .map(|p| Message::new(Role::System, p.clone()))
            .collect();
        ChatSession {
            model: config.model.clone(),
            max_tokens: config.max_tokens,
            temperature: config.temperature,
            context_budget_chars: config.context_budget_chars,
            connect_timeout: config.connect_timeout,
            request_timeout: config.request_timeout,
            auth,
            history,
            transport,
        }
    }

    /// Committed messages, oldest first, including any system prompt.
    pub fn history(&self) -> &[Message] {
        &self.history
    }

    pub fn model(&self) -> &ModelId {
        &self.model
    }

    pub fn transport(&self) -> &T {
        &self.transport
    }

    pub fn send(&mut self, text: &str) -> Result<Reply, ChatError> {
        self.send_with_cancel(text, &CancelToken::new())
    }

    pub fn send_with_cancel(
        &mut self,
        text: &str,
        cancel: &CancelToken,
    ) -> Result<Reply, ChatError> {
        if text.trim().is_empty() {
            return Err(ChatError::InvalidInput("user message is blank".into()));
        }
        if cancel.is_cancelled() {
            return Err(ChatError::Cancelled);
        }

        let mut messages = self.history.clone();
        messages.push(Message::new(Role::User, text));
        let used_chars: usize = messages.iter().map(|m| m.content.chars().count()).sum();
        if used_chars > self.context_budget_chars {
            return Err(ChatError::ContextLimit {
                used_chars,
                budget_chars: self.context_budget_chars,
            });
        }

        let request = ChatRequest {
            model: self.model.clone(),
            messages,
            max_tokens: self.max_tokens,
            temperature: self.temperature,
        };
        let started = Instant::now();
        let deadline = started + self.request_timeout;
        let ctx = SendContext {
            deadline,
            connect_timeout: self.connect_timeout,
            cancel,
            auth: self.auth.as_ref(),
        };
        let response = self.transport.send(&request, &ctx)?;
        let latency = started.elapsed();

        if cancel.is_cancelled() {
            return Err(ChatError::Cancelled);
        }
        if Instant::now() > deadline {
            return Err(ChatError::Timeout {
                phase: TimeoutPhase::Response,
            });
        }
        if let Some(served) = &response.served_model
            && served != self.model.as_str()
        {
            return Err(ChatError::ModelIdentityMismatch {
                configured: self.model.as_str().to_string(),
                served: served.clone(),
            });
        }
        let content = match response.content {
            Some(c) if !c.trim().is_empty() => c,
            _ => return Err(ChatError::EmptyCompletion),
        };

        let ChatRequest { mut messages, .. } = request;
        messages.push(Message::new(Role::Assistant, content.clone()));
        self.history = messages;
        Ok(Reply {
            content,
            truncated: response.finish_reason == FinishReason::Length,
            finish_reason: response.finish_reason,
            served_model: response.served_model,
            request_id: response.request_id,
            usage: response.usage,
            latency,
        })
    }
}
