//! local-agent-v1 envelope conformance for the served model (MODELS-HTTPS-SOURCE-001).
//!
//! Every reply is judged by the same `Agent` the Android app runs. A fresh agent sends each
//! prompt as its first turn, and the agent's own outcome (final text, an `open_app` proposal or
//! a failed turn) is recorded. The harness never executes a tool, never asks for reasoning, and
//! keeps no prompt or reply text in its summary.
//!
//! ```text
//! conformance fixture --prompts P --replies R
//! conformance live --prompts P --origin https://HOST --model ID --token-file F
//!                  [--min-interval-ms 11000] [--timeout-s 120]
//! ```
//!
//! `live` is bounded:
//! - one request per prompt, 20 prompts, `max_tokens` 512;
//! - a per-request timeout of at most 120 s and a response cap of 1 MiB;
//! - TLS verified against platform roots; redirects never followed;
//! - the bearer is read from a 0600 file and never printed.
//!
//! Plain `http://` is accepted only for 127.0.0.1, with `--allow-loopback-http`, for fixture
//! servers in tests. Exit status: 0 when the bar passes, 1 when it fails, 2 on a usage or input
//! error.

use std::collections::BTreeMap;
use std::io::Read;
use std::time::{Duration, Instant};

use rose_chat_core::agent::{Agent, Input, Outcome, Output, TransportFailure};
use serde::Deserialize;
use serde_json::{Value, json};

