//! `local-agent-v1`: the pure on-device agent state machine for the Rose Models Android app.
//!
//! The host (Java on Android) owns HTTPS, platform trust, timers, threads and app launch. It feeds
//! this machine [`Input`]s and carries out the [`Output`]s it returns. Nothing here does I/O or
//! reads a clock, so every transition is testable with fixtures.
//!
//! Rules the machine enforces:
//! - one turn at a time; at most two inference requests and one tool action per turn;
//! - the model may answer with exactly one closed JSON envelope: final text, or `open_app` for an
//!   app id on the local allowlist. Anything else (prose, code fences, arrays, unknown types or
//!   fields, a second action) fails the turn with zero execution;
//! - only a platform [`Input::ToolResult`] establishes what happened. Model text cannot claim an
//!   execution, and user text is sent as a JSON string inside a `user_message` event, so it cannot
//!   pose as a `tool_result` event;
//! - every request and tool dispatch carries a turn id and a generation; inputs that do not match
//!   the live one are rejected without changing state, so late, stale or repeated results are inert.
//!
//! See `workspace/models/contracts/local-agent-v1.md` for the host-side contract.

use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

use crate::error::ChatError;
use crate::model::{ChatRequest, FinishReason, Message, ModelId, Role};
use crate::wire;

pub const CONTRACT: &str = "local-agent-v1";
pub const TOOL_OPEN_APP: &str = "open_app";
/// Inference requests per user turn: the first answer, plus one after a tool result.
pub const MAX_INFERENCES_PER_TURN: u8 = 2;
/// Tool actions per user turn.
pub const MAX_ACTIONS_PER_TURN: u8 = 1;
const MAX_ID_CHARS: usize = 64;
/// Recent turn ids kept to reject reuse.
const RECENT_TURN_IDS: usize = 64;

