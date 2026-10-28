//! Candidate non-streaming request/response shape (OpenAI-compatible chat completions).
//!
//! This is a test shape that many open-weight serving runtimes accept. It is NOT a claim about
//! the final serving runtime or the Agent Platform invocation API; Models Core confirms that.
//! Only pure encoding and decoding live here. No HTTP client is included in this build.

use std::time::Duration;

use serde_json::{Map, Value, json};

use crate::error::{ChatError, TimeoutPhase};
use crate::model::{ChatRequest, ChatResponse, FinishReason, Usage};

/// Request body for `POST` to the configured request URL.
pub fn encode_request(request: &ChatRequest) -> String {
    let mut body = json!({
        "model": request.model,
        "messages": request.messages,
        "max_tokens": request.max_tokens,
        "stream": false,
    });
    if let Some(t) = request.temperature {
        body["temperature"] = json!(t);
    }
    body.to_string()
}

/// Map an HTTP status, optional `Retry-After` header and body to a response or error.
///
/// Error variants never include body text, so a server echoing a credential cannot leak it.
pub fn decode_response(
    status: u16,
    retry_after: Option<&str>,
    body: &[u8],
) -> Result<ChatResponse, ChatError> {
    match status {
        200..=299 => decode_success(body),
        401 => Err(ChatError::Unauthorized { status }),
        404 => Err(ChatError::NotFound { status }),
        403 => Err(ChatError::Forbidden { status }),
        408 => Err(ChatError::Timeout {
            phase: TimeoutPhase::Response,
        }),
        429 => Err(ChatError::RateLimited {
            retry_after: retry_after
                .and_then(|v| v.trim().parse::<u64>().ok())
                .map(Duration::from_secs),
        }),
        400 | 413 | 422 if is_context_length_error(body) => {
            Err(ChatError::ServerContextLimit { status })
        }
        400 | 413 | 422 => Err(ChatError::BadRequest { status }),
        500..=599 => Err(ChatError::Server { status }),
        _ => Err(ChatError::UnexpectedStatus { status }),
    }
}

fn decode_success(body: &[u8]) -> Result<ChatResponse, ChatError> {
    let value: Value = serde_json::from_slice(body)
        .map_err(|_| ChatError::MalformedResponse("body is not valid JSON".into()))?;
    let root = value
        .as_object()
        .ok_or_else(|| ChatError::MalformedResponse("body is not a JSON object".into()))?;
    let choice = root
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|c| c.first())
        .and_then(Value::as_object)
        .ok_or_else(|| ChatError::MalformedResponse("missing choices[0]".into()))?;
    let message = choice
        .get("message")
        .and_then(Value::as_object)
        .ok_or_else(|| ChatError::MalformedResponse("missing choices[0].message".into()))?;
    let content = match message.get("content") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(_) => {
            return Err(ChatError::MalformedResponse(
                "choices[0].message.content is not a string".into(),
            ));
        }
    };
    // vLLM reasoning parsers return thinking separately; keep it apart from the answer.
    let reasoning = match optional_str(message, "reasoning")? {
        Some(r) => Some(r.to_string()),
        None => optional_str(message, "reasoning_content")?.map(str::to_string),
    };
    let finish_reason = match optional_str(choice, "finish_reason")? {
        None => FinishReason::Unreported,
        Some("stop") => FinishReason::Stop,
        Some("length") => FinishReason::Length,
        Some(other) => FinishReason::Other(other.to_string()),
    };
    let usage = match root.get("usage") {
        None | Some(Value::Null) => None,
        Some(Value::Object(u)) => Some(Usage {
            prompt_tokens: u.get("prompt_tokens").and_then(Value::as_u64),
            completion_tokens: u.get("completion_tokens").and_then(Value::as_u64),
        }),
        Some(_) => {
            return Err(ChatError::MalformedResponse(
                "usage is not an object".into(),
            ));
        }
    };
    Ok(ChatResponse {
        content,
        finish_reason,
        served_model: optional_str(root, "model")?.map(str::to_string),
        request_id: optional_str(root, "id")?.map(str::to_string),
        usage,
        reasoning,
    })
}

fn optional_str<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<Option<&'a str>, ChatError> {
    match object.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s)),
        Some(_) => Err(ChatError::MalformedResponse(format!(
            "{key} is not a string"
        ))),
    }
}

/// Recognizes the common `context_length_exceeded` code or a "context length" message.
fn is_context_length_error(body: &[u8]) -> bool {
    let Ok(value) = serde_json::from_slice::<Value>(body) else {
        return false;
    };
    let error = value.get("error").unwrap_or(&value);
    let code = error.get("code").and_then(Value::as_str).unwrap_or("");
    let message = error
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    code == "context_length_exceeded"
        || message.contains("context length")
        || message.contains("context window")
}
