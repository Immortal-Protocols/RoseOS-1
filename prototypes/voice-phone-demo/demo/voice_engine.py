"""Nemotron VoiceChat streaming session with live tool calling.

mlx-vlm's ``VoiceChatStreamingSession`` exposes the model's function channel
but has no way to hand a tool result back. This module adds that, following
NVIDIA's reference (NeMo ``duplex_stt_model.py``):

* The model writes ``<SPECIAL_20> <TOOLCALL>[...]</TOOLCALL> <SPECIAL_21>`` on
  its function channel, one token per 80 ms frame.
* After ``<SPECIAL_21>`` (end of tool call) the caller runs the tool and calls
  ``inject_tool_response``. The response tokens
  ``<TOOL_RESPONSE>[...]</TOOL_RESPONSE>`` are forced onto the function channel
  in extra timeline steps with silent user audio and padded agent text, the
  same layout NeMo inserts during training. No speech is produced for those
  steps; the TTS stream continues as if they did not happen.
* Normal audio frames then resume and the model talks about the result.

The tool call freezes the timeline: audio that arrives while the tool runs is
discarded by the caller (NVIDIA documents that the user cannot interrupt a tool
call).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import mlx.core as mx
from mlx_audio.stt.models.nemotron_asr.audio import log_mel_spectrogram
from mlx_vlm.models.nemotron_voicechat.streaming import (
    VoiceChatEvent,
    VoiceChatStreamingSession,
)

SOTC = "<SPECIAL_20>"  # start of tool call
EOTC = "<SPECIAL_21>"  # end of tool call
EOTR = "<SPECIAL_22>"  # end of tool response


def tools_system_prompt(system_message: str, tools: list[dict]) -> str:
    """Render NVIDIA's function_calling/template.jinja for VoiceChat."""

    text = system_message
    if not tools:
        return text
    if text:
        text += "\n\n"
    rendered = ", ".join(
        json.dumps({k: v for k, v in tool.items() if k != "type"}) for tool in tools
    )
    text += "You can use the following tools to assist the user if required:"
    text += f"\n<AVAILABLE_TOOLS>[{rendered}]</AVAILABLE_TOOLS>\n\n"
    text += "If you decide to call any tool(s), use the following format:\n"
    text += '<TOOLCALL>[{"name": "tool_name1", "arguments": "tool_args1"}, '
    text += '{"name": "tool_name2", "arguments": "tool_args2"}]'
    text += "</TOOLCALL>\n\n"
    text += "The user will execute tool-calls and return responses from tool(s) in this format:\n"
    text += '<TOOL_RESPONSE>[{"tool_response1"}, {"tool_response2"}]</TOOL_RESPONSE>\n\n'
    text += (
        "Based on the tool responses, you can call additional tools if needed, "
        "correct tool calls if any errors are found, or just respond to the user."
    )
    if not text.isascii():
        raise ValueError("VoiceChat system prompts must be ASCII")
    return text


@dataclass
class ToolCall:
    raw: str
    name: str = ""
    arguments: dict = field(default_factory=dict)
    error: str | None = None


def parse_tool_call(raw: str) -> list[ToolCall]:
    text = raw.replace(SOTC, "").replace(EOTC, "").strip()
    if "<TOOLCALL>" in text:
        text = text.split("<TOOLCALL>", 1)[1].split("</TOOLCALL>", 1)[0].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [ToolCall(raw=raw, error=f"unparseable tool call: {exc}")]
    calls = []
    for item in data if isinstance(data, list) else [data]:
        args = item.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"text": args}
        calls.append(ToolCall(raw=raw, name=item.get("name", ""), arguments=args))
    return calls


def _force(logits: mx.array, token_id: int) -> mx.array:
    """Logits that make ``token_id`` the argmax."""

    return mx.where(mx.arange(logits.shape[-1]) == token_id, 1e4, -1e4).astype(
        logits.dtype
    )[None, None]


def _first_call_closed(raw: str) -> bool:
    """True once the first object in ``<TOOLCALL>[{...}`` is complete
    and the model has not closed the list itself."""

    start = raw.find("[")
    if start < 0:
        return False
    depth, in_string, escaped = 0, False, False
    for i, ch in enumerate(raw[start + 1 :], start + 1):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "]" not in raw[i + 1 :]
    return False