pub const PROMPT_COUNT: usize = 20;
const MAX_RESPONSE_BYTES: u64 = 1 << 20;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptSet {
    pub contract: String,
    pub model: String,
    pub allowlist: Vec<Value>,
    pub system_prompt: Option<String>,
    pub bar: Bar,
    pub prompts: Vec<Prompt>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bar {
    pub min_parsed: usize,
    #[allow(dead_code)]
    pub note: String,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Prompt {
    pub id: String,
    pub category: String,
    pub text: String,
    pub expect: String,
}

/// What the Android agent did with one reply.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Final,
    OpenApp(String),
    Failed(String),
}

impl Verdict {
    pub fn label(&self) -> String {
        match self {
            Verdict::Final => "final".into(),
            Verdict::OpenApp(app) => format!("open_app:{app}"),
            Verdict::Failed(kind) => format!("failed:{kind}"),
        }
    }

    /// The model produced an envelope the agent accepted (allowlisted or not).
    pub fn parsed(&self) -> bool {
        !matches!(self, Verdict::Failed(k) if k != "action_not_allowed")
    }
}

pub fn load_prompts(text: &str) -> Result<PromptSet, String> {
    let set: PromptSet = serde_json::from_str(text).map_err(|e| format!("prompts: {e}"))?;
    if set.prompts.len() != PROMPT_COUNT {
        return Err(format!("prompts: need exactly {PROMPT_COUNT}"));
    }
    for p in &set.prompts {
        if !matches!(
            (p.category.as_str(), p.expect.as_str()),
            ("chat", "final") | ("launch", "open_app:calculator") | ("not_allowed", "no_open_app")
        ) {
            return Err(format!(
                "prompts: {} has an unknown category/expectation",
                p.id
            ));
        }
    }
    Ok(set)
}

fn agent(set: &PromptSet) -> Agent {
    let config = json!({"contract": set.contract, "model": set.model, "system_prompt": set.system_prompt,
                        "allowlist": set.allowlist});
    Agent::from_config_json(&config.to_string()).expect("prompt set carries a valid agent config")
}

/// Start the prompt's turn on a fresh agent; returns the agent, the request body and its generation.
pub fn first_request(set: &PromptSet, prompt: &Prompt) -> (Agent, String, u64) {
    let mut a = agent(set);
    let out = a.step(Input::StartTurn {
        turn_id: "conformance".into(),
        text: prompt.text.clone(),
    });
    let (body, generation) = out
        .into_iter()
        .find_map(|o| match o {
            Output::NeedInference {
                body, generation, ..
            } => Some((body, generation)),
            _ => None,
        })
        .expect("a valid start_turn emits need_inference");
    (a, body, generation)
}

/// Feed the reply (or transport failure) to the agent and read back its verdict.
pub fn judge(
    mut a: Agent,
    generation: u64,
    reply: Result<(u16, String), TransportFailure>,
) -> Verdict {
    let input = match reply {
        Ok((status, body)) => Input::InferenceResponse {
            turn_id: "conformance".into(),
            generation,
            status,
            retry_after: None,
            body,
        },
        Err(kind) => Input::InferenceFailed {
            turn_id: "conformance".into(),
            generation,
            kind,
        },
    };
    let out = a.step(input);
    for o in &out {
        if let Output::NeedTool { app_id, .. } = o {
            return Verdict::OpenApp(app_id.clone()); // recorded, never executed
        }
    }
    for o in &out {
        if let Output::TurnEnded { outcome, error, .. } = o {
            return match outcome {
                Outcome::Completed => Verdict::Final,
                _ => Verdict::Failed(error.clone().unwrap_or_else(|| "unknown".into())),
            };
        }
    }
    Verdict::Failed("no_outcome".into())
}

pub fn conforms(prompt: &Prompt, verdict: &Verdict) -> bool {
    match prompt.expect.as_str() {
        "final" | "no_open_app" => *verdict == Verdict::Final,
        "open_app:calculator" => *verdict == Verdict::OpenApp("calculator".into()),
        _ => false,
    }
}

/// Nonsecret summary: ids, categories, verdict labels and counts only.
pub fn summarize(set: &PromptSet, results: &[(Prompt, Verdict, Value)]) -> Value {
    let parsed = results.iter().filter(|(_, v, _)| v.parsed()).count();
    let conforming = results.iter().filter(|(p, v, _)| conforms(p, v)).count();
    let wrong_launch = results
        .iter()
        .filter(|(_, v, _)| matches!(v, Verdict::OpenApp(app) if app != "calculator"))
        .count();
    let unwanted_open = results
        .iter()
        .filter(|(p, v, _)| p.category != "launch" && matches!(v, Verdict::OpenApp(_)))
        .count();
    let mut failures: BTreeMap<String, usize> = BTreeMap::new();
    for (_, v, _) in results {
        if let Verdict::Failed(kind) = v {
            *failures.entry(kind.clone()).or_default() += 1;
        }
    }
    let pass = parsed >= set.bar.min_parsed && wrong_launch == 0 && unwanted_open == 0;
    json!({
        "contract": set.contract, "model": set.model, "total": results.len(),
        "parsed": parsed, "conforming": conforming, "min_parsed": set.bar.min_parsed,
        "wrong_launch_target": wrong_launch, "open_app_outside_launch_prompts": unwanted_open,
        "failures": failures, "pass": pass,
        "results": results.iter().map(|(p, v, extra)| {
            let mut r = json!({"id": p.id, "category": p.category, "verdict": v.label(), "conforms": conforms(p, v)});
            if let (Some(r), Some(e)) = (r.as_object_mut(), extra.as_object()) {
                r.extend(e.clone());
            }
            r
        }).collect::<Vec<_>>(),
    })
}

pub fn run_fixture(set: &PromptSet, replies: &Value) -> Result<Value, String> {
    let mut results = Vec::new();
    for prompt in &set.prompts {
        let reply = replies
            .get(&prompt.id)
            .ok_or_else(|| format!("replies: missing {}", prompt.id))?;
        let status = reply["status"].as_u64().ok_or("replies: status")? as u16;
        let body = reply["body"].as_str().ok_or("replies: body")?.to_string();
        let (a, _, generation) = first_request(set, prompt);
        let verdict = judge(a, generation, Ok((status, body)));
        results.push((prompt.clone(), verdict, json!({})));
    }
    Ok(summarize(set, &results))
}

pub struct Live {
    pub origin: String,
    pub token: String,
    pub min_interval: Duration,
    pub timeout: Duration,
}

/// `https://host[:port]` with no path, or `http://127.0.0.1:port` when loopback HTTP is allowed.
pub fn check_origin(origin: &str, allow_loopback_http: bool) -> Result<(), String> {
    let rest = if let Some(r) = origin.strip_prefix("https://") {
        r
    } else if let Some(r) = origin.strip_prefix("http://127.0.0.1:") {
        if !allow_loopback_http {
            return Err("origin must be https:// (plain http only for 127.0.0.1 with --allow-loopback-http)".into());
        }
        r
    } else {
        return Err("origin must be https://host[:port]".into());
    };
    if rest.is_empty()
        || !rest
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || ".-:".contains(c))
    {
        return Err(
            "origin must be scheme://host[:port] with no path, query or credentials".into(),
        );
    }
    Ok(())
}

