//! rose-chat: configuration check and an in-process FIXTURE demonstration.
//!
//! Exit codes: 0 success, 2 usage error, 3 configuration error, 4 chat error,
//! 5 live chat unavailable in this build.

use std::env;
use std::fs;
use std::process::ExitCode;

use rose_chat_core::auth::AuthMode;
use rose_chat_core::config::{Config, Overrides};
use rose_chat_core::fixture::{ScriptedTransport, Step};
use rose_chat_core::{ChatError, ChatSession};

const USAGE: &str = "usage:
  rose-chat config-check --config FILE [--base-url URL] [--model-id ID]
  rose-chat fixture-demo
  rose-chat chat --config FILE      (not available in this build)";

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
        Some("fixture-demo") if args.len() == 1 => fixture_demo(),
        Some("chat") => {
            eprintln!(
                "live chat is unavailable in this build: no network transport is implemented yet \
                 and no endpoint has been handed over. No request was made."
            );
            ExitCode::from(5)
        }
        _ => usage(),
    }
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

fn config_check(args: &[String]) -> ExitCode {
    let mut path = None;
    let mut cli = Overrides::default();
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        let Some(value) = it.next() else {
            return usage();
        };
        match flag.as_str() {
            "--config" => path = Some(value.clone()),
            "--base-url" => cli.base_url = Some(value.clone()),
            "--model-id" => cli.model_id = Some(value.clone()),
            _ => return usage(),
        }
    }
    let Some(path) = path else { return usage() };
    let text = match fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("configuration error: cannot read {path}: {}", e.kind());
            return ExitCode::from(3);
        }
    };
    let env_lookup = |k: &str| env::var(k).ok();
    let config = match Config::load(&text, &Overrides::from_env(&env_lookup), &cli) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::from(3);
        }
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
