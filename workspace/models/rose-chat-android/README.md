# rose-chat-android

Thin JNI adapter that exposes Core's `local-agent-v1` `Agent`
(`workspace/models/rose-chat-core`, built with `--no-default-features`) to
`dev.pixel.models.NativeAgent` in the Rose Models app. It has three exports: `nativeCreate(config)`,
`nativeStep(handle, input)` and `nativeDestroy(handle)`. JSON passes through unchanged, and strings
cross as UTF-16. Panics never reach the JVM: they become `rejected` outputs or an
`IllegalArgumentException` on create. Each handle holds the agent behind a mutex. It contains no
agent logic.

```sh
cargo test --locked                                         # adapter unit tests (host)
bash ../android/tests/run.sh                                # JVM host tests through this adapter
bash ../android/build-native.sh                             # arm64-v8a .so with an existing NDK
```