pub fn read_token(path: &str) -> Result<String, String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        let meta = std::fs::metadata(path).map_err(|e| format!("token file: {}", e.kind()))?;
        if !meta.is_file() || meta.permissions().mode() & 0o077 != 0 {
            return Err("token file must be a regular file with mode 600 or stricter".into());
        }
        // SAFETY: getuid has no preconditions.
        if meta.uid() != unsafe { libc_getuid() } {
            return Err("token file must be owned by the current user".into());
        }
    }
    let token = std::fs::read_to_string(path).map_err(|e| format!("token file: {}", e.kind()))?;
    let token = token.trim().to_string();
    let ok = (token.len() == 43
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'))
        || (token.len() == 64
            && token
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)));
    if !ok {
        return Err("token must be 256 bits: 43 base64url or 64 lowercase hex characters".into());
    }
    Ok(token)
}

#[cfg(unix)]
unsafe extern "C" {
    #[link_name = "getuid"]
    fn libc_getuid() -> u32;
}

fn post(
    agent: &ureq::Agent,
    url: &str,
    token: &str,
    body: String,
) -> Result<(u16, String), TransportFailure> {
    let response = agent
        .post(url)
        .header("Content-Type", "application/json")
        .header("Authorization", &format!("Bearer {token}"))
        .send(body);
    let mut response = match response {
        Ok(r) => r,
        Err(ureq::Error::Timeout(_)) => return Err(TransportFailure::TimeoutResponse),
        Err(ureq::Error::Tls(_)) | Err(ureq::Error::Rustls(_)) => {
            return Err(TransportFailure::Tls);
        }
        Err(_) => return Err(TransportFailure::Connect),
    };
    let status = response.status().as_u16();
    let mut text = String::new();
    let read = response
        .body_mut()
        .as_reader()
        .take(MAX_RESPONSE_BYTES + 1)
        .read_to_string(&mut text);
    match read {
        Ok(n) if n as u64 > MAX_RESPONSE_BYTES => Err(TransportFailure::ResponseTooLarge),
        Ok(_) => Ok((status, text)),
        Err(_) => Err(TransportFailure::TimeoutResponse),
    }
}

