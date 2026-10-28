use std::fmt;
use std::time::Duration;

use serde::Serialize;

use crate::error::ChatError;

/// Opaque model identifier, taken verbatim from configuration.
///
/// There is deliberately no default, alias table or normalization: the identifier sent to the
/// endpoint is exactly what the operator configured.
#[derive(Clone, PartialEq, Eq, Hash, Serialize)]
#[serde(transparent)]
pub struct ModelId(String);

impl ModelId {
    pub fn new(id: impl Into<String>) -> Result<Self, ChatError> {
        let id = id.into();
        if id.trim().is_empty() {
            return Err(ChatError::Config(
                "model.id is required and must not be blank; there is no default model".into(),
            ));
        }
        if id.trim() != id {
            return Err(ChatError::Config(
                "model.id has leading or trailing whitespace; it is used verbatim, so remove it"
                    .into(),
            ));
        }
        if id.chars().any(char::is_control) {
            return Err(ChatError::Config(
                "model.id contains control characters".into(),
            ));
        }
        Ok(ModelId(id))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for ModelId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "ModelId({:?})", self.0)
    }
}

impl fmt::Display for ModelId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    System,
    User,
    Assistant,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Message {
    pub role: Role,
    pub content: String,
}

impl Message {
    pub fn new(role: Role, content: impl Into<String>) -> Self {
        Message {
            role,
            content: content.into(),
        }
    }
}

/// One non-streaming request: the full ordered history plus the new user message.
#[derive(Clone, Debug, PartialEq)]
pub struct ChatRequest {
    pub model: ModelId,
    pub messages: Vec<Message>,
    pub max_tokens: u32,
    pub temperature: Option<f32>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FinishReason {
    Stop,
    /// The reply hit `max_tokens` (or the server's own limit) and is incomplete.
    Length,
    Other(String),
    /// The endpoint did not say why generation ended.
    Unreported,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Usage {
    pub prompt_tokens: Option<u64>,
    pub completion_tokens: Option<u64>,
}

/// What a transport returns before the session validates it.
#[derive(Clone, Debug, PartialEq)]
pub struct ChatResponse {
    /// `None` when the endpoint sent no content or an explicit null.
    pub content: Option<String>,
    pub finish_reason: FinishReason,
    /// Model name as reported by the endpoint, if it reported one.
    pub served_model: Option<String>,
    pub request_id: Option<String>,
    pub usage: Option<Usage>,
    /// Separate reasoning text, if the server returned one. Never used as the answer and never
    /// added to the history.
    pub reasoning: Option<String>,
}

/// A validated assistant turn, already committed to the history.
#[derive(Clone, Debug, PartialEq)]
pub struct Reply {
    pub content: String,
    pub finish_reason: FinishReason,
    /// True when `finish_reason` is `Length`: the text is kept but is incomplete.
    pub truncated: bool,
    pub served_model: Option<String>,
    pub request_id: Option<String>,
    pub usage: Option<Usage>,
    /// Character count of any separate reasoning text the server returned. The text itself is
    /// not kept, shown or replayed.
    pub reasoning_chars: Option<usize>,
    pub latency: Duration,
}
