//! Thin JNI adapter for `dev.pixel.models.NativeAgent`: create, step and destroy one Core
//! [`Agent`] (local-agent-v1). It adds no agent logic. JSON goes in and out unchanged, Rust panics
//! never cross into the JVM, and strings cross as UTF-16 so non-BMP text survives intact.
//!
//! Safety: the exports are `unsafe` because the JVM passes raw `JNIEnv` and handle pointers. They
//! must only be called by the JVM for `NativeAgent`, with a handle from `nativeCreate` that has not
//! been destroyed.
//!
//! Lifetime: Java owns the handle. `NativeAgent` calls destroy at most once and never steps after
//! it; a zero handle is answered with a `rejected` output.

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::ptr;
use std::sync::Mutex;

use jni_sys::{JNIEnv, jclass, jlong, jstring};
use rose_chat_core::agent::Agent;

/// One agent behind a mutex, so concurrent steps from Java threads serialize safely.
pub struct Handle {
    agent: Mutex<Agent>,
}

/// Builds an agent from local-agent-v1 config JSON.
pub fn create(config_json: &str) -> Result<Box<Handle>, String> {
    Agent::from_config_json(config_json)
        .map(|agent| {
            Box::new(Handle {
                agent: Mutex::new(agent),
            })
        })
        .map_err(|e| e.to_string())
}

/// One step. A poisoned lock (an earlier panic) is reported as `rejected`, not a crash.
pub fn step(handle: &Handle, input_json: &str) -> String {
    match handle.agent.lock() {
        Ok(mut agent) => agent.step_json(input_json),
        Err(_) => rejected("agent_unavailable"),
    }
}

/// A local-agent-v1 step result holding one `rejected` output.
pub fn rejected(reason: &str) -> String {
    let output = serde_json_rejected(reason);
    format!("{{\"contract\":\"local-agent-v1\",\"outputs\":[{output}]}}")
}

fn serde_json_rejected(reason: &str) -> String {
    let escaped: String = reason
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
        .collect();
    format!("{{\"type\":\"rejected\",\"reason\":\"{escaped}\"}}")
}

// ---- raw JNI glue ----

unsafe fn read_string(env: *mut JNIEnv, value: jstring) -> Option<String> {
    if value.is_null() {
        return None;
    }
    unsafe {
        let jni = &(**env).v1_1;
        let len = (jni.GetStringLength)(env, value);
        let chars = (jni.GetStringChars)(env, value, ptr::null_mut());
        if chars.is_null() {
            return None;
        }
        let units = std::slice::from_raw_parts(chars, len as usize);
        let text = String::from_utf16(units).ok();
        (jni.ReleaseStringChars)(env, value, chars);
        text
    }
}

unsafe fn new_string(env: *mut JNIEnv, text: &str) -> jstring {
    let units: Vec<u16> = text.encode_utf16().collect();
    unsafe { ((**env).v1_1.NewString)(env, units.as_ptr(), units.len() as i32) }
}

unsafe fn throw_illegal_argument(env: *mut JNIEnv, message: &str) {
    let mut bytes: Vec<u8> = message
        .bytes()
        .filter(|b| *b != 0 && b.is_ascii())
        .collect();
    bytes.push(0);
    unsafe {
        let jni = &(**env).v1_1;
        let class = (jni.FindClass)(env, c"java/lang/IllegalArgumentException".as_ptr());
        if !class.is_null() {
            (jni.ThrowNew)(env, class, bytes.as_ptr().cast());
        }
    }
}

/// `static native long nativeCreate(String configJson)`. Throws IllegalArgumentException on an
/// invalid config; the message names the problem and contains no secrets (config holds none).
///
/// # Safety
/// Called only by the JVM with a valid `JNIEnv`; see the module docs for the handle contract.
#[unsafe(no_mangle)]
pub unsafe extern "system" fn Java_dev_pixel_models_NativeAgent_nativeCreate(
    env: *mut JNIEnv,
    _class: jclass,
    config: jstring,
) -> jlong {
    let result = catch_unwind(AssertUnwindSafe(|| unsafe {
        match read_string(env, config) {
            None => Err("config is null or not valid UTF-16".to_string()),
            Some(text) => create(&text),
        }
    }));
    match result {
        Ok(Ok(handle)) => Box::into_raw(handle) as jlong,
        Ok(Err(message)) => {
            unsafe { throw_illegal_argument(env, &message) };
            0
        }
        Err(_) => {
            unsafe { throw_illegal_argument(env, "agent creation panicked") };
            0
        }
    }
}

/// `static native String nativeStep(long handle, String inputJson)`.
///
/// # Safety
/// Called only by the JVM with a valid `JNIEnv`; see the module docs for the handle contract.
#[unsafe(no_mangle)]
pub unsafe extern "system" fn Java_dev_pixel_models_NativeAgent_nativeStep(
    env: *mut JNIEnv,
    _class: jclass,
    handle: jlong,
    input: jstring,
) -> jstring {
    let output = catch_unwind(AssertUnwindSafe(|| unsafe {
        if handle == 0 {
            return rejected("agent_closed");
        }
        let handle = &*(handle as *const Handle);
        match read_string(env, input) {
            None => rejected("input_not_text"),
            Some(text) => step(handle, &text),
        }
    }))
    .unwrap_or_else(|_| rejected("agent_panicked"));
    unsafe { new_string(env, &output) }
}

/// `static native void nativeDestroy(long handle)`. Java guarantees at most one call per handle.
///
/// # Safety
/// Called only by the JVM with a valid `JNIEnv`; see the module docs for the handle contract.
#[unsafe(no_mangle)]
pub unsafe extern "system" fn Java_dev_pixel_models_NativeAgent_nativeDestroy(
    _env: *mut JNIEnv,
    _class: jclass,
    handle: jlong,
) {
    if handle != 0 {
        let _ = catch_unwind(|| unsafe { drop(Box::from_raw(handle as *mut Handle)) });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CONFIG: &str = r#"{"contract":"local-agent-v1","model":"m","allowlist":[{"app_id":"calculator","label":"Calculator"}]}"#;

    #[test]
    fn create_step_and_reject() {
        let handle = create(CONFIG).unwrap();
        let out = step(
            &handle,
            r#"{"type":"start_turn","turn_id":"t1","text":"hi"}"#,
        );
        assert!(out.contains("\"need_inference\""), "{out}");
        let again = step(
            &handle,
            r#"{"type":"start_turn","turn_id":"t2","text":"hi"}"#,
        );
        assert!(again.contains("\"rejected\""), "{again}");
        let bad = step(&handle, "not json");
        assert!(bad.contains("\"rejected\""), "{bad}");
    }

    #[test]
    fn invalid_config_is_an_error() {
        assert!(create("{}").is_err());
        assert!(
            create(r#"{"contract":"local-agent-v1","model":"m","allowlist":[],"extra":1}"#)
                .is_err()
        );
    }

    #[test]
    fn rejected_reason_is_sanitized() {
        assert_eq!(
            rejected("bad \"reason\"\n"),
            r#"{"contract":"local-agent-v1","outputs":[{"type":"rejected","reason":"badreason"}]}"#
        );
    }
}