pub fn run_live(set: &PromptSet, live: &Live) -> Value {
    let url = format!("{}/v1/chat/completions", live.origin);
    let loopback = live.origin.starts_with("http://127.0.0.1:");
    let http: ureq::Agent = ureq::Agent::config_builder()
        .http_status_as_error(false)
        .max_redirects(0)
        .max_redirects_will_error(false)
        .https_only(!loopback)
        .proxy(if loopback {
            None
        } else {
            ureq::Proxy::try_from_env()
        })
        .timeout_global(Some(live.timeout))
        .tls_config(
            ureq::tls::TlsConfig::builder()
                .root_certs(ureq::tls::RootCerts::PlatformVerifier)
                .build(),
        )
        .user_agent(concat!("rose-conformance/", env!("CARGO_PKG_VERSION")))
        .build()
        .into();
    let mut results = Vec::new();
    for (i, prompt) in set.prompts.iter().enumerate() {
        if i > 0 {
            std::thread::sleep(live.min_interval);
        }
        let (a, body, generation) = first_request(set, prompt);
        let started = Instant::now();
        let reply = post(&http, &url, &live.token, body);
        let latency_ms = started.elapsed().as_millis() as u64;
        let status = reply.as_ref().ok().map(|(s, _)| *s);
        let verdict = judge(a, generation, reply);
        results.push((
            prompt.clone(),
            verdict,
            json!({"http_status": status, "latency_ms": latency_ms}),
        ));
    }
    let mut summary = summarize(set, &results);
    summary["origin"] = json!(live.origin);
    summary
}

#[allow(dead_code)]
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(match cli(&args) {
        Ok(summary) => {
            println!("{}", serde_json::to_string_pretty(&summary).unwrap());
            if summary["pass"] == true { 0 } else { 1 }
        }
        Err(e) => {
            eprintln!("refused: {e}");
            2
        }
    });
}

pub fn cli(args: &[String]) -> Result<Value, String> {
    let mode = args.first().ok_or("usage: conformance fixture|live ...")?;
    let mut opts: BTreeMap<&str, &str> = BTreeMap::new();
    let mut allow_loopback_http = false;
    let mut it = args[1..].iter();
    while let Some(flag) = it.next() {
        if flag == "--allow-loopback-http" {
            allow_loopback_http = true;
            continue;
        }
        let key = match flag.as_str() {
            "--prompts" | "--replies" | "--origin" | "--model" | "--token-file"
            | "--min-interval-ms" | "--timeout-s" => flag.as_str(),
            other => return Err(format!("unknown option {other}")),
        };
        let value = it.next().ok_or_else(|| format!("{key} needs a value"))?;
        opts.insert(key, value);
    }
    let prompts_path = opts.get("--prompts").ok_or("--prompts is required")?;
    let set = load_prompts(
        &std::fs::read_to_string(prompts_path).map_err(|e| format!("prompts: {}", e.kind()))?,
    )?;
    match mode.as_str() {
        "fixture" => {
            let replies_path = opts.get("--replies").ok_or("--replies is required")?;
            let replies: Value = serde_json::from_str(
                &std::fs::read_to_string(replies_path)
                    .map_err(|e| format!("replies: {}", e.kind()))?,
            )
            .map_err(|e| format!("replies: {e}"))?;
            run_fixture(&set, replies.get("replies").unwrap_or(&replies))
        }
        "live" => {
            let origin = opts
                .get("--origin")
                .ok_or("--origin is required")?
                .trim_end_matches('/')
                .to_string();
            check_origin(&origin, allow_loopback_http)?;
            let model = opts.get("--model").ok_or("--model is required")?;
            if *model != set.model {
                return Err(format!(
                    "--model must equal the prompt set's model {:?}",
                    set.model
                ));
            }
            let token = read_token(opts.get("--token-file").ok_or("--token-file is required")?)?;
            let parse = |k: &str, d: u64| {
                opts.get(k).map_or(Ok(d), |v| {
                    v.parse::<u64>().map_err(|_| format!("{k}: not a number"))
                })
            };
            let timeout_s = parse("--timeout-s", 120)?;
            if !(1..=120).contains(&timeout_s) {
                return Err("--timeout-s must be 1-120".into());
            }
            let interval = parse("--min-interval-ms", 11_000)?;
            if interval > 60_000 {
                return Err("--min-interval-ms must be at most 60000".into());
            }
            Ok(run_live(
                &set,
                &Live {
                    origin,
                    token,
                    min_interval: Duration::from_millis(interval),
                    timeout: Duration::from_secs(timeout_s),
                },
            ))
        }
        other => Err(format!("unknown mode {other}")),
    }
}
