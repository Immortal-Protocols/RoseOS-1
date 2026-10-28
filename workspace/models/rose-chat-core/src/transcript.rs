//! Opt-in metadata-only run record (JSON Lines).
//!
//! It records identities and per-turn outcomes so a run can be compared and reproduced. It never
//! records message text, reasoning text, credential values or URL query strings.
//!
//! Each record is written to the file as one complete line with a single unbuffered write before
//! the next turn starts. A run killed by SIGINT/SIGTERM during a later turn therefore keeps every
//! earlier record. The interrupted turn and the summary are absent. Nothing is fsynced, so
//! durability across power loss or an OS crash is not claimed.

use std::fs::File;
use std::io::{self, Write};
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

use crate::config::{CANDIDATE_PROTOCOL, Config};
use crate::error::ChatError;
use crate::model::{FinishReason, Reply};

pub struct Transcript {
    out: File,
    turns: usize,
    failed: usize,
}

/// Source revision baked in at build time, if the builder set `ROSE_CHAT_SOURCE_REV`.
pub const SOURCE_REV: Option<&str> = option_env!("ROSE_CHAT_SOURCE_REV");

impl Transcript {
    pub fn create(path: &Path, config: &Config, label: Option<&str>) -> io::Result<Transcript> {
        let mut t = Transcript {
            out: File::create(path)?,
            turns: 0,
            failed: 0,
        };
        let url = config.endpoint.request_url();
        let url = url.split(['?', '#']).next().unwrap_or("");
        t.line(json!({
            "type": "run",
            "client": "rose-chat",
            "client_version": env!("CARGO_PKG_VERSION"),
            "source_rev": SOURCE_REV.unwrap_or("unrecorded"),
            "label": label,
            "protocol": CANDIDATE_PROTOCOL,
            "request_url": url,
            "model_id": config.model.as_str(),
            "expected_revision": config.expected_revision,
            "auth": config.auth.describe(),
            "started_unix_ms": unix_ms(),
            "content_recorded": false,
        }))?;
        Ok(t)
    }

    pub fn turn(
        &mut self,
        user_chars: usize,
        result: &Result<Reply, ChatError>,
        http_status: Option<u16>,
        elapsed: Duration,
    ) -> io::Result<()> {
        self.turns += 1;
        let mut record = json!({
            "type": "turn",
            "index": self.turns,
            "user_chars": user_chars,
            "http_status": http_status,
            "elapsed_ms": elapsed.as_millis() as u64,
        });
        match result {
            Ok(reply) => {
                record["outcome"] = json!("ok");
                record["reply_chars"] = json!(reply.content.chars().count());
                record["finish_reason"] = json!(finish_name(&reply.finish_reason));
                record["truncated"] = json!(reply.truncated);
                record["served_model"] = json!(reply.served_model);
                record["request_id"] = json!(reply.request_id);
                record["reasoning_chars"] = json!(reply.reasoning_chars);
                if let Some(u) = reply.usage {
                    record["prompt_tokens"] = json!(u.prompt_tokens);
                    record["completion_tokens"] = json!(u.completion_tokens);
                }
            }
            Err(e) => {
                self.failed += 1;
                record["outcome"] = json!(e.kind());
            }
        }
        self.line(record)
    }

    pub fn finish(mut self) -> io::Result<()> {
        let summary = json!({
            "type": "summary",
            "turns": self.turns,
            "ok": self.turns - self.failed,
            "failed": self.failed,
            "finished_unix_ms": unix_ms(),
        });
        self.line(summary)
    }

    /// One record, written whole, with no user-space buffering.
    fn line(&mut self, value: Value) -> io::Result<()> {
        self.out.write_all(format!("{value}\n").as_bytes())
    }
}

fn finish_name(f: &FinishReason) -> String {
    match f {
        FinishReason::Stop => "stop".into(),
        FinishReason::Length => "length".into(),
        FinishReason::Other(o) => o.clone(),
        FinishReason::Unreported => "unreported".into(),
    }
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
