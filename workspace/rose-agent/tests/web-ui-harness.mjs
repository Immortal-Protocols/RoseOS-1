// Loads app/assets/index.html into a vm context the way the WebView does: each
// <script src> asset in page order, then the inline page script. Only the
// build-generated ElevenLabs bundle is replaced; tests supply the voice provider.
import vm from 'node:vm';
import fs from 'node:fs';

const assets=new URL('../app/assets/',import.meta.url);
const GENERATED=new Set(['eleven-client.js']);

export function pageScripts(){
 const html=fs.readFileSync(new URL('index.html',assets),'utf8');
 const scripts=[];
 for(const m of html.matchAll(/<script(?: src="([^"]+)")?>([\s\S]*?)<\/script>/g)){
  if(m[1]){if(!GENERATED.has(m[1]))scripts.push({name:m[1],source:fs.readFileSync(new URL(m[1],assets),'utf8')});}
  else scripts.push({name:'index.html inline',source:m[2]});
 }
 return scripts;
}

function element(id){
 const classes=new Set();
 return {id,textContent:'',value:'',hidden:false,disabled:false,children:[],style:{},dataset:{},
  classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),toggle:(c,on)=>on??!classes.has(c)?classes.add(c):classes.delete(c),contains:c=>classes.has(c)},
  append(...n){this.children.push(...n);},replaceChildren(...n){this.children=n;},addEventListener(){},setAttribute(){},
  get firstChild(){return this.children[0];},scrollTop:0,scrollHeight:0};
}

// Timers are recorded, never run, so no poll loop starts; tests drive state directly.
export function loadPage({native={}}={}){
 const nodes=new Map(),timers=[],calls=[];
 const record=name=>(...args)=>{calls.push([name,...args]);};
 const Native=new Proxy({audioPrepared:()=>true,endpoint:()=>'https://example.invalid',audioRoute:()=>'',
  voiceBoostEnabled:()=>false,cameraMicrophoneSuspended:()=>false,appContext:()=>'{}',phoneCommand:()=>'{}',
  shouldOffer:()=>false,installStatus:()=>'',lastButton:()=>'',...native},
  {get:(t,k)=>k in t?t[k]:record(k)});
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,element(id));return nodes.get(id);},
  createElement:tag=>element(tag),querySelector:()=>null,querySelectorAll:()=>[],body:element('body')};
 const context=vm.createContext({document,Native,console:{log(){},warn(){},error:console.error},
  localStorage:{getItem:()=>null,setItem(){}},navigator:{},performance:{now:()=>0},crypto:globalThis.crypto,
  setTimeout:(f,ms)=>{timers.push({f,ms});return timers.length;},setInterval:(f,ms)=>{timers.push({f,ms,repeat:true});return timers.length;},
  clearTimeout(){},clearInterval(){},Audio:class{},TextEncoder,TextDecoder,URL,AbortController,
  Map,Set,Promise,JSON,Error,Math,Date,Number,String,Array,Object,Symbol,structuredClone});
 // As in a browser, window is the global object itself.
 context.window=context.self=context;const window=context;
 for(const script of pageScripts()){
  try{vm.runInContext(script.source,context,{filename:script.name});}
  catch(error){throw new Error(`${script.name} failed to load in the test page: ${error.message}`);}
 }
 const run=code=>vm.runInContext(code,context);
 return {context,run,nodes,node:id=>document.getElementById(id),calls,timers,window};
}

// A provider conversation with observable mute and input state, standing in for
// the object VoiceProviders.start resolves to.
export const fakeConversation=`({muted:false,closed:false,setMuted(v){this.muted=!!v;},
 inputLevel:()=>0.2,outputLevel:()=>0,close(){this.closed=true;return Promise.resolve();},
 sendContext(){},sendMessage(){},announce(){}})`;
