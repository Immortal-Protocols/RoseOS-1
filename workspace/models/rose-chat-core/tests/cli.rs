//! CLI exit codes, the FIXTURE demonstration and scripted chat against the QA loopback fixture.
//! Loopback results prove client behaviour only, not any model or endpoint.

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

/// QA's loopback fixture (qa/models/fixture_server.py); killed on drop. LOOPBACK FIXTURE only.
struct Fixture(std::process::Child, String);

impl Fixture {
    fn start() -> Fixture {
        use std::io::BufRead;
        let script =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../qa/models/fixture_server.py");
        let mut child = Command::new("python3")
            .arg("-I")
            .arg(script)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("python3 runs the QA fixture");
        let mut line = String::new();
        std::io::BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        let v: serde_json::Value = serde_json::from_str(&line).unwrap();
        let base = v["base"].as_str().unwrap().to_string();
        Fixture(child, base)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn chat_config(name: &str, base: &str, auth: &str) -> PathBuf {
    temp_config(
        name,
        &format!(
            "[endpoint]\nbase_url = \"{base}\"\nprotocol = \"openai-chat-candidate\"\n\
             [model]\nid = \"cli-loopback-label\"\n[timeouts]\nconnect_ms = 500\nrequest_ms = 2000\n{auth}"
        ),
    )
}

/// Transcript lines with run-specific timing fields removed.
fn stable_transcript(path: &PathBuf) -> Vec<serde_json::Value> {
    fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(|l| {
            let mut v: serde_json::Value = serde_json::from_str(l).unwrap();
            let o = v.as_object_mut().unwrap();
            for k in [
                "started_unix_ms",
                "finished_unix_ms",
                "elapsed_ms",
                "request_url",
            ] {
                o.remove(k);
            }
            v
        })
        .collect()
}

#[test]
fn chat_runs_a_scripted_two_turn_conversation_with_a_metadata_transcript() {
    let fx = Fixture::start();
    let cfg = chat_config("chat-ok.toml", &format!("{}/s/ok/v1", fx.1), "");
    let script = temp_config(
        "turns.txt",
        "# synthetic\nfirst synthetic turn\n\nsecond synthetic turn\n",
    );
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    let (t1, t2) = (dir.join("t1.jsonl"), dir.join("t2.jsonl"));
    let run = |t: &PathBuf| {
        rose_chat(
            &[
                "chat",
                "--config",
                cfg.to_str().unwrap(),
                "--script",
                script.to_str().unwrap(),
                "--transcript",
                t.to_str().unwrap(),
                "--label",
                "loopback-test",
            ],
            &[],
        )
    };
    let out = run(&t1);
    assert_eq!(out.status.code(), Some(0), "{}", text(&out.stderr));
    let stdout = text(&out.stdout);
    assert!(
        stdout.contains("assistant> fixture reply 1 (received 1 messages)"),
        "{stdout}"
    );
    assert!(
        stdout.contains("assistant> fixture reply 2 (received 3 messages)"),
        "{stdout}"
    );
    assert!(text(&out.stderr).contains("2 turns, 0 failed"));

    let raw = fs::read_to_string(&t1).unwrap();
    assert!(
        !raw.contains("synthetic turn"),
        "transcript must not record message text"
    );
    assert!(
        !raw.contains("fixture reply"),
        "transcript must not record replies"
    );
    let lines = stable_transcript(&t1);
    assert_eq!(lines.len(), 4);
    assert_eq!(lines[0]["type"], "run");
    assert_eq!(lines[0]["model_id"], "cli-loopback-label");
    assert_eq!(lines[0]["label"], "loopback-test");
    assert_eq!(lines[0]["content_recorded"], false);
    assert_eq!(lines[1]["outcome"], "ok");
    assert_eq!(lines[1]["http_status"], 200);
    assert_eq!(lines[2]["served_model"], "cli-loopback-label");
    assert_eq!(lines[3]["failed"], 0);

    // C15: the same script, config and fixture give the same transcript apart from timing.
    let fx2 = Fixture::start();
    let cfg2 = chat_config("chat-ok.toml", &format!("{}/s/ok/v1", fx2.1), "");
    assert_eq!(cfg, cfg2);
    let out = run(&t2);
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(stable_transcript(&t2), lines);
}

#[test]
fn chat_exit_4_on_failed_turns_and_3_on_missing_credential() {
    let fx = Fixture::start();
    let cfg = chat_config("chat-503.toml", &format!("{}/s/503/v1", fx.1), "");
    let script = temp_config("one.txt", "hello\n");
    let t = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("t503.jsonl");
    let out = rose_chat(
        &[
            "chat",
            "--config",
            cfg.to_str().unwrap(),
            "--script",
            script.to_str().unwrap(),
            "--transcript",
            t.to_str().unwrap(),
        ],
        &[],
    );
    assert_eq!(out.status.code(), Some(4));
    assert!(text(&out.stderr).contains("turn 1 failed [server]"));
    let lines = stable_transcript(&t);
    assert_eq!(lines[1]["outcome"], "server");
    assert_eq!(lines[1]["http_status"], 503);

    let cfg = chat_config(
        "chat-auth.toml",
        &format!("{}/s/ok/v1", fx.1),
        "[auth]\nmode = \"bearer-env\"\nenv_var = \"ROSE_CHAT_TEST_UNSET_TOKEN\"\n",
    );
    let out = rose_chat(
        &[
            "chat",
            "--config",
            cfg.to_str().unwrap(),
            "--script",
            script.to_str().unwrap(),
        ],
        &[],
    );
    assert_eq!(out.status.code(), Some(3));
    assert!(text(&out.stderr).contains("ROSE_CHAT_TEST_UNSET_TOKEN"));
}

#[test]
fn chat_reads_turns_from_stdin() {
    use std::io::Write as _;
    let fx = Fixture::start();
    let cfg = chat_config("chat-stdin.toml", &format!("{}/s/ok/v1", fx.1), "");
    let mut child = Command::new(env!("CARGO_BIN_EXE_rose-chat"))
        .args(["chat", "--config", cfg.to_str().unwrap()])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(b"one\ntwo\n")
        .unwrap();
    let out = child.wait_with_output().unwrap();
    assert_eq!(out.status.code(), Some(0), "{}", text(&out.stderr));
    assert!(text(&out.stdout).contains("fixture reply 2 (received 3 messages)"));
}
