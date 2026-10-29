// Rose HiFi — one shared WebGL renderer for every Rose placement.
// Shader and signal mapping follow prototypes/rose-three-dimensions (1a834e8):
// procedural metallic hemispheres, rose/silver metal for microphone, upper-shell
// gap for connection, light surround for listening/speaking. Adapted here to a
// transparent, supersampled canvas so Rose can sit inside tray wells and headers.
// The single canvas is re-parented between slots; no per-thumbnail contexts.
(function () {
  'use strict';

  const VERTEX = 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}';
  const FRAGMENT = `
precision highp float;
uniform vec2 resolution;uniform float time,gap,mic,voice,energy,lens;
const vec3 eye=vec3(0.,1.8,7.4);
void hemi(vec3 ro,vec3 rd,float cy,float side,inout float best,inout vec3 normal,inout float kind){
 vec3 oc=ro-vec3(0.,cy,0.);float b=dot(oc,rd),c=dot(oc,oc)-1.;float d=b*b-c;
 if(d>0.){float s=sqrt(d);float t=-b-s;vec3 p=oc+t*rd;if(t>.001&&t<best&&side*p.y>=0.){best=t;normal=normalize(p);kind=1.;}t=-b+s;p=oc+t*rd;if(t>.001&&t<best&&side*p.y>=0.){best=t;normal=normalize(p);kind=1.;}}
 if(abs(rd.y)>.0001){float t=-oc.y/rd.y;vec3 p=oc+t*rd;if(t>.001&&t<best&&dot(p.xz,p.xz)<1.){best=t;normal=vec3(0.,-side,0.);kind=2.;}}
}
float object(vec3 ro,vec3 rd,out vec3 normal,out float kind){float t=100.;normal=vec3(0.);kind=0.;hemi(ro,rd,gap,1.,t,normal,kind);hemi(ro,rd,0.,-1.,t,normal,kind);return t;}
vec3 environment(vec3 d){
 vec3 col=mix(vec3(.10,.17,.23),vec3(.72,.82,.91),smoothstep(-.55,.65,d.y));
 float left=pow(max(0.,dot(d,normalize(vec3(-3.,3.,3.)))),20.);
 float right=pow(max(0.,dot(d,normalize(vec3(3.,2.,2.)))),38.);
 float strip=exp(-pow((d.x+.5)*7.,2.)-pow((d.y-.3)*1.6,2.))*smoothstep(-.2,.5,d.z);
 col+=vec3(2.6,2.7,2.8)*left+vec3(3.,3.1,3.3)*right+vec3(.65,.73,.82)*strip;
 col+=vec3(.7,.8,.95)*pow(max(0.,dot(d,normalize(vec3(0.,3.,-3.)))),15.);return col;
}
vec3 metal(vec3 p,vec3 n,vec3 rd,float kind){
 vec3 base=mix(vec3(.65,.73,.81),vec3(.95,.48,.53),mic);
 vec3 r=reflect(rd,n);float f=pow(1.-max(0.,dot(n,-rd)),5.);
 vec3 col=environment(r)*mix(base,vec3(.96),f*.65);
 col+=base*.09*max(0.,dot(n,normalize(vec3(-2.,4.,3.))));
 if(kind>1.5){float edge=smoothstep(.87,.99,length(p.xz));col=mix(base*.16,col*.85,edge);}
 float seam=exp(-abs(p.y)*120.)+exp(-abs(p.y-gap)*120.);col*=1.-.26*min(seam,1.);
 return col;
}
vec4 sampleAt(vec2 frag){
 vec2 uv=(frag-.5*resolution)/resolution.y;uv.y+=.055+gap*.05;
 vec3 forward=normalize(vec3(0.,.15,0.)-eye),right=vec3(1.,0.,0.),up=cross(right,forward);
 vec3 rd=normalize(forward*lens+uv.x*right+uv.y*up);
 vec3 n;float kind;float t=object(eye,rd,n,kind);
 vec3 col=vec3(0.);float alpha=0.;
 if(t<99.){col=metal(eye+rd*t,n,rd,kind);alpha=1.;}
 // Light lives in a camera-facing surround; it never changes the sphere's scale.
 float active=smoothstep(0.,.85,voice);float broad=smoothstep(1.,2.,voice);
 float envelope=energy*(.48+.22*sin(time*3.1)+.17*sin(time*7.3+1.2)+.09*sin(time*11.7));
 float radius=mix(1.075,1.55+.12*envelope,broad);
 float planeT=dot(vec3(0.,.06,0.)-eye,forward)/dot(rd,forward);vec3 hp=eye+rd*planeT-vec3(0.,.06,0.);
 vec2 q=vec2(dot(hp,right),dot(hp,up));float angle=atan(q.y,q.x);
 float ripple=broad*energy*.008*sin(angle*5.+time*1.7)*sin(time*2.3);
 float dist=abs(length(q)-radius-ripple);float aa=7.4/lens/resolution.y;
 float core=1.-smoothstep(.006,.014+aa,dist);float glow=exp(-dist*30.)*.30+exp(-dist*9.)*.12;
 float luminance=.80+.20*sin(angle*2.-.5+time*.16*broad);
 float ring=(core*.9+glow)*active*luminance;
 vec3 light=vec3(.64,.77,1.0)*ring;
 if(alpha>0.){col+=light;col=col/(col+vec3(.55));col=pow(col,vec3(1./2.2));}
 else{col=vec3(.80,.89,1.0);alpha=clamp(ring,0.,1.);}
 return vec4(col*alpha,alpha);
}
void main(){
 vec4 c=sampleAt(gl_FragCoord.xy+vec2(-.25,-.125))+sampleAt(gl_FragCoord.xy+vec2(.25,.125))
       +sampleAt(gl_FragCoord.xy+vec2(.125,-.25))+sampleAt(gl_FragCoord.xy+vec2(-.125,.25));
 gl_FragColor=c*.25;
}`;

  const GAPS = { connected: 0.012, connecting: 0.10, disconnected: 0.38 };
  const VOICES = { quiet: 0, listening: 1, speaking: 2 };

  function create(opts) {
    const canvas = opts.canvas;
    const fallback = opts.fallback;
    const report = opts.onStatus || function () {};
    let gl = null, program = null, uniforms = {}, raf = 0, last = 0, time = 0;
    let lost = false, forced = false, reduced = false, failed = '';
    let target = { mic: 1, gap: GAPS.disconnected, voice: 0 };
    let current = Object.assign({}, target);
    let energy = 0.6;
    const frames = [];
    let draws = 0;

    function status() {
      const mode = forced ? 'static-forced' : lost ? 'static-context-lost' : failed ? 'static-unavailable' : program ? 'webgl' : 'starting';
      return {
        mode, failed, reduced, draws,
        renderer: gl && !lost ? rendererName() : null,
        canvas: { width: canvas.width, height: canvas.height },
        frames: frames.slice(-240),
        signals: Object.assign({}, target),
        animating: !!raf,
      };
    }
    function rendererName() {
      try {
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      } catch (e) { return 'unknown'; }
    }
    function showFallback(on) {
      canvas.style.visibility = on ? 'hidden' : 'visible';
      fallback.hidden = !on;
      updateFallback();
      report(status());
    }
    function updateFallback() {
      fallback.dataset.mic = target.mic ? 'on' : 'muted';
      fallback.dataset.connection = Object.keys(GAPS).find((k) => GAPS[k] === target.gap) || 'connected';
      fallback.dataset.voice = Object.keys(VOICES).find((k) => VOICES[k] === target.voice) || 'quiet';
    }
    function compile(type, source) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, source); gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) { const log = gl.getShaderInfoLog(sh); gl.deleteShader(sh); throw new Error('compile: ' + log); }
      return sh;
    }
    function initialize() {
      try {
        gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: false, powerPreference: 'low-power' });
        if (!gl) throw new Error('WebGL is unavailable');
        const vs = compile(gl.VERTEX_SHADER, VERTEX), fs = compile(gl.FRAGMENT_SHADER, FRAGMENT);
        program = gl.createProgram();
        gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('link: ' + gl.getProgramInfoLog(program));
        gl.deleteShader(vs); gl.deleteShader(fs); gl.useProgram(program);
        const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(program, 'a'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
        for (const k of ['resolution', 'time', 'gap', 'mic', 'voice', 'energy', 'lens']) uniforms[k] = gl.getUniformLocation(program, k);
        gl.clearColor(0, 0, 0, 0);
        lost = false; failed = '';
        if (!forced) showFallback(false);
        request();
      } catch (e) {
        program = null; failed = String(e.message || e);
        showFallback(true);
      }
    }
    function draw() {
      if (!gl || !program || lost || forced) return;
      const r = canvas.getBoundingClientRect();
      if (!r.width || !r.height) return;
      // Cap backing store: DPR ≤ 2 and ≤ 360 px per side keeps 4× supersampling affordable.
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.min(360, Math.max(1, Math.round(r.width * dpr)));
      const h = Math.min(360, Math.max(1, Math.round(r.height * dpr)));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      gl.viewport(0, 0, w, h);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(uniforms.resolution, w, h);
      gl.uniform1f(uniforms.time, reduced ? 0 : time);
      gl.uniform1f(uniforms.gap, current.gap);
      gl.uniform1f(uniforms.mic, current.mic);
      gl.uniform1f(uniforms.voice, current.voice);
      gl.uniform1f(uniforms.energy, energy);
      gl.uniform1f(uniforms.lens, 2.05);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      draws++;
    }
    function moving() { return Object.keys(current).some((k) => Math.abs(target[k] - current[k]) > 0.0005); }
    function request() { if (!raf && !document.hidden) raf = requestAnimationFrame(tick); }
    function tick(now) {
      raf = 0;
      const dt = last ? Math.min((now - last) / 1000, 0.05) : 0;
      if (last && !reduced) { frames.push(now - last); if (frames.length > 600) frames.shift(); }
      last = now;
      if (!reduced) {
        time += dt;
        const ease = 1 - Math.exp(-dt * 5.5);
        for (const k of Object.keys(current)) current[k] += (target[k] - current[k]) * ease;
      } else current = Object.assign({}, target);
      draw();
      const shown = canvas.isConnected && canvas.offsetParent !== null;
      const live = shown && !document.hidden && !reduced && !forced && !lost && program && (current.voice > 0.001 || moving());
      if (live) request(); else last = 0;
    }

    canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); lost = true; cancelAnimationFrame(raf); raf = 0; last = 0; showFallback(true); });
    canvas.addEventListener('webglcontextrestored', () => { program = null; initialize(); });
    document.addEventListener('visibilitychange', () => { cancelAnimationFrame(raf); raf = 0; last = 0; if (!document.hidden) request(); report(status()); });
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => request()).observe(canvas);
    else window.addEventListener('resize', request);

    initialize();

    return {
      setSignals(sig) {
        target = { mic: sig.mic ? 1 : 0, gap: GAPS[sig.connection] ?? GAPS.connected, voice: VOICES[sig.voice] ?? 0 };
        if (typeof sig.energy === 'number') energy = sig.energy;
        if (reduced) current = Object.assign({}, target);
        updateFallback();
        request();
      },
      setReduced(on) { reduced = !!on; current = Object.assign({}, target); last = 0; request(); report(status()); },
      setForcedFallback(on) {
        forced = !!on;
        if (forced) { cancelAnimationFrame(raf); raf = 0; showFallback(true); }
        else if (program && !lost) { showFallback(false); request(); }
        else showFallback(true);
      },
      attach(slot) { if (canvas.parentElement !== slot) { slot.appendChild(canvas); slot.appendChild(fallback); } request(); },
      loseContext() { const ext = gl && gl.getExtension('WEBGL_lose_context'); if (ext) { ext.loseContext(); return ext; } return null; },
      restoreContext(ext) { if (ext) ext.restoreContext(); },
      status,
    };
  }

  window.RoseRenderer = { create, GAPS, VOICES };
})();
