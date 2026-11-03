// Rose HiFi — phone-mode walkthrough with real touch input (CDP touch events) on emulated phones.
// Usage: node prototypes/rose-hifi/phone-check.cjs [Rose-HiFi.html] [outDir]
// Headless Chromium device emulation: checks layout and gesture logic, not a physical phone or Safari.
const path=require('path');const {chromium,devices}=require('playwright');
const FILE='file://'+path.resolve(process.argv[2]||path.join(__dirname,'Rose-HiFi.html'));const OUT=path.resolve(process.argv[3]||path.join(__dirname,'out','phone'));require('fs').mkdirSync(OUT,{recursive:true});
(async()=>{const b=await chromium.launch();const res=[];const ok=(n,c,x)=>{res.push((c?'PASS ':'FAIL ')+n+(x?' '+x:''));};
for(const [name,dev] of [['iphone13',devices['iPhone 13']],['pixel7',devices['Pixel 7']],['iphoneSE',devices['iPhone SE']]]){
const c=await b.newContext({...dev});const p=await c.newPage();const errs=[];p.on('pageerror',e=>errs.push(e.message));
await p.goto(FILE);await p.waitForTimeout(1500);
const cdp=await c.newCDPSession(p);
const swipe=async(x,y0,y1,steps=10,dt=16)=>{await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y:y0}]});for(let i=1;i<=steps;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y0+(y1-y0)*i/steps}]});await p.waitForTimeout(dt);}await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await p.waitForTimeout(600);};
const st=()=>p.evaluate(()=>({view:window.__rose.getState().view,peek:document.getElementById('screen').classList.contains('peek')}));
const g=await p.evaluate(()=>({pm:document.documentElement.classList.contains('phone-mode'),r:document.getElementById('screen').getBoundingClientRect().toJSON(),vw:innerWidth,vh:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight}));
ok(name+' phone mode on',g.pm);ok(name+' screen fits viewport',g.r.left>=-1&&g.r.right<=g.vw+1&&g.r.top>=-1&&g.r.bottom<=g.vh+1,JSON.stringify({w:Math.round(g.r.width),h:Math.round(g.r.height),vw:g.vw,vh:g.vh}));
ok(name+' no page scroll',g.sw<=g.vw&&g.sh<=g.vh+1);
await p.screenshot({path:path.join(OUT,name+'-home.png')});
g.sh=await p.evaluate(()=>document.getElementById("screen").offsetHeight);const r=g.r,cx=r.left+r.width/2;
// tap an app (messages, page 1)
await p.locator('.home-page[aria-hidden=false] .app[data-app=messages]').tap();await p.waitForTimeout(700);
ok(name+' tap opens app',(await st()).view==='app');
// short swipe up from bottom edge with touch
await swipe(cx,r.bottom-8,r.bottom-8-90*r.height/g.sh);
let s=await st();ok(name+' touch swipe up raises edge, app stays',s.peek&&s.view==='app',JSON.stringify(s));
await p.screenshot({path:path.join(OUT,name+'-peek.png')});
// type in composer while raised
await p.locator('#v-app .ab-input').tap();await p.keyboard.type('Hi');await p.locator('#v-app .ab-send').tap();await p.waitForTimeout(300);
ok(name+' composer works with edge up',(await p.locator('#v-app .c-msg p').count())===4&&(await st()).peek);
// swipe down on edge
await swipe(cx,r.bottom-60*r.height/g.sh,r.bottom-60*r.height/g.sh+40);
s=await st();ok(name+' touch swipe down lowers edge',!s.peek&&s.view==='app',JSON.stringify(s));
// long swipe goes home
await swipe(cx,r.bottom-8,r.bottom-8-360*r.height/g.sh,12);
s=await st();ok(name+' long touch swipe goes Home',s.view==='home',JSON.stringify(s));
// home tray: mic and switcher taps
await p.locator('#tray-mic').tap();await p.waitForTimeout(300);ok(name+' mic tap mutes',await p.evaluate(()=>window.__rose.getState().muted));
await p.locator('#tray-mic').tap();await p.waitForTimeout(300);
await p.locator('#tray-overview').tap();await p.waitForTimeout(800);ok(name+' switcher opens',(await st()).view==='switcher');
await swipe(cx,r.bottom-8,r.bottom-8-120);ok(name+' swipe up in switcher goes Home',(await st()).view==='home');
// home page swipe horizontally
await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:r.right-40,y:r.top+r.height*0.45}]});for(let i=1;i<=10;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:r.right-40-i*25,y:r.top+r.height*0.45}]});await p.waitForTimeout(16);}await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await p.waitForTimeout(700);
ok(name+' horizontal swipe changes Home page',(await p.evaluate(()=>window.__rose.getState().homePage))===1);
// open Rose and check
await p.locator('#tray-rose').tap();await p.waitForTimeout(1800);ok(name+' Rose opens',(await st()).view==='rose');
await p.screenshot({path:path.join(OUT,name+'-rose.png')});
// controls sheet
await p.locator('#h-toggle').tap();await p.waitForTimeout(500);
const hv=await p.evaluate(()=>{const h=document.querySelector('.harness').getBoundingClientRect();return h.top<innerHeight-50;});ok(name+' controls sheet opens',hv);
await p.screenshot({path:path.join(OUT,name+'-controls.png')});
await p.locator('#h-tasks').tap();await p.locator('#h-toggle').tap();await p.waitForTimeout(500);
ok(name+' controls sheet closes',await p.evaluate(()=>document.querySelector('.harness').getBoundingClientRect().top>=innerHeight-2));
ok(name+' no errors',errs.length===0,errs.join('|'));
await c.close();}
const c=await b.newContext({...devices['iPhone 13'],javaScriptEnabled:false});const p=await c.newPage();await p.goto(FILE);await p.waitForTimeout(500);await p.screenshot({path:path.join(OUT,'nojs.png')});
ok('no-JS notice visible',await p.locator('.noscript').isVisible());
console.log(res.join('\n'));if(res.some(r=>r.startsWith('FAIL')))process.exitCode=1;await b.close();})();