def ascii_only(text: str) -> str:
    """VoiceChat requires ASCII tool responses; fold common punctuation."""

    table = {"—": "-", "–": "-", "‘": "'", "’": "'",
             "“": '"', "”": '"', "…": "...", " ": " "}
    for src, dst in table.items():
        text = text.replace(src, dst)
    return text.encode("ascii", "ignore").decode("ascii")


class ToolStreamingSession(VoiceChatStreamingSession):
    """A ``VoiceChatStreamingSession`` that detects and answers tool calls.

    ``push_audio`` returns the normal events plus a ``tool_call`` event (kind
    ``"function_delta"`` with ``delta=None`` and ``text`` set to the full call)
    once the model closes a call. Until ``inject_tool_response`` is called the
    session refuses further audio.
    """

    def __init__(self, parent, *, mute_function_channel: bool = False, **kwargs):
        tokenizer = parent.tokenizer
        self._sotc_id = tokenizer.convert_tokens_to_ids(SOTC)
        self._eotc_id = tokenizer.convert_tokens_to_ids(EOTC)
        self._eotr_id = tokenizer.convert_tokens_to_ids(EOTR)
        self._call_tokens: list[int] | None = None
        self._live = False  # no channel overrides during prompt prefill
        self.mute_function_channel = mute_function_channel
        self.single_call = True
        self.batched_inject = True
        self.hold_speech = False
        self.wants_to_speak = False
        self.paused = False  # Design C: timeline frozen while the decider rules
        # After a tool response: frames to wait for the agent to start talking
        # before its turn-start token is written for it (None = not waiting).
        self.speak_nudge_frames = 6
        self._await_speech: int | None = None
        self.nudges = 0
        # Unprompted turns: the model tends to fill silence ("I am listening.").
        # With require_prompt, it may only start talking after floor_open is
        # set (the user spoke, or a tool result arrived); starting consumes it.
        self.require_prompt = True
        self.floor_open = True  # the greeting
        self.quiet_frames = 0  # agent text frames since it last said anything
        self.floor_after_frames = 12  # a pause this long ends the agent's turn
        self.suppressed = 0
        # Same for tool calls: one call per user turn, so it cannot loop on
        # its own results. Set with floor_open when the user speaks.
        self.call_open = True
        self.suppressed_calls = 0
        self.pending_call: str | None = None
        self.injection_ms: list[float] = []
        super().__init__(parent, **kwargs)
        self._silence_embedding = self._compute_silence_embedding()
        self._live = True

    def _prefill_prompt(self, system_prompt: str | None) -> None:
        """Same timeline as the base class, with the language model batched.

        Prompt steps have known inputs (prompt token + padded channels), so
        the LM runs once over all of them; the TTS still advances one step per
        prompt token with padded text, exactly as the base implementation.
        """

        prompt_text = (
            self.config.default_system_prompt if system_prompt is None else system_prompt
        )
        if not prompt_text.strip():
            return
        ids = [
            self.config.bos_token_id,
            *self.tokenizer.encode(prompt_text, add_special_tokens=False),
            self.config.eos_token_id,
        ]
        n, pad = len(ids), self.config.pad_token_id
        embed = self.model.stt_model.embed_tokens
        pads = embed(mx.array([[pad] * n], dtype=mx.int32))
        fused = pads + embed(mx.array([ids], dtype=mx.int32)) + self.config.function_channel_weight * pads
        output = self._language_step(fused)
        mx.eval(output.text_logits, output.function_logits)
        self._text_tokens.extend([pad] * n)
        self._function_tokens.extend([pad] * n)
        current = mx.array([[pad]], dtype=mx.int32)
        mask = mx.ones(current.shape, dtype=mx.bool_)
        for _ in range(n):
            if self._timeline_index > 0:
                self._previous_code = self.model.tts_model.tts_model.step(
                    self._previous_code, current, mask, self._tts_cache, guidance=True
                ).codes
                mx.eval(self._previous_code)
            self._timeline_index += 1

    def _compute_silence_embedding(self) -> mx.array:
        # Stateless (non-cached) perception pass over a silent window, so the
        # streaming encoder caches are untouched.
        window = mx.zeros(
            (self._perception_window_frames * self.frame_samples,), dtype=mx.float32
        )
        mel = log_mel_spectrogram(window, self.config.audio_config.preprocessor)
        lengths = mx.array([mel.shape[1]], dtype=mx.int32)
        projected, _, _ = self.model.stt_model.perception(mel, lengths)
        silence = projected[:, -2:-1]
        mx.eval(silence)
        return silence

    def _language_step(self, inputs: mx.array):
        output = super()._language_step(inputs)
        if not self._live:
            return output
        pad = self.config.pad_token_id
        mute_text = False
        if self.mute_function_channel:
            # Design B: the voice model never starts a tool call itself.
            output.function_logits = _force(output.function_logits, pad)
        elif (self.require_prompt and self._call_tokens is None and not self.call_open
              and self._in_call_next(output)):
            output.function_logits = _force(output.function_logits, pad)
            self.suppressed_calls += 1
        elif self._call_tokens is not None or self._in_call_next(output):
            # While a call is being written the agent says nothing; NeMo's
            # offline path splices this stretch out, live we keep it silent.
            mute_text = True
        if self.hold_speech and not mute_text:
            # Design C: the agent may not start talking until the decider has
            # ruled on the user's turn. Record that it wanted to.
            token = int(mx.argmax(output.text_logits[:, -1]))
            if token not in (pad, self.config.silence_token_id):
                self.wants_to_speak = True
                mute_text = True
        if not mute_text and self.require_prompt and self._await_speech is None:
            token = int(mx.argmax(output.text_logits[:, -1]))
            starting = token == self.config.bos_token_id or (
                token not in (pad, self.config.silence_token_id)
                and self.quiet_frames >= self.floor_after_frames)
            if starting:
                if self.floor_open:
                    self.floor_open = False
                else:
                    mute_text = True
                    self.suppressed += 1
        if mute_text:
            output.text_logits = _force(output.text_logits, pad)
        elif self._await_speech is not None and self.pending_call is None:
            token = int(mx.argmax(output.text_logits[:, -1]))
            if token not in (pad, self.config.silence_token_id):
                self._await_speech = None  # it started on its own
            elif self._await_speech <= 0:
                # It heard the result but did not take the turn: open the
                # turn for it; the words that follow are its own.
                output.text_logits = _force(output.text_logits, self.config.bos_token_id)
                self._await_speech = None
                self.nudges += 1
            else:
                self._await_speech -= 1
        token = int(mx.argmax(output.text_logits[:, -1]))
        silent = token in (pad, self.config.silence_token_id)
        self.quiet_frames = self.quiet_frames + 1 if silent else 0
        return output

    def _in_call_next(self, output) -> bool:
        return int(mx.argmax(output.function_logits[:, -1])) == self._sotc_id

    def _step_audio_frame(self, frame: mx.array) -> list[VoiceChatEvent]:
        if self.pending_call is not None or self.paused:
            # Timeline is frozen while a tool runs or the decider rules.
            return []
        events = super()._step_audio_frame(frame)
        function_id = self._function_tokens[-1]
        if function_id == self._sotc_id:
            self._call_tokens = []
            self.call_open = False
        elif self._call_tokens is not None:
            if function_id == self._eotc_id:
                raw = self.tokenizer.decode(self._call_tokens, skip_special_tokens=False)
                events.append(self._close_call(raw))
            else:
                self._call_tokens.append(function_id)
                raw = self.tokenizer.decode(self._call_tokens, skip_special_tokens=False)
                if self.single_call and _first_call_closed(raw):
                    # The model tends to add an unrequested second call, and
                    # NVIDIA documents parallel calls as unreliable. Close the
                    # list now so the second call never enters its context.
                    closing = "]</TOOLCALL>"
                    tokens = self.tokenizer.encode(closing, add_special_tokens=False)
                    self._prefill_known(tokens + [self._eotc_id], predict_last=False)
                    events.append(self._close_call(raw + closing))
        return events

    def _close_call(self, raw: str) -> VoiceChatEvent:
        self._call_tokens = None
        self.pending_call = raw
        return VoiceChatEvent(
            kind="function_delta",
            frame_index=self._frame_index,
            token_id=self._eotc_id,
            delta=None,
            text=raw,
        )

    def push_audio(self, samples, *, sample_rate=None):
        if self.pending_call is not None:
            return []
        events = []
        # Process frame by frame so audio after a call is dropped, not queued.
        chunk = mx.array(samples, dtype=mx.float32).reshape(-1)
        self._pending_audio = mx.concatenate([self._pending_audio, chunk])
        while (
            self._pending_audio.shape[0] >= self.frame_samples
            and self.pending_call is None
            and not self.paused
        ):
            frame = self._pending_audio[: self.frame_samples]
            self._pending_audio = self._pending_audio[self.frame_samples :]
            events.extend(self._step_audio_frame(frame))
        if self.pending_call is not None:
            self._pending_audio = mx.zeros((0,), dtype=mx.float32)
        return events

    def inject_tool_response(self, responses: list) -> None:
        """Force ``<TOOL_RESPONSE>[...]</TOOL_RESPONSE>`` onto the function channel."""

        if self.pending_call is None:
            raise RuntimeError("no tool call is pending")
        self._inject(self._response_tokens(responses))

    def inject_tool_exchange(self, name: str, arguments: dict, responses: list) -> None:
        """Design B: write a whole call + response the voice model never made.

        This is how an outside decider hands the voice model a result using
        the only text input channel the model was trained to read mid-stream.
        """

        self._inject(self._call_tokens_for(name, arguments) + self._response_tokens(responses))

    def begin_tool_call(self, name: str, arguments: dict) -> str:
        """Design C: write a call as if the model made it; the timeline then
        freezes until ``inject_tool_response``, exactly like a model call."""

        tokens = self._call_tokens_for(name, arguments)
        self._prefill_known(tokens, predict_last=False)
        self.call_open = False
        return self.pending_call

    def _call_tokens_for(self, name: str, arguments: dict) -> list[int]:
        call = ascii_only(json.dumps([{"name": name, "arguments": arguments}]))
        self.pending_call = f"<TOOLCALL>{call}</TOOLCALL>"
        tokens = [self._sotc_id]
        tokens += self.tokenizer.encode(self.pending_call, add_special_tokens=False)
        tokens.append(self._eotc_id)
        return tokens

    def _response_tokens(self, responses: list) -> list[int]:
        payload = ", ".join(ascii_only(json.dumps(r, ensure_ascii=True)) for r in responses)
        text = f"<TOOL_RESPONSE>[{payload}]</TOOL_RESPONSE>"
        return self.tokenizer.encode(text, add_special_tokens=False)

    def _inject(self, token_ids: list[int]) -> None:
        start = time.perf_counter()
        # One extra step lets the model write the end-of-response marker
        # itself, as in training; force it if it does not.
        predicted = self._prefill_known(token_ids, predict_last=True)
        if predicted != self._eotr_id:
            self._function_tokens[-1] = self._eotr_id
        self.injection_ms.append((time.perf_counter() - start) * 1000.0)
        self.pending_call = None
        self._await_speech = self.speak_nudge_frames
        self.floor_open = True

    def _prefill_known(self, token_ids: list[int], *, predict_last: bool) -> int | None:
        """Insert timeline steps whose function tokens are already known.

        Each inserted step has silent user audio and no speech (agent text =
        pad). Because every input is known up front, all steps go through the
        language model as one batched forward pass instead of one per token.
        ``predict_last`` adds one more step and returns the model's function
        token for it.
        """

        pad = self.config.pad_token_id
        prev_functions = [self._function_tokens[-1], *token_ids]
        if not predict_last:
            prev_functions = prev_functions[:-1]
        n = len(prev_functions)
        prev_texts = [self._text_tokens[-1]] + [pad] * (n - 1)
        embed = self.model.stt_model.embed_tokens
        fused = (
            embed(mx.array([prev_texts], dtype=mx.int32))
            + self._silence_embedding
            + self.config.function_channel_weight
            * embed(mx.array([prev_functions], dtype=mx.int32))
        )
        if self.batched_inject:
            output = self._language_step(fused)
            logits = output.function_logits[:, -1]
        else:
            for i in range(n):
                output = self._language_step(fused[:, i : i + 1])
            logits = output.function_logits[:, -1]
        predicted = int(mx.argmax(logits)) if predict_last else None
        self._text_tokens.extend([pad] * n)
        self._function_tokens.extend(token_ids if not predict_last else [*token_ids, predicted])
        self._timeline_index += n
        return predicted


def create_tool_session(voicechat, **kwargs) -> ToolStreamingSession:
    """Like ``VoiceChatSession.create_streaming_session`` but tool-capable."""

    return ToolStreamingSession(voicechat, **kwargs)
