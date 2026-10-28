use std::time::Duration;

use serde::Deserialize;

use crate::auth::AuthMode;
use crate::error::ChatError;
use crate::model::ModelId;

/// The only wire protocol name accepted in this build. It is a candidate shape for testing,
/// not a statement about the final serving runtime or Agent Platform API.
pub const CANDIDATE_PROTOCOL: &str = "openai-chat-candidate";

/// Environment overrides, applied over the file and under command-line overrides.
pub const ENV_BASE_URL: &str = "ROSE_CHAT_BASE_URL";
pub const ENV_REQUEST_URL: &str = "ROSE_CHAT_REQUEST_URL";
pub const ENV_MODEL_ID: &str = "ROSE_CHAT_MODEL_ID";

const DEFAULT_CONNECT_MS: u64 = 10_000;
const DEFAULT_REQUEST_MS: u64 = 120_000;
const DEFAULT_MAX_TOKENS: u32 = 512;
const DEFAULT_CONTEXT_BUDGET_CHARS: usize = 32_000;
const DEFAULT_MAX_RESPONSE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_RESPONSE_BYTES_CEILING: u64 = 64 * 1024 * 1024;

/// Suffix appended to `endpoint.base_url`. Never appended to `endpoint.request_url`.
pub const CHAT_COMPLETIONS_SUFFIX: &str = "/chat/completions";

/// Where requests are sent. Exactly one form is configured.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Endpoint {
    /// OpenAI-compatible base such as `http://127.0.0.1:8080/v1`; `/chat/completions` is appended.
    Base(String),
    /// Complete request URL used verbatim, such as an Agent Platform `...:rawPredict` URL.
    RequestUrl(String),
}

