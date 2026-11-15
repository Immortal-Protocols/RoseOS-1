// Rose's JNI binding. llama.cpp is separately pinned and retains its MIT notice in the bundle.
#include <jni.h>
#include "llama.h"
#include <algorithm>
#include <chrono>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
void quiet(ggml_log_level, const char *, void *) {} // No prompts, labels or model diagnostics in device logs.
struct Failure { const char *category; };
void refuse(JNIEnv *env, const char *category) {
    jclass cls = env->FindClass("rose/models/composer/Refused");
    if (cls) env->ThrowNew(cls, category);
}
std::string bytes(JNIEnv *env, jbyteArray value) {
    if (!value) throw Failure{"bad_request"};
    auto n = env->GetArrayLength(value);
    std::string s(n, '\0');
    env->GetByteArrayRegion(value, 0, n, reinterpret_cast<jbyte *>(&s[0]));
    if (env->ExceptionCheck()) throw Failure{"model_unavailable"};
    return s;
}
struct Abort {
    JavaVM *vm = nullptr;
    jobject cancellation;
    jmethodID cancelled;
    Clock::time_point deadline;
    bool wasCancelled = false;
    Abort(JNIEnv *env, jobject c, jlong ms) : cancellation(env->NewGlobalRef(c)),
        deadline(Clock::now() + std::chrono::milliseconds(std::max<jlong>(0, ms))) {
        env->GetJavaVM(&vm);
        jclass cls = env->GetObjectClass(c);
        cancelled = env->GetMethodID(cls, "isCancelled", "()Z");
        env->DeleteLocalRef(cls);
    }
    ~Abort() {
        JNIEnv *env = nullptr;
        if (vm->GetEnv(reinterpret_cast<void **>(&env), JNI_VERSION_1_6) == JNI_OK)
            env->DeleteGlobalRef(cancellation);
    }
    static bool check(void *data) {
        auto &a = *static_cast<Abort *>(data);
        if (a.wasCancelled) return true;
        JNIEnv *env = nullptr;
        bool attached = false;
        if (a.vm->GetEnv(reinterpret_cast<void **>(&env), JNI_VERSION_1_6) != JNI_OK) {
            #ifdef __ANDROID__
            if (a.vm->AttachCurrentThread(&env, nullptr) != JNI_OK)
#else
            if (a.vm->AttachCurrentThread(reinterpret_cast<void **>(&env), nullptr) != JNI_OK)
#endif
                return true;
            attached = true;
        }
        a.wasCancelled = env->CallBooleanMethod(a.cancellation, a.cancelled);
        if (env->ExceptionCheck()) { env->ExceptionClear(); a.wasCancelled = true; }
        if (attached) a.vm->DetachCurrentThread();
        return a.wasCancelled || Clock::now() >= a.deadline;
    }
    static bool progress(float, void *data) { return !check(data); }
    void poll() { if (check(this)) throw Failure{wasCancelled ? "cancelled" : "timeout"}; }
};
struct Session {
    llama_model *model = nullptr;
    llama_context *ctx = nullptr;
    ~Session() { if (ctx) llama_free(ctx); if (model) llama_model_free(model); }
};
using Sampler = std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)>;

// Tokenize user data without recognizing special tokens: labels and requests cannot close the chat turn.
void append(const llama_vocab *vocab, std::vector<llama_token> &out, const std::string &text, bool special) {
    int n = llama_tokenize(vocab, text.data(), text.size(), nullptr, 0, false, special);
    if (n > 0) throw Failure{"model_unavailable"};
    std::vector<llama_token> tokens(-n);
    n = llama_tokenize(vocab, text.data(), text.size(), tokens.data(), tokens.size(), false, special);
    if (n < 0) throw Failure{"model_unavailable"};
    out.insert(out.end(), tokens.begin(), tokens.begin() + n);
}
}

