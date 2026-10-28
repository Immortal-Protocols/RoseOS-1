//! In-process FIXTURE transport. It proves client behaviour only: no socket, no endpoint and
//! no model is involved, and its replies are scripted synthetic text.

use std::collections::VecDeque;
use std::thread;
use std::time::Duration;

use crate::error::ChatError;
use crate::model::{ChatRequest, ChatResponse, FinishReason};
use crate::transport::{ChatTransport, SendContext};
use crate::wire;

/// One scripted outcome, consumed per `send`.
#[derive(Clone, Debug)]
pub enum Step {
    /// Return this response as-is.
    Respond(ChatResponse),
    /// Return this error as-is.
    Fail(ChatError),
    /// Encode the request with the candidate wire shape, then decode this status/body.
    Wire {
        status: u16,
        retry_after: Option<String>,
        body: String,
    },
    /// Sleep, ignoring the deadline, then run the inner step (a misbehaving transport).
    Delayed(Duration, Box<Step>),
    /// Set the caller's cancel token, then run the inner step (cancel while in flight).
    CancelDuring(Box<Step>),
}

impl Step {
    /// A successful reply with `finish_reason = stop` and no reported model.
    pub fn reply(text: &str) -> Step {
        Step::Respond(ChatResponse {
            content: Some(text.to_string()),
            finish_reason: FinishReason::Stop,
            served_model: None,
            request_id: None,
            usage: None,
            reasoning: None,
        })
    }

    /// A 200 candidate-shape body carrying `text` and reporting `model`.
    pub fn wire_ok(model: &str, text: &str) -> Step {
        let body = serde_json::json!({
            "id": "fixture-response",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        });
        Step::Wire {
            status: 200,
            retry_after: None,
            body: body.to_string(),
        }
    }
}

/// Plays a script and records every request it received.
#[derive(Default)]
pub struct ScriptedTransport {
    steps: VecDeque<Step>,
    /// Requests in arrival order.
    pub requests: Vec<ChatRequest>,
    /// Candidate-shape JSON bodies, for `Step::Wire` steps only.
    pub wire_bodies: Vec<String>,
    /// Whether a credential was present for each request. The value itself is never recorded.
    pub auth_present: Vec<bool>,
}

impl ScriptedTransport {
    pub fn new(steps: impl IntoIterator<Item = Step>) -> Self {
        ScriptedTransport {
            steps: steps.into_iter().collect(),
            ..Default::default()
        }
    }

    pub fn remaining(&self) -> usize {
        self.steps.len()
    }

    fn run(
        &mut self,
        step: Step,
        request: &ChatRequest,
        ctx: &SendContext<'_>,
    ) -> Result<ChatResponse, ChatError> {
        match step {
            Step::Respond(r) => Ok(r),
            Step::Fail(e) => Err(e),
            Step::Wire {
                status,
                retry_after,
                body,
            } => {
                self.wire_bodies.push(wire::encode_request(request));
                wire::decode_response(status, retry_after.as_deref(), body.as_bytes())
            }
            Step::Delayed(d, inner) => {
                thread::sleep(d);
                self.run(*inner, request, ctx)
            }
            Step::CancelDuring(inner) => {
                ctx.cancel.cancel();
                self.run(*inner, request, ctx)
            }
        }
    }
}

impl ChatTransport for ScriptedTransport {
    fn send(
        &mut self,
        request: &ChatRequest,
        ctx: &SendContext<'_>,
    ) -> Result<ChatResponse, ChatError> {
        self.requests.push(request.clone());
        self.auth_present.push(ctx.auth.is_some());
        let step = self
            .steps
            .pop_front()
            .expect("fixture script exhausted: the test sent more turns than it scripted");
        self.run(step, request, ctx)
    }
}