impl Endpoint {
    /// The exact URL a request is POSTed to.
    pub fn request_url(&self) -> String {
        match self {
            Endpoint::Base(b) => format!("{}{CHAT_COMPLETIONS_SUFFIX}", b.trim_end_matches('/')),
            Endpoint::RequestUrl(u) => u.clone(),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Protocol {
    OpenAiChatCandidate,
}

/// Validated configuration. Holds no credential values.
#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub endpoint: Endpoint,
    pub protocol: Protocol,
    pub model: ModelId,
    /// Recorded in evidence only; never sent or checked.
    pub expected_revision: Option<String>,
    pub auth: AuthMode,
    pub connect_timeout: Duration,
    pub request_timeout: Duration,
    pub max_tokens: u32,
    /// Conservative client-side guard on total message characters (not tokens).
    pub context_budget_chars: usize,
    /// Responses with larger bodies fail with `response_too_large`.
    pub max_response_bytes: u64,
    pub system_prompt: Option<String>,
    pub temperature: Option<f32>,
}

/// Values that replace file settings. `None` leaves the lower layer unchanged.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Overrides {
    pub base_url: Option<String>,
    pub request_url: Option<String>,
    pub model_id: Option<String>,
}

impl Overrides {
    pub fn from_env(env: &dyn Fn(&str) -> Option<String>) -> Self {
        Overrides {
            base_url: env(ENV_BASE_URL),
            request_url: env(ENV_REQUEST_URL),
            model_id: env(ENV_MODEL_ID),
        }
    }
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawConfig {
    endpoint: Option<RawEndpoint>,
    model: Option<RawModel>,
    auth: Option<RawAuth>,
    timeouts: Option<RawTimeouts>,
    limits: Option<RawLimits>,
    retry: Option<RawRetry>,
    chat: Option<RawChat>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawEndpoint {
    base_url: Option<String>,
    request_url: Option<String>,
    protocol: Option<String>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawModel {
    id: Option<String>,
    expected_revision: Option<String>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawAuth {
    mode: Option<String>,
    env_var: Option<String>,
    command: Option<Vec<String>>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawTimeouts {
    connect_ms: Option<u64>,
    request_ms: Option<u64>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawLimits {
    max_tokens: Option<u32>,
    context_budget_chars: Option<usize>,
    max_response_bytes: Option<u64>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawRetry {
    max_attempts: Option<u32>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawChat {
    system_prompt: Option<String>,
    temperature: Option<f32>,
}

impl Config {
    /// Parse a TOML file's text with no overrides.
    pub fn from_toml(text: &str) -> Result<Config, ChatError> {
        Config::load(text, &Overrides::default(), &Overrides::default())
    }

    /// Parse TOML, then apply environment overrides and command-line overrides (CLI wins).
    pub fn load(text: &str, env: &Overrides, cli: &Overrides) -> Result<Config, ChatError> {
        // toml's error text can quote the offending line; keep only its location.
        let raw: RawConfig = toml::from_str(text).map_err(|e| {
            let at = e
                .span()
                .map(|s| format!(" at byte {}", s.start))
                .unwrap_or_default();
            let what = if e.message().starts_with("unknown field") {
                e.message().to_string()
            } else {
                "invalid TOML or wrong value type".to_string()
            };
            ChatError::Config(format!("{what}{at}"))
        })?;
        let endpoint = raw.endpoint.unwrap_or_default();
        let model = raw.model.unwrap_or_default();

        // The highest layer that names an endpoint replaces the whole endpoint below it.
        let file = Overrides {
            base_url: endpoint.base_url,
            request_url: endpoint.request_url,
            model_id: None,
        };
        let mut chosen = None;
        for (layer, name) in [(cli, "command line"), (env, "environment"), (&file, "file")] {
            match (&layer.base_url, &layer.request_url) {
                (Some(_), Some(_)) => {
                    return Err(ChatError::Config(format!(
                        "the {name} sets both endpoint.base_url and endpoint.request_url; set one"
                    )));
                }
                (Some(b), None) => chosen = Some(Endpoint::Base(b.clone())),
                (None, Some(u)) => chosen = Some(Endpoint::RequestUrl(u.clone())),
                (None, None) => continue,
            }
            break;
        }
        let endpoint_target = chosen.ok_or_else(|| {
            ChatError::Config("endpoint.base_url or endpoint.request_url is required".into())
        })?;
        validate_endpoint(&endpoint_target)?;

        let protocol = match endpoint.protocol.as_deref() {
            Some(CANDIDATE_PROTOCOL) => Protocol::OpenAiChatCandidate,
            Some(other) => {
                return Err(ChatError::Config(format!(
                    "endpoint.protocol {other:?} is not supported; this build supports only \
                     {CANDIDATE_PROTOCOL:?}"
                )));
            }
            None => {
                return Err(ChatError::Config(format!(
                    "endpoint.protocol is required (supported: {CANDIDATE_PROTOCOL:?})"
                )));
            }
        };

        let model_id = cli
            .model_id
            .clone()
            .or_else(|| env.model_id.clone())
            .or(model.id)
            .ok_or_else(|| {
                ChatError::Config("model.id is required; there is no default model".into())
            })?;
        let model_id = ModelId::new(model_id)?;

        let auth = parse_auth(raw.auth.unwrap_or_default())?;

        let timeouts = raw.timeouts.unwrap_or_default();
        let connect_ms = timeouts.connect_ms.unwrap_or(DEFAULT_CONNECT_MS);
        let request_ms = timeouts.request_ms.unwrap_or(DEFAULT_REQUEST_MS);
        if connect_ms == 0 || request_ms == 0 {
            return Err(ChatError::Config(
                "timeouts must be greater than zero".into(),
            ));
        }
        if connect_ms > request_ms {
            return Err(ChatError::Config(
                "timeouts.connect_ms must not exceed timeouts.request_ms".into(),
            ));
        }

        let limits = raw.limits.unwrap_or_default();
        let max_tokens = limits.max_tokens.unwrap_or(DEFAULT_MAX_TOKENS);
        let context_budget_chars = limits
            .context_budget_chars
            .unwrap_or(DEFAULT_CONTEXT_BUDGET_CHARS);
        if max_tokens == 0 || context_budget_chars == 0 {
            return Err(ChatError::Config("limits must be greater than zero".into()));
        }
        let max_response_bytes = limits
            .max_response_bytes
            .unwrap_or(DEFAULT_MAX_RESPONSE_BYTES);
        if max_response_bytes == 0 || max_response_bytes > MAX_RESPONSE_BYTES_CEILING {
            return Err(ChatError::Config(format!(
                "limits.max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES_CEILING}"
            )));
        }

        match raw.retry.unwrap_or_default().max_attempts.unwrap_or(0) {
            0 => {}
            n => {
                return Err(ChatError::Config(format!(
                    "retry.max_attempts = {n} is not supported in this build; only 0 (no automatic \
                     retry) is implemented"
                )));
            }
        }

        let chat = raw.chat.unwrap_or_default();
        if let Some(t) = chat.temperature
            && !(0.0..=2.0).contains(&t)
        {
            return Err(ChatError::Config(
                "chat.temperature must be between 0 and 2".into(),
            ));
        }
        let system_prompt = chat.system_prompt.filter(|s| !s.trim().is_empty());

        Ok(Config {
            endpoint: endpoint_target,
            protocol,
            model: model_id,
            expected_revision: model.expected_revision,
            auth,
            connect_timeout: Duration::from_millis(connect_ms),
            request_timeout: Duration::from_millis(request_ms),
            max_tokens,
            context_budget_chars,
            max_response_bytes,
            system_prompt,
            temperature: chat.temperature,
        })
    }

    /// Human-readable resolved settings. Contains no credential values.
    pub fn summary(&self) -> String {
        let protocol = match self.protocol {
            Protocol::OpenAiChatCandidate => CANDIDATE_PROTOCOL,
        };
        let mut out = format!(
            "{}\nrequest URL = {}\nendpoint.protocol = {protocol} (candidate shape, not a final \
             runtime claim)\nmodel.id = {}\nmodel.expected_revision = {}\nauth = {}\n\
             timeouts.connect_ms = {}\ntimeouts.request_ms = {}\nlimits.max_tokens = {}\n\
             limits.context_budget_chars = {}\nlimits.max_response_bytes = {}\n\
             retry.max_attempts = 0\n",
            match &self.endpoint {
                Endpoint::Base(b) => format!("endpoint.base_url = {b}"),
                Endpoint::RequestUrl(u) => format!("endpoint.request_url = {u} (used verbatim)"),
            },
            self.endpoint.request_url(),
            self.model,
            self.expected_revision
                .as_deref()
                .unwrap_or("(not recorded)"),
            self.auth.describe(),
            self.connect_timeout.as_millis(),
            self.request_timeout.as_millis(),
            self.max_tokens,
            self.context_budget_chars,
            self.max_response_bytes,
        );
        out.push_str(&format!(
            "chat.system_prompt = {}\nchat.temperature = {}\n",
            if self.system_prompt.is_some() {
                "set"
            } else {
                "(none)"
            },
            self.temperature
                .map(|t| t.to_string())
                .unwrap_or_else(|| "(server default)".into()),
        ));
        out
    }
}

fn validate_endpoint(endpoint: &Endpoint) -> Result<(), ChatError> {
    let (field, url) = match endpoint {
        Endpoint::Base(u) => ("endpoint.base_url", u),
        Endpoint::RequestUrl(u) => ("endpoint.request_url", u),
    };
    if url.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err(ChatError::Config(format!("{field} contains whitespace")));
    }
    let (secure, rest) = if let Some(r) = url.strip_prefix("https://") {
        (true, r)
    } else if let Some(r) = url.strip_prefix("http://") {
        (false, r)
    } else {
        return Err(ChatError::Config(format!(
            "{field} must start with http:// or https://"
        )));
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.is_empty() {
        return Err(ChatError::Config(format!("{field} has no host")));
    }
    if authority.contains('@') {
        return Err(ChatError::Config(format!(
            "{field} must not embed credentials; use the [auth] section"
        )));
    }
    if !secure && !is_loopback_host(authority) {
        return Err(ChatError::Config(format!(
            "{field} uses plain http:// to a non-loopback host; use https:// (plain HTTP is allowed \
             only for 127.0.0.0/8, ::1 and localhost)"
        )));
    }
    if rest.contains('#') {
        return Err(ChatError::Config(format!(
            "{field} must not contain a fragment"
        )));
    }
    if let Endpoint::Base(_) = endpoint {
        let path = url.trim_end_matches('/');
        if rest.contains('?') {
            return Err(ChatError::Config(
                "endpoint.base_url must not contain a query; use endpoint.request_url".into(),
            ));
        }
        if path.ends_with(CHAT_COMPLETIONS_SUFFIX)
            || path.ends_with(":rawPredict")
            || path.ends_with(":streamRawPredict")
            || path.ends_with(":predict")
        {
            return Err(ChatError::Config(format!(
                "endpoint.base_url looks like a complete request URL; `{CHAT_COMPLETIONS_SUFFIX}` \
                 would be appended to it. Use endpoint.request_url for a URL that is used verbatim"
            )));
        }
    }
    Ok(())
}

/// True for `localhost`, 127.0.0.0/8 and `[::1]`, with or without a port.
pub fn is_loopback_host(authority: &str) -> bool {
    let host = if let Some(v6) = authority.strip_prefix('[') {
        v6.split(']').next().unwrap_or("")
    } else {
        authority.split(':').next().unwrap_or("")
    };
    let host = host.to_ascii_lowercase();
    host == "localhost"
        || host == "::1"
        || host
            .parse::<std::net::Ipv4Addr>()
            .is_ok_and(|ip| ip.is_loopback())
}

/// The authority (host and optional port) of an http(s) URL.
pub fn url_authority(url: &str) -> &str {
    let rest = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
        .unwrap_or(url);
    rest.split(['/', '?', '#']).next().unwrap_or("")
}

fn parse_auth(raw: RawAuth) -> Result<AuthMode, ChatError> {
    let mode = raw.mode.as_deref().unwrap_or("none");
    match mode {
        "none" => {
            if raw.env_var.is_some() || raw.command.is_some() {
                return Err(ChatError::Config(
                    "auth.env_var and auth.command apply only to bearer modes".into(),
                ));
            }
            Ok(AuthMode::None)
        }
        "bearer-env" => {
            if raw.command.is_some() {
                return Err(ChatError::Config(
                    "auth.command is not used by bearer-env".into(),
                ));
            }
            let var = raw.env_var.ok_or_else(|| {
                ChatError::Config("auth.env_var is required for bearer-env".into())
            })?;
            let valid = var
                .chars()
                .next()
                .is_some_and(|c| c.is_ascii_uppercase() || c == '_')
                && var
                    .chars()
                    .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_');
            if !valid {
                return Err(ChatError::Config(
                    "auth.env_var must be an environment variable name like ROSE_CHAT_TOKEN, not a \
                     token value"
                        .into(),
                ));
            }
            Ok(AuthMode::BearerEnv { var })
        }
        "bearer-command" => {
            if raw.env_var.is_some() {
                return Err(ChatError::Config(
                    "auth.env_var is not used by bearer-command".into(),
                ));
            }
            let argv = raw.command.unwrap_or_default();
            if argv.is_empty() || argv[0].trim().is_empty() {
                return Err(ChatError::Config(
                    "auth.command must be a non-empty argument list for bearer-command".into(),
                ));
            }
            Ok(AuthMode::BearerCommand { argv })
        }
        other => Err(ChatError::Config(format!(
            "auth.mode {other:?} is not one of none, bearer-env, bearer-command"
        ))),
    }
}