extern "C" JNIEXPORT jlong JNICALL Java_rose_models_composer_LlamaInference_nativeOpen(
        JNIEnv *env, jclass, jbyteArray path, jlong timeout, jobject cancellation) {
    try {
        static std::once_flag backend;
        std::call_once(backend, [] { llama_log_set(quiet, nullptr); llama_backend_init(); });
        Abort abort(env, cancellation, timeout); abort.poll();
        std::unique_ptr<Session> s(new Session());
        auto mp = llama_model_default_params();
        #ifdef __APPLE__
        mp.n_gpu_layers = 99;
#else
        mp.n_gpu_layers = 0;
#endif
        mp.progress_callback = Abort::progress; mp.progress_callback_user_data = &abort;
        s->model = llama_model_load_from_file(bytes(env, path).c_str(), mp);
        abort.poll();
        if (!s->model) throw Failure{"model_unavailable"};
        auto cp = llama_context_default_params();
        cp.n_ctx = 2048; cp.n_batch = 512; cp.n_ubatch = 128;
        cp.n_threads = 4; cp.n_threads_batch = 4; cp.offload_kqv = true; cp.op_offload = true;
        cp.abort_callback = Abort::check; cp.abort_callback_data = &abort;
        s->ctx = llama_init_from_model(s->model, cp);
        abort.poll();
        if (!s->ctx) throw Failure{"model_unavailable"};
        llama_set_abort_callback(s->ctx, nullptr, nullptr);
        return reinterpret_cast<jlong>(s.release());
    } catch (const Failure &e) { refuse(env, e.category); }
      catch (...) { refuse(env, "model_unavailable"); }
    return 0;
}

extern "C" JNIEXPORT jbyteArray JNICALL Java_rose_models_composer_LlamaInference_nativeGenerate(
        JNIEnv *env, jclass, jlong handle, jbyteArray system, jbyteArray user, jbyteArray grammar,
        jint maxTokens, jlong timeout, jobject cancellation) {
    auto *s = reinterpret_cast<Session *>(handle);
    try {
        if (!s || maxTokens < 1 || maxTokens > 512) throw Failure{"bad_request"};
        Abort abort(env, cancellation, timeout); abort.poll();
        llama_set_abort_callback(s->ctx, Abort::check, &abort);
        // Unset callback before its stack state dies, including on every exception.
        struct Reset { llama_context *ctx; ~Reset() { llama_set_abort_callback(ctx, nullptr, nullptr); } } reset{s->ctx};
        llama_memory_clear(llama_get_memory(s->ctx), true);
        const auto *vocab = llama_model_get_vocab(s->model);
        std::vector<llama_token> prompt;
        append(vocab, prompt, "<|im_start|>system\n", true);
        append(vocab, prompt, bytes(env, system), false);
        append(vocab, prompt, "<|im_end|>\n<|im_start|>user\n", true);
        append(vocab, prompt, bytes(env, user), false);
        // Qwen3's documented non-thinking prefix. Grammar starts at the first JSON token.
        append(vocab, prompt, "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n", true);
        if (prompt.size() + maxTokens > llama_n_ctx(s->ctx)) throw Failure{"over_limit"};
        Sampler chain(llama_sampler_chain_init(llama_sampler_chain_default_params()), llama_sampler_free);
        auto gbnf = bytes(env, grammar);
        auto *constrained = llama_sampler_init_grammar(vocab, gbnf.c_str(), "root");
        if (!constrained) throw Failure{"invalid_output"};
        llama_sampler_chain_add(chain.get(), constrained);
        llama_sampler_chain_add(chain.get(), llama_sampler_init_greedy());
        for (size_t offset = 0; offset < prompt.size();) {
            abort.poll();
            int n = std::min<size_t>(128, prompt.size() - offset);
            if (llama_decode(s->ctx, llama_batch_get_one(prompt.data() + offset, n)) != 0) {
                abort.poll(); throw Failure{"model_unavailable"};
            }
            offset += n;
        }
        std::string text;
        for (int i = 0; i <= maxTokens; ++i) {
            abort.poll();
            llama_token token = llama_sampler_sample(chain.get(), s->ctx, -1);
            if (llama_vocab_is_eog(vocab, token)) {
                auto result = env->NewByteArray(text.size());
                if (result) env->SetByteArrayRegion(result, 0, text.size(), reinterpret_cast<const jbyte *>(text.data()));
                return result;
            }
            if (i == maxTokens) break;
            char piece[256];
            int n = llama_token_to_piece(vocab, token, piece, sizeof(piece), 0, false);
            if (n < 0) throw Failure{"invalid_output"};
            text.append(piece, n);
            if (text.size() > 16384) throw Failure{"over_limit"};
            if (llama_decode(s->ctx, llama_batch_get_one(&token, 1)) != 0) {
                abort.poll(); throw Failure{"model_unavailable"};
            }
        }
        throw Failure{"invalid_output"};
    } catch (const Failure &e) { refuse(env, e.category); }
      catch (...) { refuse(env, "model_unavailable"); }
    return nullptr;
}

extern "C" JNIEXPORT void JNICALL Java_rose_models_composer_LlamaInference_nativeClose(JNIEnv *, jclass, jlong handle) {
    delete reinterpret_cast<Session *>(handle);
}
