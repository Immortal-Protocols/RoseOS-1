use std::fmt;
use std::process::{Command, Stdio};

use crate::error::ChatError;

/// A credential value. It is never printed: `Debug` and `Display` both show `<redacted>`.
#[derive(Clone, PartialEq, Eq)]
pub struct Secret(String);

impl Secret {
    pub fn new(value: impl Into<String>) -> Self {
        Secret(value.into())
    }

    /// The raw value, for a transport to put in a request header. Never log it.
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secret(<redacted>)")
    }
}

impl fmt::Display for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("<redacted>")
    }
}

/// How the client obtains a bearer credential at runtime. Configuration names where the
/// credential comes from; it never contains the credential itself.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthMode {
    /// No Authorization header (loopback fixtures, local servers).
    None,
    /// Read the token from this environment variable when a session starts.
    BearerEnv { var: String },
    /// Run this command (no shell) and use its trimmed stdout as the token.
    BearerCommand { argv: Vec<String> },
}

impl AuthMode {
    /// Resolve the credential. `env` is injected so tests do not touch the process environment.
    /// Errors name the source (variable or program) and never include its output.
    pub fn resolve(
        &self,
        env: &dyn Fn(&str) -> Option<String>,
    ) -> Result<Option<Secret>, ChatError> {
        match self {
            AuthMode::None => Ok(None),
            AuthMode::BearerEnv { var } => match env(var) {
                Some(v) if !v.trim().is_empty() => Ok(Some(Secret::new(v.trim()))),
                _ => Err(ChatError::Auth(format!(
                    "environment variable {var} is unset or empty"
                ))),
            },
            AuthMode::BearerCommand { argv } => {
                let Some((program, args)) = argv.split_first() else {
                    return Err(ChatError::Auth("auth command is empty".into()));
                };
                let output = Command::new(program)
                    .args(args)
                    .stdin(Stdio::null())
                    .stderr(Stdio::null())
                    .output()
                    .map_err(|e| {
                        ChatError::Auth(format!("could not run {program}: {}", e.kind()))
                    })?;
                if !output.status.success() {
                    return Err(ChatError::Auth(format!(
                        "{program} exited unsuccessfully ({})",
                        output.status
                    )));
                }
                let token = String::from_utf8(output.stdout)
                    .map_err(|_| ChatError::Auth(format!("{program} printed non-UTF-8 output")))?;
                let token = token.trim();
                if token.is_empty() {
                    return Err(ChatError::Auth(format!("{program} printed no token")));
                }
                Ok(Some(Secret::new(token)))
            }
        }
    }

    pub fn describe(&self) -> String {
        match self {
            AuthMode::None => "none".into(),
            AuthMode::BearerEnv { var } => format!("bearer-env (variable {var})"),
            AuthMode::BearerCommand { argv } => format!(
                "bearer-command (program {})",
                argv.first().map_or("(empty)", String::as_str)
            ),
        }
    }
}
