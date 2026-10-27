import WebSocket from 'ws';
const pages=await fetch('http://127.0.0.1:9223/json/list').then(r=>r.json());
const page=pages.find(p=>p.url.startsWith('https://appassets.androidplatform.net'));
if(!page)throw new Error('Rose Agent WebView not found');
const w=new WebSocket(page.webSocketDebuggerUrl);
const timer=setTimeout(()=>process.exit(1),10000);
w.on('open',()=>w.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression:process.argv[2],returnByValue:true,awaitPromise:true,userGesture:true}})));
w.on('message',d=>{const e=JSON.parse(d);if(e.id===1){console.log(JSON.stringify(e.result));clearTimeout(timer);w.close();}});
