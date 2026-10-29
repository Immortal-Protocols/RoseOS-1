const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(__dirname+'/Rose-Three-Dimensions.html','utf8');
const js=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const vals={},events={},elements={},jobs=new Map();let jobID=0,clock=0,draws=0;
function element(id){return {id,value:'',checked:false,textContent:'',style:{},attrs:{},children:[],classList:{toggle(){}},setAttribute(k,v){this.attrs[k]=v},appendChild(x){this.children.push(x)},addEventListener(k,f){events[id+':'+k]=f},getBoundingClientRect(){return {width:800,height:600}},querySelector(s){return elements[id+s]??(elements[id+s]=element(id+s))}}}
for(const id of [...html.matchAll(/id="([^"]+)"/g)].map(x=>x[1]))elements[id]=element(id);
elements.mic.value='1';elements.connection.value='0';elements.voice.value='1';elements.amplitude.value='.6';
for(const [id,labels] of Object.entries({mic:['Microphone muted','Microphone on'],connection:['Connected','Connecting','Disconnected'],voice:['Quiet','Listening','Speaking']}))Object.defineProperty(elements[id],'selectedOptions',{get:()=>[{textContent:labels[Number(elements[id].value)]}]});
const nav=element('nav');elements.scene.parentElement={nextElementSibling:nav};
const gl=new Proxy({getShaderParameter:()=>true,getProgramParameter:()=>true,getAttribLocation:()=>0,getUniformLocation:(_,k)=>k,uniform1f:(k,v)=>vals[k]=v,uniform2f:(k,a,b)=>vals[k]=[a,b],getParameter:()=> 'MOCK',drawArrays:()=>draws++},{get:(o,k)=>k in o?o[k]:(()=>({}))});elements.scene.getContext=()=>gl;
const document={hidden:false,getElementById:id=>elements[id],querySelectorAll:()=>nav.children,createElement:()=>element('button'),addEventListener:(k,f)=>events['document:'+k]=f};
const context={document,matchMedia:()=>({matches:false,addEventListener(){}}),ResizeObserver:class{observe(){}},devicePixelRatio:2,requestAnimationFrame:f=>{jobs.set(++jobID,f);return jobID},cancelAnimationFrame:id=>jobs.delete(id),console};vm.runInNewContext(js,context);
function step(n=1){for(let i=0;i<n;i++){clock+=16.67;const callbacks=[...jobs.values()];jobs.clear();callbacks.forEach(f=>f(clock));}}
step(5);assert.equal(nav.children.length,6);assert.deepEqual(vals.resolution,[1200,900]);
elements.voice.value='2';elements.voice.onchange();step(150);elements.mic.value='0';elements.mic.onchange();step(150);assert(vals.voice>1.99);assert(vals.mic<.001);assert(elements.stateLabel.textContent.includes('Speaking · Muted'));
elements.connection.value='2';elements.connection.onchange();step(150);assert(Math.abs(vals.gap-.38)<.0001);assert(vals.voice>1.99);
elements.pause.onclick();step();const time=vals.time;step(120);assert.equal(vals.time,time);assert.equal(jobs.size,0);
elements.reduced.checked=true;elements.reduced.onchange();nav.children[3].onclick();step();assert.equal(vals.mic,1);assert.equal(vals.gap,.012);assert.equal(vals.voice,1);assert.equal(vals.time,0);assert.equal(jobs.size,0);
elements.reduced.checked=false;elements.reduced.onchange();elements.pause.onclick();step();document.hidden=true;events['document:visibilitychange']();assert.equal(jobs.size,0);document.hidden=false;events['document:visibilitychange']();step();
events['scene:webglcontextlost']({preventDefault(){}});assert.equal(elements.fallback.style.display,'flex');assert.equal(elements.scene.style.visibility,'hidden');
events['scene:webglcontextrestored']();step();assert.equal(elements.fallback.style.display,'none');
elements.reset.onclick();step(150);assert.equal(elements.mic.value,1);assert.equal(elements.connection.value,0);assert.equal(elements.voice.value,1);
assert(!/<script[^>]+src=|<link[^>]+href=|fetch\(|XMLHttpRequest|WebSocket/.test(html));
console.log('PASS: six presets; independent mute/output/connection; capped resolution; pause; static reduced motion; hidden-page suspension; context-loss/restoration handlers; reset; self-contained source.');
console.log('DOM/WebGL calls were mocked. No browser pixels, GLSL compilation, visual quality, or GPU performance were verified.');
