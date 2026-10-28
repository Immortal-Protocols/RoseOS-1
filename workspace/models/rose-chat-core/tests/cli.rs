//! CLI exit codes and the FIXTURE demonstration. No network request is made by any command.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

fn rose_chat(args: &[&str], env: &[(&str, &str)]) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_rose-chat"));
    cmd.args(args)
        .env_remove("ROSE_CHAT_MODEL_ID")
        .env_remove("ROSE_CHAT_BASE_URL");
    for (k, v) in env {
        cmd.env(k, v);
    }
    cmd.output().expect("rose-chat runs")
}

fn temp_config(name: &str, text: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    let path = dir.join(name);
    fs::write(&path, text).unwrap();
    path
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

#[test]
fn fixture_demo_runs_two_labelled_turns() {
    let out = rose_chat(&["fixture-demo"], &[]);
    assert_eq!(out.status.code(), Some(0), "{}", text(&out.stderr));
    let stdout = text(&out.stdout);
    assert!(stdout.starts_with("FIXTURE DEMO"), "{stdout}");
    assert!(stdout.contains("not a model result"));
    assert!(stdout.contains("history: 4 committed messages"));
    // The second request on the wire carries the first exchange.
    assert!(stdout.contains("Remember the synthetic code word MAPLE."));
    assert!(stdout.contains("Fixture reply 1"));
}

#[test]
fn config_check_ok_and_reports_without_values() {
    let path = temp_config(
        "ok.toml",
        "[endpoint]\nbase_url = \"http://127.0.0.1:9\"\nprotocol = \"openai-chat-candidate\"\n\
         [model]\nid = \"file-model\"\n[auth]\nmode = \"bearer-env\"\nenv_var = \"ROSE_CHAT_CLI_TEST_TOKEN\"\n",
    );
    let p = path.to_str().unwrap();
    let out = rose_chat(
        &["config-check", "--config", p],
        &[("ROSE_CHAT_CLI_TEST_TOKEN", "tok-SENSITIVE-123")],
    );
    assert_eq!(out.status.code(), Some(0), "{}", text(&out.stderr));
    let stdout = text(&out.stdout);
    assert!(stdout.contains("model.id = file-model"));
    assert!(stdout.contains("ROSE_CHAT_CLI_TEST_TOKEN is set"));
    assert!(!stdout.contains("tok-SENSITIVE-123"));

    // Precedence: CLI flag over environment over file.
    let out = rose_chat(
        &["config-check", "--config", p],
        &[("ROSE_CHAT_MODEL_ID", "env-model")],
    );
    assert!(text(&out.stdout).contains("model.id = env-model"));
    let out = rose_chat(
        &["config-check", "--config", p, "--model-id", "cli-model"],
        &[("ROSE_CHAT_MODEL_ID", "env-model")],
    );
    assert!(text(&out.stdout).contains("model.id = cli-model"));
}

#[test]
fn config_errors_exit_3() {
    let path = temp_config(
        "no-model.toml",
        "[endpoint]\nbase_url = \"http://127.0.0.1:9\"\nprotocol = \"openai-chat-candidate\"\n",
    );
    let out = rose_chat(&["config-check", "--config", path.to_str().unwrap()], &[]);
    assert_eq!(out.status.code(), Some(3));
    assert!(text(&out.stderr).contains("model.id is required"));

    let out = rose_chat(
        &["config-check", "--config", "/nonexistent/rose-chat.toml"],
        &[],
    );
    assert_eq!(out.status.code(), Some(3));
}

#[test]
fn usage_and_config_exit_codes() {
    assert_eq!(rose_chat(&[], &[]).status.code(), Some(2));
    assert_eq!(rose_chat(&["config-check"], &[]).status.code(), Some(2));
    assert_eq!(
        rose_chat(&["config-check", "--bogus", "x"], &[])
            .status
            .code(),
        Some(2)
    );
    assert_eq!(rose_chat(&["chat"], &[]).status.code(), Some(2));
    assert_eq!(
        rose_chat(&["config-check", "--config", "x", "--script", "y"], &[])
            .status
            .code(),
        Some(2),
        "--script is a chat-only flag"
    );
    let out = rose_chat(&["chat", "--config", "/nonexistent/x.toml"], &[]);
    assert_eq!(out.status.code(), Some(3));
}
