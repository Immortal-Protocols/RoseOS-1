//! rose-chat: configuration check, scripted or interactive chat over HTTP, and an in-process
//! FIXTURE demonstration.
//!
//! Exit codes: 0 success, 2 usage error, 3 configuration or credential error, 4 one or more
//! chat turns failed (each failure is reported; later turns still run with unchanged history).

use std::env;
use std::fs;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Instant;

use rose_chat_core::auth::AuthMode;
use rose_chat_core::config::{Config, Overrides};
use rose_chat_core::fixture::{ScriptedTransport, Step};
use rose_chat_core::transcript::Transcript;
use rose_chat_core::{ChatError, ChatSession, HttpTransport};

const USAGE: &str = "usage:
  rose-chat config-check --config FILE [ENDPOINT/MODEL OVERRIDES]
  rose-chat chat --config FILE [--script FILE] [--transcript FILE] [--label TEXT]
                 [ENDPOINT/MODEL OVERRIDES]
  rose-chat fixture-demo

overrides: --base-url URL | --request-url URL, --model-id ID
chat reads one user message per line from --script, or from stdin; blank lines and
lines starting with # are skipped.";

const FIXTURE_CONFIG: &str = r#"
[endpoint]
base_url = "http://127.0.0.1:9"
protocol = "openai-chat-candidate"

[model]
id = "fixture-only-not-a-model"
"#;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("config-check") => config_check(&args[1..]),
        Some("chat") => chat(&args[1..]),
        Some("fixture-demo") if args.len() == 1 => fixture_demo(),
        _ => usage(),
    }
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

#[derive(Default)]
struct Args {
    config: Option<String>,
    overrides: Overrides,
    script: Option<PathBuf>,
    transcript: Option<PathBuf>,
    label: Option<String>,
}

/// Parses `--flag value` pairs. `chat_flags` enables the chat-only flags.
fn parse_args(args: &[String], chat_flags: bool) -> Option<Args> {
    let mut out = Args::default();
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        let value = it.next()?.clone();
        match flag.as_str() {
            "--config" => out.config = Some(value),
            "--base-url" => out.overrides.base_url = Some(value),
            "--request-url" => out.overrides.request_url = Some(value),
            "--model-id" => out.overrides.model_id = Some(value),
            "--script" if chat_flags => out.script = Some(value.into()),
            "--transcript" if chat_flags => out.transcript = Some(value.into()),
            "--label" if chat_flags => out.label = Some(value),
            _ => return None,
        }
    }
    out.config.as_ref()?;
    Some(out)
}

fn load_config(args: &Args) -> Result<Config, ExitCode> {
    let path = args.config.as_deref().unwrap_or_default();
    let text = fs::read_to_string(path).map_err(|e| {
        eprintln!("configuration error: cannot read {path}: {}", e.kind());
        ExitCode::from(3)
    })?;
    let env_lookup = |k: &str| env::var(k).ok();
    Config::load(&text, &Overrides::from_env(&env_lookup), &args.overrides).map_err(|e| {
        eprintln!("{e}");
        ExitCode::from(3)
    })
}

fn config_check(raw: &[String]) -> ExitCode {
    let Some(args) = parse_args(raw, false) else {
        return usage();
    };
    let config = match load_config(&args) {
        Ok(c) => c,
        Err(code) => return code,
    };
    print!("{}", config.summary());
    // Report credential availability without resolving commands or printing values.
    match &config.auth {
        AuthMode::BearerEnv { var } => {
            let state = if env::var(var).is_ok_and(|v| !v.trim().is_empty()) {
                "set"
            } else {
                "NOT set"
            };
            println!("auth.env_var {var} is {state} in this environment");
        }
        AuthMode::BearerCommand { .. } => println!("auth.command is not run by config-check"),
        AuthMode::None => {}
    }
    println!("config-check: OK (no network request was made)");
    ExitCode::SUCCESS
}

