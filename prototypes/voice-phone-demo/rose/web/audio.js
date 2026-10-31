// Browser audio for Rose: mic capture with the browser's echo cancellation,
// and a low-latency player that can duck or stop instantly and reports how
// much of each spoken segment was actually played.
//
// Wire protocol on one WebSocket (/ws/audio):
//   client -> server  binary: Int16 PCM, 16 kHz mono, 512 samples (32 ms)
//   client -> server  text:   {"type":"played","seg":N,"samples":S,"done":bool}
//                             (samples count within one binary message; done = that message finished)
//                             {"type":"stopped","seg":N,"samples":S}
//   server -> client  binary: uint32 LE segment id, then Int16 PCM 24 kHz mono
//   server -> client  text:   {"type":"duck","gain":0.25} | {"type":"unduck"}
//                             {"type":"pause"} | {"type":"resume"} (hold position)
//                             {"type":"stop"} | {"type":"end","seg":N}

const CAPTURE_RATE = 16000;
const PLAY_RATE = 24000;
const FRAME = 512;

const captureWorklet = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(${FRAME}); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === ${FRAME}) {
        const pcm = new Int16Array(${FRAME});
        let sum = 0;
        for (let j = 0; j < ${FRAME}; j++) {
          const s = Math.max(-1, Math.min(1, this.buf[j]));
          sum += s * s;
          pcm[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        this.port.postMessage({pcm, rms: Math.sqrt(sum / ${FRAME})}, [pcm.buffer]);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor('rose-capture', Capture);`;

const playerWorklet = `
class Player extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];          // {seg, data: Float32Array, pos}
    this.gain = 1; this.target = 1;
    this.stopping = false;
    this.holding = false;     // paused: fade to silence and keep the position
    this.reportEvery = 2400;  // samples (100 ms)
    this.sinceReport = 0;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === 'audio') this.queue.push({seg: m.seg, data: m.data, pos: 0});
      else if (m.type === 'duck') { if (!this.stopping) this.target = m.gain; }
      else if (m.type === 'unduck') { if (!this.stopping && !this.holding) this.target = 1; }
      else if (m.type === 'pause') { if (!this.stopping) { this.holding = true; this.target = 0; } }
      else if (m.type === 'resume') { this.holding = false; if (!this.stopping) this.target = 1; }
      else if (m.type === 'stop') { this.stopping = true; this.target = 0; }
    };
  }
  report(item, done) {
    this.port.postMessage({type: 'played', seg: item.seg, samples: item.pos, done});
  }
  process(_, outputs) {
    const out = outputs[0][0];
    const step = 1 / (0.04 * sampleRate);  // 40 ms ramps
    for (let i = 0; i < out.length; i++) {
      if (this.gain < this.target) this.gain = Math.min(this.target, this.gain + step);
      else if (this.gain > this.target) this.gain = Math.max(this.target, this.gain - step);
      if (this.stopping && this.gain === 0) {
        const head = this.queue[0];
        if (head) this.port.postMessage({type: 'stopped', seg: head.seg, samples: head.pos});
        else this.port.postMessage({type: 'stopped', seg: -1, samples: 0});
        this.queue = []; this.stopping = false; this.holding = false; this.target = 1; this.gain = 1;
        for (; i < out.length; i++) out[i] = 0;
        break;
      }
      const head = this.queue[0];
      if (!head || (this.holding && this.gain === 0)) { out[i] = 0; continue; }
      out[i] = head.data[head.pos++] * this.gain;
      if (++this.sinceReport >= this.reportEvery) { this.sinceReport = 0; this.report(head, false); }
      if (head.pos >= head.data.length) {
        this.report(head, true);
        this.queue.shift();
        if (!this.queue.length) this.port.postMessage({type: 'idle'});
      }
    }
    return true;
  }
}
registerProcessor('rose-player', Player);`;

function moduleURL(src) {
  return URL.createObjectURL(new Blob([src], {type: 'application/javascript'}));
}

export class RoseAudio {
  constructor({url, onLevel, onPlayback, onStatus} = {}) {
    this.url = url || `ws://${location.host}/ws/audio`;
    this.onLevel = onLevel || (() => {});
    this.onPlayback = onPlayback || (() => {});
    this.onStatus = onStatus || (() => {});
    this.ws = null; this.capture = null; this.player = null; this.stream = null;
    this.playing = false;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true,
              channelCount: 1},
    });
    this.capture = new AudioContext({sampleRate: CAPTURE_RATE, latencyHint: 'interactive'});
    this.playback = new AudioContext({sampleRate: PLAY_RATE, latencyHint: 'interactive'});
    await this.capture.audioWorklet.addModule(moduleURL(captureWorklet));
    await this.playback.audioWorklet.addModule(moduleURL(playerWorklet));

    const src = this.capture.createMediaStreamSource(this.stream);
    const cap = new AudioWorkletNode(this.capture, 'rose-capture');
    src.connect(cap);
    // Keep the capture graph pulled without making the mic audible.
    const sink = this.capture.createGain(); sink.gain.value = 0;
    cap.connect(sink).connect(this.capture.destination);

    this.player = new AudioWorkletNode(this.playback, 'rose-player');
    this.player.connect(this.playback.destination);
    this.player.port.onmessage = (e) => this._fromPlayer(e.data);

    this._connect();
    cap.port.onmessage = (e) => {
      this.onLevel(e.data.rms);
      if (this.ws && this.ws.readyState === 1) this.ws.send(e.data.pcm.buffer);
    };
    this.onStatus('live');
  }

  _connect() {
    this.ws = new WebSocket(this.url);
    this.ws.binaryType = 'arraybuffer';
    this.ws.onopen = () => {
      this.ws.send(JSON.stringify({type: 'hello', captureRate: CAPTURE_RATE, playRate: PLAY_RATE}));
      this.onStatus('live');
    };
    this.ws.onclose = () => { this.onStatus('reconnecting'); setTimeout(() => this._connect(), 800); };
    this.ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        const m = JSON.parse(e.data);
        if (['duck', 'unduck', 'pause', 'resume', 'stop'].includes(m.type)) this.player.port.postMessage(m);
        return;
      }
      const seg = new DataView(e.data).getUint32(0, true);
      const pcm = new Int16Array(e.data, 4);
      const data = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 0x8000;
      this.player.port.postMessage({type: 'audio', seg, data}, [data.buffer]);
      if (!this.playing) { this.playing = true; this.onPlayback(true); }
    };
  }

  _fromPlayer(m) {
    if (m.type === 'idle') { this.playing = false; this.onPlayback(false); }
    if (m.type === 'stopped') { this.playing = false; this.onPlayback(false); }
    if ((m.type === 'played' || m.type === 'stopped') && this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(m));
    }
  }

  async stop() {
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.ws) { this.ws.onclose = null; this.ws.close(); }
    if (this.capture) await this.capture.close();
    if (this.playback) await this.playback.close();
    this.onStatus('off');
  }
}
