//! Process-level transcript durability: a completed turn's record is on disk before the next turn
//! starts and survives SIGINT/SIGTERM during that later turn. Runs the real `rose-chat` binary
//! against QA's loopback fixture (`qa/models/fixture_server.py`). The signal is sent only after the
//! fixture has received the second request, so the test synchronizes on fixture state, not on a
//! guessed delay. LOOPBACK FIXTURE only: no model or endpoint.
#![cfg(unix)]

use std::fs;
use std::io::{BufRead, BufReader, Read};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

const SIGINT: i32 = 2;
const SIGTERM: i32 = 15;

struct Fixture {
    child: Child,
    base: String,
}

impl Fixture {
    fn start() -> Fixture {
        let script =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../qa/models/fixture_server.py");
        let mut child = Command::new("python3")
            .arg("-I")
            .arg(script)
            .args(["--hold", "30"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("python3 runs the QA fixture");
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        let info: Value = serde_json::from_str(&line).unwrap();
        Fixture {
            child,
            base: info["base"].as_str().unwrap().to_string(),
        }
    }

    fn request_count(&self) -> usize {
        let agent: ureq::Agent = ureq::Agent::config_builder().proxy(None).build().into();
        let body = agent
            .get(&format!("{}/__qa/requests", self.base))
            .call()
            .unwrap()
            .body_mut()
            .read_to_string()
            .unwrap();
        let v: Value = serde_json::from_str(&body).unwrap();
        v["requests"].as_array().map_or(0, Vec::len)
    }

    /// Blocks until the fixture has received `n` requests (bounded, fails loudly).
    fn wait_for_requests(&self, n: usize) {
        let limit = Instant::now() + Duration::from_secs(15);
        while self.request_count() < n {
            assert!(
                Instant::now() < limit,
                "fixture never received {n} requests"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn work_dir(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn write_inputs(dir: &Path, base: &str, scenario: &str, turns: &str) -> (PathBuf, PathBuf) {
    let config = dir.join("config.toml");
    fs::write(
        &config,
        format!(
            "[endpoint]\nbase_url = \"{base}/s/{scenario}/v1\"\nprotocol = \"openai-chat-candidate\"\n\
             [model]\nid = \"signal-test-label\"\n[timeouts]\nconnect_ms = 1000\nrequest_ms = 30000\n"
        ),
    )
    .unwrap();
    let script = dir.join("turns.txt");
    fs::write(&script, turns).unwrap();
    (config, script)
}

fn spawn_chat(config: &Path, script: &Path, transcript: &Path) -> Child {
    Command::new(env!("CARGO_BIN_EXE_rose-chat"))
        .args(["chat", "--config"])
        .arg(config)
        .arg("--script")
        .arg(script)
        .arg("--transcript")
        .arg(transcript)
        .env_remove("ROSE_CHAT_BASE_URL")
        .env_remove("ROSE_CHAT_REQUEST_URL")
        .env_remove("ROSE_CHAT_MODEL_ID")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
}

/// Every line must be a complete JSON record terminated by a newline.
fn records(path: &Path) -> Vec<Value> {
    let raw = fs::read_to_string(path).unwrap();
    assert!(
        raw.is_empty() || raw.ends_with('\n'),
        "partial record: {raw:?}"
    );
    raw.lines()
        .map(|l| serde_json::from_str(l).unwrap_or_else(|e| panic!("bad record {l:?}: {e}")))
        .collect()
}

fn wait_with_limit(child: &mut Child, limit: Duration) -> ExitStatus {
    let end = Instant::now() + limit;
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            return status;
        }
        if Instant::now() > end {
            let _ = child.kill();
            panic!("rose-chat did not exit within {limit:?} of the signal");
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn interrupt_second_turn(signal: i32, flag: &str) {
    let fx = Fixture::start();
    let dir = work_dir(&format!("signal-{signal}"));
    let transcript = dir.join("transcript.jsonl");
    let (config, script) = write_inputs(
        &dir,
        &fx.base,
        "seq:ok,no-headers",
        "turn one\nturn two\nturn three\n",
    );
    let mut child = spawn_chat(&config, &script, &transcript);

    // Turn 2 is held open by the fixture. Its arrival proves turn 1 finished in the client.
    fx.wait_for_requests(2);
    let before = records(&transcript);
    assert_eq!(
        before.len(),
        2,
        "turn 1 not on disk before turn 2: {before:?}"
    );

    let sent = Instant::now();
    let status = Command::new("kill")
        .args([flag, &child.id().to_string()])
        .status()
        .unwrap();
    assert!(status.success());
    let status = wait_with_limit(&mut child, Duration::from_secs(2));
    let took = sent.elapsed();

    assert_eq!(status.signal(), Some(signal), "{status:?}");
    assert_eq!(status.code(), None);
    let mut stdout = String::new();
    child
        .stdout
        .take()
        .unwrap()
        .read_to_string(&mut stdout)
        .unwrap();
    assert!(stdout.contains("assistant> fixture reply 1"), "{stdout}");

    let after = records(&transcript);
    assert_eq!(after, before, "records changed after the signal");
    assert_eq!(after[0]["type"], "run");
    assert_eq!(after[1]["type"], "turn");
    assert_eq!(after[1]["index"], 1);
    assert_eq!(after[1]["outcome"], "ok");
    assert!(
        after.iter().all(|r| r["type"] != "summary"),
        "no summary for an interrupted run"
    );
    let raw = fs::read_to_string(&transcript).unwrap();
    assert!(!raw.contains("turn one") && !raw.contains("fixture reply"));
    assert_eq!(fx.request_count(), 2, "turn 3 must not be sent");
    assert!(took < Duration::from_secs(1), "exit took {took:?}");
}

#[test]
fn sigint_during_second_turn_keeps_the_first_turn_record() {
    interrupt_second_turn(SIGINT, "-INT");
}

#[test]
fn sigterm_during_second_turn_keeps_the_first_turn_record() {
    interrupt_second_turn(SIGTERM, "-TERM");
}

#[test]
fn completed_and_failed_runs_end_with_a_summary() {
    let fx = Fixture::start();
    for (scenario, code, outcome) in [("ok", 0, "ok"), ("seq:ok,503", 4, "server")] {
        let dir = work_dir(&format!("complete-{code}"));
        let transcript = dir.join("transcript.jsonl");
        let (config, script) = write_inputs(&dir, &fx.base, scenario, "one\ntwo\n");
        let out = spawn_chat(&config, &script, &transcript)
            .wait_with_output()
            .unwrap();
        assert_eq!(out.status.code(), Some(code), "{scenario}");
        let r = records(&transcript);
        let types: Vec<_> = r.iter().map(|v| v["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["run", "turn", "turn", "summary"], "{scenario}");
        assert_eq!(r[1]["outcome"], "ok");
        assert_eq!(r[2]["outcome"], outcome, "{scenario}");
        assert_eq!(r[3]["failed"], u64::from(code == 4));
    }
}

#[cfg(target_os = "linux")]
#[test]
fn unwritable_transcript_fails_before_any_request() {
    let fx = Fixture::start();
    let dir = work_dir("devfull");
    let (config, script) = write_inputs(&dir, &fx.base, "ok", "one\n");
    let out = spawn_chat(&config, &script, Path::new("/dev/full"))
        .wait_with_output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("cannot write transcript"),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(fx.request_count(), 0);
}