/// Host-supplied configuration, fixed for the agent's lifetime.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentConfig {
    /// Must be `local-agent-v1`.
    pub contract: String,
    /// Exact served model id; replies reporting any other model fail the turn.
    pub model: String,
    /// Operator text placed before the agent instructions (for example `Reasoning strength: low`).
    #[serde(default)]
    pub system_prompt: Option<String>,
    /// Apps the phone resolved locally. The model only ever sees and returns these opaque ids.
    pub allowlist: Vec<AllowedApp>,
    #[serde(default)]
    pub limits: Limits,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AllowedApp {
    /// Opaque id: lowercase ASCII letter, then up to 31 of `a-z`, `0-9`, `_`.
    pub app_id: String,
    pub label: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Limits {
    /// `max_tokens` sent with every request.
    pub max_tokens: u32,
    pub max_user_chars: usize,
    /// Characters across every message of one request, checked before it is emitted.
    pub context_budget_chars: usize,
    /// Characters of assistant content accepted from one reply.
    pub max_reply_chars: usize,
    /// Passed to the host with each request; the host enforces it.
    pub inference_timeout_ms: u64,
    /// Passed to the host with each tool dispatch; the host reports `failed` when it expires.
    pub tool_timeout_ms: u64,
}

impl Default for Limits {
    fn default() -> Self {
        Limits {
            max_tokens: 512,
            max_user_chars: 4_000,
            context_budget_chars: 20_000,
            max_reply_chars: 8_000,
            inference_timeout_ms: 120_000,
            tool_timeout_ms: 10_000,
        }
    }
}

/// Everything the host may tell the agent.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Input {
    /// The user pressed Send. `turn_id` is chosen by the host and must not repeat.
    StartTurn { turn_id: String, text: String },
    /// The HTTP response for a `need_inference`, passed through unparsed.
    InferenceResponse {
        turn_id: String,
        generation: u64,
        status: u16,
        #[serde(default)]
        retry_after: Option<String>,
        body: String,
    },
    /// The request for a `need_inference` produced no HTTP response.
    InferenceFailed {
        turn_id: String,
        generation: u64,
        kind: TransportFailure,
    },
    /// What the platform actually did for a `need_tool`. Exactly one per dispatch, including
    /// `cancelled` when the host's own turn check blocked the launch.
    ToolResult {
        turn_id: String,
        generation: u64,
        action_id: String,
        status: ToolStatus,
    },
    /// Stop.
    Cancel { turn_id: String },
    /// The activity left the foreground (for example because the launched app took over).
    Backgrounded,
    /// The user explicitly continued a parked turn after returning.
    Continue { turn_id: String },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransportFailure {
    Connect,
    TimeoutConnect,
    TimeoutResponse,
    Tls,
    ResponseTooLarge,
}

impl TransportFailure {
    fn kind(self) -> &'static str {
        match self {
            TransportFailure::Connect => "connect",
            TransportFailure::TimeoutConnect => "timeout_connect",
            TransportFailure::TimeoutResponse => "timeout_response",
            TransportFailure::Tls => "tls",
            TransportFailure::ResponseTooLarge => "response_too_large",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolStatus {
    /// The launch Intent was accepted. Not proof that the app rendered.
    LaunchRequested,
    NotFound,
    Locked,
    NotAllowed,
    Cancelled,
    Failed,
}

/// Everything the agent may ask of the host.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Output {
    /// POST `body` to the configured chat-completions URL; answer with exactly one
    /// `inference_response` or `inference_failed` carrying this turn id and generation.
    NeedInference {
        turn_id: String,
        generation: u64,
        body: String,
        timeout_ms: u64,
    },
    /// Abort the in-flight request with this generation. Any later reply for it is rejected.
    CancelInference {
        turn_id: String,
        generation: u64,
    },
    /// Launch the locally resolved app for `app_id`, only if this turn is still the active one
    /// and the activity is foreground and unlocked; answer with exactly one `tool_result`.
    NeedTool {
        turn_id: String,
        generation: u64,
        action_id: String,
        tool: String,
        app_id: String,
        timeout_ms: u64,
    },
    Render {
        turn_id: String,
        event: RenderEvent,
    },
    /// The turn is over; no further output carries this turn id except a late receipt for a
    /// launch already dispatched before Stop.
    TurnEnded {
        turn_id: String,
        outcome: Outcome,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    /// The input did not match the live state and was ignored; nothing changed.
    Rejected {
        reason: String,
    },
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum RenderEvent {
    /// `thinking`, `awaiting_app` or `parked`.
    Status {
        state: String,
    },
    AssistantText {
        text: String,
    },
    ToolProposed {
        action_id: String,
        app_id: String,
        label: String,
    },
    ToolResult {
        action_id: String,
        app_id: String,
        status: ToolStatus,
        /// The receipt arrived after Stop: the launch may have happened and cannot be undone.
        after_stop: bool,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Completed,
    Cancelled,
    Failed,
    /// A parked turn was closed by a new Send; its tool receipt stays in the history.
    Superseded,
}

/// The model's closed answer envelope.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Final { text: String },
    OpenApp { app_id: String },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    rose_action: String,
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    text: Option<String>,
    #[serde(default)]
    app_id: Option<String>,
}

/// Parse assistant content as exactly one `local-agent-v1` envelope. Returns an error kind.
///
/// The whole content (surrounding whitespace aside) must be one JSON object with no duplicate or
/// unknown fields. Nothing is extracted from prose or code blocks.
pub fn parse_action(content: &str, allowlist: &[AllowedApp]) -> Result<Action, &'static str> {
    let content = content.trim();
    if !content.starts_with('{') {
        return Err("malformed_action");
    }
    let envelope: Envelope = serde_json::from_str(content).map_err(|_| "malformed_action")?;
    if envelope.rose_action != CONTRACT {
        return Err("malformed_action");
    }
    match (envelope.kind.as_str(), envelope.text, envelope.app_id) {
        ("final", Some(text), None) if !text.trim().is_empty() => Ok(Action::Final { text }),
        ("final", _, _) => Err("malformed_action"),
        (TOOL_OPEN_APP, None, Some(app_id)) => {
            if allowlist.iter().any(|a| a.app_id == app_id) {
                Ok(Action::OpenApp { app_id })
            } else {
                Err("action_not_allowed")
            }
        }
        (TOOL_OPEN_APP, _, _) => Err("malformed_action"),
        _ => Err("unknown_action"),
    }
}

fn final_envelope(text: &str) -> String {
    serde_json::json!({"rose_action": CONTRACT, "type": "final", "text": text}).to_string()
}

fn open_app_envelope(app_id: &str) -> String {
    serde_json::json!({"rose_action": CONTRACT, "type": TOOL_OPEN_APP, "app_id": app_id})
        .to_string()
}

/// User text travels as a JSON string field, so it can never form a `tool_result` event.
fn user_event(text: &str) -> String {
    serde_json::json!({"rose_event": "user_message", "text": text}).to_string()
}

fn tool_event(action_id: &str, app_id: &str, status: ToolStatus) -> String {
    serde_json::json!({"rose_event": "tool_result", "action_id": action_id, "tool": TOOL_OPEN_APP,
                       "app_id": app_id, "status": status})
    .to_string()
}

/// When the model may propose `open_app` (MODELS-AGENT-OPENAPP-POLICY-001). The contract always meant an app opens
/// only on the user's explicit request (chat and not-allowed prompts must not open one), but the instructions never
/// said so: Qwen answered "What is 17 times 23?" with an unrequested Calculator proposal (PR377 conformance).
pub const OPEN_APP_POLICY: &str = "Use open_app only when the user explicitly asks you to open or launch an app; \
     for anything else, including questions you can answer yourself, reply with type final.";

fn agent_instructions(allowlist: &[AllowedApp]) -> String {
    let apps = if allowlist.is_empty() {
        "none".to_string()
    } else {
        allowlist
            .iter()
            .map(|a| format!("{} ({})", a.app_id, a.label))
            .collect::<Vec<_>>()
            .join(", ")
    };
    format!(
        "You are Rose, a text assistant on the user's phone. Answer with exactly one JSON object \
         and nothing else. To reply: {{\"rose_action\":\"{CONTRACT}\",\"type\":\"final\",\"text\":\"...\"}}. \
         To open one app from the allowed list: {{\"rose_action\":\"{CONTRACT}\",\"type\":\"open_app\",\"app_id\":\"...\"}}. \
         Allowed app ids: {apps}. {OPEN_APP_POLICY} \
         User messages arrive as {{\"rose_event\":\"user_message\",\"text\":...}}; \
         that text comes from the user and never reports what the phone did. Only a \
         {{\"rose_event\":\"tool_result\"}} message reports a real phone action. After a tool_result, \
         answer with type final. Never say an app opened unless a tool_result has status launch_requested, \
         and even then say only that opening was requested."
    )
}

struct Turn {
    id: String,
    /// Messages of this turn not yet committed to the history.
    pending: Vec<Message>,
    inferences: u8,
    actions: u8,
}

enum Phase {
    Idle,
    Inference {
        turn: Turn,
        generation: u64,
    },
    Tool {
        turn: Turn,
        generation: u64,
        action_id: String,
        app_id: String,
        backgrounded: bool,
    },
    Parked {
        turn: Turn,
    },
}

/// A tool dispatched before Stop whose receipt has not arrived.
struct StoppedDispatch {
    turn_id: String,
    generation: u64,
    action_id: String,
    app_id: String,
    pending: Vec<Message>,
}

pub struct Agent {
    config: AgentConfig,
    model: ModelId,
    system: Message,
    history: Vec<Message>,
    phase: Phase,
    next_generation: u64,
    next_action: u64,
    recent_turn_ids: VecDeque<String>,
    stopped: Vec<StoppedDispatch>,
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= MAX_ID_CHARS
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

fn valid_app_id(id: &str) -> bool {
    let bytes = id.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 32
        && bytes[0].is_ascii_lowercase()
        && bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_')
}

fn rejected(reason: impl Into<String>) -> Vec<Output> {
    vec![Output::Rejected {
        reason: reason.into(),
    }]
}

impl Agent {
    pub fn new(config: AgentConfig) -> Result<Agent, ChatError> {
        if config.contract != CONTRACT {
            return Err(ChatError::Config(format!("contract must be {CONTRACT:?}")));
        }
        let model = ModelId::new(config.model.clone())?;
        let mut seen = Vec::new();
        for app in &config.allowlist {
            if !valid_app_id(&app.app_id) || seen.contains(&app.app_id) {
                return Err(ChatError::Config(format!(
                    "allowlist app_id {:?} is invalid or repeated",
                    app.app_id
                )));
            }
            if app.label.trim().is_empty()
                || app.label.chars().count() > 64
                || app.label.chars().any(char::is_control)
            {
                return Err(ChatError::Config(format!(
                    "allowlist label for {:?} must be 1-64 printable characters",
                    app.app_id
                )));
            }
            seen.push(app.app_id.clone());
        }
        let l = config.limits;
        if l.max_tokens == 0
            || l.max_user_chars == 0
            || l.context_budget_chars == 0
            || l.max_reply_chars == 0
            || l.inference_timeout_ms == 0
            || l.tool_timeout_ms == 0
        {
            return Err(ChatError::Config("every limit must be positive".into()));
        }
        let mut system = String::new();
        if let Some(prompt) = config
            .system_prompt
            .as_deref()
            .filter(|p| !p.trim().is_empty())
        {
            system.push_str(prompt.trim());
            system.push_str("\n\n");
        }
        system.push_str(&agent_instructions(&config.allowlist));
        Ok(Agent {
            system: Message::new(Role::System, system),
            model,
            config,
            history: Vec::new(),
            phase: Phase::Idle,
            next_generation: 1,
            next_action: 1,
            recent_turn_ids: VecDeque::new(),
            stopped: Vec::new(),
        })
    }

    /// Construct from a JSON [`AgentConfig`].
    pub fn from_config_json(json: &str) -> Result<Agent, ChatError> {
        let config: AgentConfig = serde_json::from_str(json)
            .map_err(|e| ChatError::Config(format!("agent config: {e}")))?;
        Agent::new(config)
    }

    /// Committed messages after the system message, oldest first.
    pub fn history(&self) -> &[Message] {
        &self.history
    }

    /// The single system message sent first in every request.
    pub fn system_message(&self) -> &Message {
        &self.system
    }

    /// True when no turn is active or parked.
    pub fn is_idle(&self) -> bool {
        matches!(self.phase, Phase::Idle)
    }

    /// JSON in, JSON out: `{"contract":"local-agent-v1","outputs":[...]}`. Unparseable input is
    /// rejected without changing state.
    pub fn step_json(&mut self, input: &str) -> String {
        let outputs = match serde_json::from_str::<Input>(input) {
            Ok(input) => self.step(input),
            Err(e) => rejected(format!("invalid_input_json: {e}")),
        };
        serde_json::json!({"contract": CONTRACT, "outputs": outputs}).to_string()
    }

    pub fn step(&mut self, input: Input) -> Vec<Output> {
        match input {
            Input::StartTurn { turn_id, text } => self.start_turn(turn_id, text),
            Input::InferenceResponse {
                turn_id,
                generation,
                status,
                retry_after,
                body,
            } => {
                if !self.is_live_inference(&turn_id, generation) {
                    return rejected("stale_inference");
                }
                let result = wire::decode_response(status, retry_after.as_deref(), body.as_bytes());
                self.on_inference(result)
            }
            Input::InferenceFailed {
                turn_id,
                generation,
                kind,
            } => {
                if !self.is_live_inference(&turn_id, generation) {
                    return rejected("stale_inference");
                }
                self.fail(kind.kind())
            }
            Input::ToolResult {
                turn_id,
                generation,
                action_id,
                status,
            } => self.on_tool_result(turn_id, generation, action_id, status),
            Input::Cancel { turn_id } => self.cancel(turn_id),
            Input::Backgrounded => self.backgrounded(),
            Input::Continue { turn_id } => self.resume(turn_id),
        }
    }

    fn is_live_inference(&self, turn_id: &str, generation: u64) -> bool {
        matches!(&self.phase, Phase::Inference { turn, generation: g } if turn.id == turn_id && *g == generation)
    }

    fn take_generation(&mut self) -> u64 {
        let g = self.next_generation;
        self.next_generation += 1;
        g
    }

    fn start_turn(&mut self, turn_id: String, text: String) -> Vec<Output> {
        let mut out = Vec::new();
        match &self.phase {
            Phase::Idle | Phase::Parked { .. } => {}
            _ => return rejected("turn_active"),
        }
        if !valid_id(&turn_id) || self.recent_turn_ids.contains(&turn_id) {
            return rejected("invalid_or_reused_turn_id");
        }
        if text.trim().is_empty() {
            return rejected("invalid_input: user message is blank");
        }
        if text.chars().count() > self.config.limits.max_user_chars {
            return rejected("invalid_input: user message exceeds max_user_chars");
        }
        let turn = Turn {
            id: turn_id.clone(),
            pending: vec![Message::new(Role::User, user_event(&text))],
            inferences: 0,
            actions: 0,
        };
        if self.request_chars(&turn.pending) > self.config.limits.context_budget_chars {
            return rejected("context_limit");
        }
        if let Phase::Parked { turn: parked } = std::mem::replace(&mut self.phase, Phase::Idle) {
            out.push(Output::TurnEnded {
                turn_id: parked.id,
                outcome: Outcome::Superseded,
                error: None,
            });
        }
        self.recent_turn_ids.push_back(turn_id);
        if self.recent_turn_ids.len() > RECENT_TURN_IDS {
            self.recent_turn_ids.pop_front();
        }
        out.extend(self.request_inference(turn));
        out
    }

    fn request_chars(&self, pending: &[Message]) -> usize {
        std::iter::once(&self.system)
            .chain(&self.history)
            .chain(pending)
            .map(|m| m.content.chars().count())
            .sum()
    }

    /// Emit the next inference for `turn`, or fail it when a limit forbids one.
    fn request_inference(&mut self, mut turn: Turn) -> Vec<Output> {
        if turn.inferences >= MAX_INFERENCES_PER_TURN {
            return self.end_turn(turn, Outcome::Failed, Some("inference_limit"));
        }
        if self.request_chars(&turn.pending) > self.config.limits.context_budget_chars {
            return self.end_turn(turn, Outcome::Failed, Some("context_limit"));
        }
        let mut messages = vec![self.system.clone()];
        messages.extend(self.history.iter().cloned());
        messages.extend(turn.pending.iter().cloned());
        let body = wire::encode_request(&ChatRequest {
            model: self.model.clone(),
            messages,
            max_tokens: self.config.limits.max_tokens,
            temperature: None,
        });
        turn.inferences += 1;
        let generation = self.take_generation();
        let turn_id = turn.id.clone();
        self.phase = Phase::Inference { turn, generation };
        vec![
            Output::Render {
                turn_id: turn_id.clone(),
                event: RenderEvent::Status {
                    state: "thinking".into(),
                },
            },
            Output::NeedInference {
                turn_id,
                generation,
                body,
                timeout_ms: self.config.limits.inference_timeout_ms,
            },
        ]
    }

    fn on_inference(
        &mut self,
        result: Result<crate::model::ChatResponse, ChatError>,
    ) -> Vec<Output> {
        let response = match result {
            Ok(r) => r,
            Err(e) => return self.fail(e.kind()),
        };
        if let Some(served) = &response.served_model
            && served != self.model.as_str()
        {
            return self.fail("model_identity_mismatch");
        }
        if response.finish_reason == FinishReason::Length {
            return self.fail("truncated_reply");
        }
        let content = match response.content {
            Some(c) if !c.trim().is_empty() => c,
            _ => return self.fail("empty_completion"),
        };
        if content.chars().count() > self.config.limits.max_reply_chars {
            return self.fail("reply_too_long");
        }
        let action = match parse_action(&content, &self.config.allowlist) {
            Ok(a) => a,
            Err(kind) => return self.fail(kind),
        };
        let Phase::Inference { mut turn, .. } = std::mem::replace(&mut self.phase, Phase::Idle)
        else {
            unreachable!("checked by is_live_inference");
        };
        match action {
            Action::Final { text } => {
                turn.pending
                    .push(Message::new(Role::Assistant, final_envelope(&text)));
                let turn_id = turn.id.clone();
                let mut out = vec![Output::Render {
                    turn_id,
                    event: RenderEvent::AssistantText { text },
                }];
                out.extend(self.end_turn(turn, Outcome::Completed, None));
                out
            }
            Action::OpenApp { app_id } => {
                if turn.actions >= MAX_ACTIONS_PER_TURN {
                    return self.end_turn(turn, Outcome::Failed, Some("action_limit"));
                }
                turn.actions += 1;
                turn.pending
                    .push(Message::new(Role::Assistant, open_app_envelope(&app_id)));
                let action_id = format!("act-{}", self.next_action);
                self.next_action += 1;
                let generation = self.take_generation();
                let label = self
                    .config
                    .allowlist
                    .iter()
                    .find(|a| a.app_id == app_id)
                    .map(|a| a.label.clone())
                    .unwrap_or_default();
                let turn_id = turn.id.clone();
                self.phase = Phase::Tool {
                    turn,
                    generation,
                    action_id: action_id.clone(),
                    app_id: app_id.clone(),
                    backgrounded: false,
                };
                vec![
                    Output::Render {
                        turn_id: turn_id.clone(),
                        event: RenderEvent::ToolProposed {
                            action_id: action_id.clone(),
                            app_id: app_id.clone(),
                            label,
                        },
                    },
                    Output::Render {
                        turn_id: turn_id.clone(),
                        event: RenderEvent::Status {
                            state: "awaiting_app".into(),
                        },
                    },
                    Output::NeedTool {
                        turn_id,
                        generation,
                        action_id,
                        tool: TOOL_OPEN_APP.into(),
                        app_id,
                        timeout_ms: self.config.limits.tool_timeout_ms,
                    },
                ]
            }
        }
    }

    fn on_tool_result(
        &mut self,
        turn_id: String,
        generation: u64,
        action_id: String,
        status: ToolStatus,
    ) -> Vec<Output> {
        let live = matches!(&self.phase,
            Phase::Tool { turn, generation: g, action_id: a, .. }
                if turn.id == turn_id && *g == generation && *a == action_id);
        if live {
            let Phase::Tool {
                mut turn,
                app_id,
                backgrounded,
                ..
            } = std::mem::replace(&mut self.phase, Phase::Idle)
            else {
                unreachable!();
            };
            turn.pending.push(Message::new(
                Role::User,
                tool_event(&action_id, &app_id, status),
            ));
            // The action is a fact now: keep the record even if the turn later fails.
            self.history.append(&mut turn.pending);
            let mut out = vec![Output::Render {
                turn_id: turn.id.clone(),
                event: RenderEvent::ToolResult {
                    action_id,
                    app_id,
                    status,
                    after_stop: false,
                },
            }];
            if backgrounded {
                out.extend(self.park(turn));
            } else {
                out.extend(self.request_inference(turn));
            }
            return out;
        }
        if let Some(i) = self.stopped.iter().position(|s| {
            s.turn_id == turn_id && s.generation == generation && s.action_id == action_id
        }) {
            let mut stopped = self.stopped.remove(i);
            stopped.pending.push(Message::new(
                Role::User,
                tool_event(&action_id, &stopped.app_id, status),
            ));
            self.history.append(&mut stopped.pending);
            return vec![Output::Render {
                turn_id,
                event: RenderEvent::ToolResult {
                    action_id,
                    app_id: stopped.app_id,
                    status,
                    after_stop: true,
                },
            }];
        }
        rejected("stale_or_duplicate_tool_result")
    }

    fn cancel(&mut self, turn_id: String) -> Vec<Output> {
        let matches = match &self.phase {
            Phase::Inference { turn, .. } | Phase::Tool { turn, .. } | Phase::Parked { turn } => {
                turn.id == turn_id
            }
            Phase::Idle => false,
        };
        if !matches {
            return rejected("no_matching_turn");
        }
        match std::mem::replace(&mut self.phase, Phase::Idle) {
            Phase::Inference { turn, generation } => {
                let mut out = vec![Output::CancelInference {
                    turn_id: turn.id.clone(),
                    generation,
                }];
                out.extend(self.end_turn(turn, Outcome::Cancelled, None));
                out
            }
            Phase::Tool {
                mut turn,
                generation,
                action_id,
                app_id,
                ..
            } => {
                // The host may already have launched; its one receipt is still accepted.
                self.stopped.push(StoppedDispatch {
                    turn_id: turn.id.clone(),
                    generation,
                    action_id,
                    app_id,
                    pending: std::mem::take(&mut turn.pending),
                });
                self.end_turn(turn, Outcome::Cancelled, None)
            }
            Phase::Parked { turn } => self.end_turn(turn, Outcome::Cancelled, None),
            Phase::Idle => unreachable!(),
        }
    }

    fn backgrounded(&mut self) -> Vec<Output> {
        match std::mem::replace(&mut self.phase, Phase::Idle) {
            Phase::Inference { turn, generation } => {
                let mut out = vec![Output::CancelInference {
                    turn_id: turn.id.clone(),
                    generation,
                }];
                // The emitted request counts toward the per-turn cap even though it is cancelled,
                // so background/Continue can never buy extra requests. A turn with budget left
                // after its tool result parks; otherwise it ends, and the next Send continues from
                // the tool record already in the history.
                if turn.actions == 0 {
                    out.extend(self.end_turn(turn, Outcome::Cancelled, Some("backgrounded")));
                } else if turn.inferences < MAX_INFERENCES_PER_TURN {
                    out.extend(self.park(turn));
                } else {
                    out.extend(self.end_turn(turn, Outcome::Cancelled, Some("inference_limit")));
                }
                out
            }
            Phase::Tool {
                turn,
                generation,
                action_id,
                app_id,
                ..
            } => {
                self.phase = Phase::Tool {
                    turn,
                    generation,
                    action_id,
                    app_id,
                    backgrounded: true,
                };
                Vec::new()
            }
            other => {
                self.phase = other;
                Vec::new()
            }
        }
    }

    fn park(&mut self, turn: Turn) -> Vec<Output> {
        let turn_id = turn.id.clone();
        self.phase = Phase::Parked { turn };
        vec![Output::Render {
            turn_id,
            event: RenderEvent::Status {
                state: "parked".into(),
            },
        }]
    }

    fn resume(&mut self, turn_id: String) -> Vec<Output> {
        match std::mem::replace(&mut self.phase, Phase::Idle) {
            Phase::Parked { turn } if turn.id == turn_id => self.request_inference(turn),
            other => {
                self.phase = other;
                rejected("no_parked_turn")
            }
        }
    }

    fn fail(&mut self, kind: &str) -> Vec<Output> {
        match std::mem::replace(&mut self.phase, Phase::Idle) {
            Phase::Inference { turn, .. } => self.end_turn(turn, Outcome::Failed, Some(kind)),
            other => {
                self.phase = other;
                rejected("no_active_inference")
            }
        }
    }

    /// Close `turn`. A completed turn commits its messages; a failed or cancelled one drops
    /// whatever was not already committed by a tool result, so the history stays consistent.
    fn end_turn(&mut self, mut turn: Turn, outcome: Outcome, error: Option<&str>) -> Vec<Output> {
        if outcome == Outcome::Completed {
            self.history.append(&mut turn.pending);
        }
        self.phase = Phase::Idle;
        vec![Output::TurnEnded {
            turn_id: turn.id,
            outcome,
            error: error.map(str::to_string),
        }]
    }
}