fn chat(raw: &[String]) -> ExitCode {
    let Some(args) = parse_args(raw, true) else {
        return usage();
    };
    let config = match load_config(&args) {
        Ok(c) => c,
        Err(code) => return code,
    };
    let lines: Box<dyn Iterator<Item = io::Result<String>>> = match &args.script {
        Some(path) => match fs::File::open(path) {
            Ok(f) => Box::new(io::BufReader::new(f).lines()),
            Err(e) => {
                eprintln!("cannot read script {}: {}", path.display(), e.kind());
                return ExitCode::from(2);
            }
        },
        None => Box::new(io::stdin().lock().lines()),
    };
    let env_lookup = |k: &str| env::var(k).ok();
    let auth = match config.auth.resolve(&env_lookup) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::from(3);
        }
    };
    let mut transcript = match &args.transcript {
        Some(path) => match Transcript::create(path, &config, args.label.as_deref()) {
            Ok(t) => Some(t),
            Err(e) => {
                eprintln!("cannot write transcript {}: {}", path.display(), e.kind());
                return ExitCode::from(2);
            }
        },
        None => None,
    };

    let transport = HttpTransport::new(&config);
    eprintln!(
        "rose-chat: POST {} model.id={} (client {})",
        transport.url().split(['?', '#']).next().unwrap_or(""),
        config.model,
        env!("CARGO_PKG_VERSION"),
    );
    let mut session = ChatSession::new(&config, transport, auth);
    let interactive = args.script.is_none() && io::stdin().is_terminal();
    let mut failed = 0usize;
    let mut turn = 0usize;
    for line in lines {
        if interactive {
            print!("you> ");
            let _ = io::stdout().flush();
        }
        let line = match line {
            Ok(l) => l,
            Err(e) => {
                eprintln!("cannot read input: {}", e.kind());
                return ExitCode::from(2);
            }
        };
        let text = line.trim();
        if text.is_empty() || text.starts_with('#') {
            continue;
        }
        turn += 1;
        let started = Instant::now();
        let result = session.send(text);
        let status = session.transport().last_status;
        match &result {
            Ok(reply) => {
                println!("assistant> {}", reply.content);
                let mut notes = Vec::new();
                if reply.truncated {
                    notes.push("truncated at max_tokens".to_string());
                }
                if let Some(n) = reply.reasoning_chars {
                    notes.push(format!("separate reasoning omitted: {n} chars"));
                }
                if !notes.is_empty() {
                    println!("  [{}]", notes.join("; "));
                }
            }
            Err(e) => {
                failed += 1;
                eprintln!("turn {turn} failed [{}]: {e}", e.kind());
            }
        }
        if let Some(t) = transcript.as_mut()
            && let Err(e) = t.turn(text.chars().count(), &result, status, started.elapsed())
        {
            eprintln!("cannot write transcript: {}", e.kind());
            return ExitCode::from(2);
        }
    }
    if let Some(t) = transcript
        && let Err(e) = t.finish()
    {
        eprintln!("cannot write transcript: {}", e.kind());
        return ExitCode::from(2);
    }
    eprintln!("rose-chat: {turn} turns, {failed} failed");
    if failed > 0 {
        ExitCode::from(4)
    } else {
        ExitCode::SUCCESS
    }
}

fn fixture_demo() -> ExitCode {
    let config = Config::from_toml(FIXTURE_CONFIG).expect("built-in fixture config is valid");
    let model = config.model.as_str().to_string();
    let transport = ScriptedTransport::new([
        Step::wire_ok(
            &model,
            "Fixture reply 1: noted, your synthetic code word is MAPLE.",
        ),
        Step::wire_ok(
            &model,
            "Fixture reply 2: the code word you gave earlier was MAPLE.",
        ),
    ]);
    let mut session = ChatSession::new(&config, transport, None);
    println!("FIXTURE DEMO: in-process scripted transport. No socket, endpoint or model is used;");
    println!("this shows client history and wire-shape handling only, not a model result.");
    println!("model.id (fixture label) = {model}\n");
    for text in [
        "Remember the synthetic code word MAPLE.",
        "What code word did I give you?",
    ] {
        println!("user> {text}");
        match session.send(text) {
            Ok(reply) => println!(
                "fixture> {} [finish={:?}]",
                reply.content, reply.finish_reason
            ),
            Err(e) => return chat_failure(&e),
        }
    }
    let last = session
        .transport()
        .wire_bodies
        .last()
        .expect("two wire requests were made");
    println!("\nsecond request body (candidate wire shape):\n{last}");
    println!("\nhistory: {} committed messages", session.history().len());
    ExitCode::SUCCESS
}

fn chat_failure(e: &ChatError) -> ExitCode {
    eprintln!("chat error [{}]: {e}", e.kind());
    ExitCode::from(4)
}
