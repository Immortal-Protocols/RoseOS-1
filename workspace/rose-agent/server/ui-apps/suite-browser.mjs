import {createAppControls} from './controls.mjs';
import {visibleText,hasId,rawId,unique,requireText,tool,writeField,assertKnown,descendants,tapOnce} from './suite-common.mjs';
const PKG='app.vanadium.browser';
export const browserTools=[
 tool('browser_open','Open an HTTP(S) URL in the current Vanadium tab. Replaces the current page, submits once, verifies the full address through browser controls and the local Android link preview and returns exposed page text when available. Does not bypass interstitials or sign in.',{url:{type:'string'}}),
 tool('browser_read','Read the current Vanadium page and verify its full URL. Returns only accessibility-exposed visible text, which may be incomplete. Does not summarize hidden page content.')
];
// Candidate only: native Google search did not complete within the verification
// budget. Do not advertise until it passes real entry-point validation.
export const browserSearchTool=tool('browser_search','Search Google in the current Vanadium tab, verify the resulting search URL and return visible page text. Does not click search results.',{query:{type:'string'}});
export function normalizeBrowserUrl(value){value=requireText(value,'URL',190);let url;try{url=new URL(value);}catch{throw Error('An absolute HTTP(S) URL is required');}if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error('Only HTTP(S) URLs without embedded credentials are supported');return url.href;}
const address=s=>unique(rawId(s,PKG,'url_bar'),'Browser address field');
export function visiblePage(s){
 const byId=new Map(s.nodes.map(n=>[n.id,n]));
 const inWeb=n=>{let seen=new Set();while(n&&!seen.has(n.id)){seen.add(n.id);if(n.class==='android.webkit.WebView')return true;n=byId.get(n.parentId);}return false;};
 return [...new Set(s.nodes.filter(inWeb).map(visibleText).filter(Boolean))];
}
export function createBrowserAdapter(phone,{enableSearch=false}={}){
 const ui=createAppControls({phone,packageName:PKG,timeoutMs:45000});
 async function focusAddress(s){
  // The omnibox label and node ID can change while a submitted page loads.
  // Only the unique native editable field may be re-resolved, and only after
  // the transport explicitly confirms that the previous tap was not sent.
  for(let attempt=0;attempt<2;attempt++){
   const n=address(s),label=n.description||n.text;
   if(!n.editable||n.clickTarget!==n.id||!label)throw Error('Browser address focus target is unavailable');
   if(n.focused)return s;
   const r=await phone({action:'tap_label',snapshotId:s.snapshotId,nodeId:n.id,label});
   if(r.status==='action_accepted')return ui.wait(x=>rawId(x,PKG,'url_bar').length===1&&address(x).focused);
   if(attempt===0&&['label_target_mismatch','stale_screen'].includes(r.status)){s=await ui.screen();continue;}
   throw Error('Browser address focus rejected: '+r.status);
  }
 }
 async function edit(){let s=await ui.screen();if(!rawId(s,PKG,'url_bar').length){await ui.tap(s,unique(rawId(s,PKG,'search_box_text'),'New-tab address control'),{physical:false});s=await ui.wait(x=>rawId(x,PKG,'url_bar').length===1);}return focusAddress(s);}
 async function closeEditor(){
  for(let i=0;i<2;i++){const s=await ui.screen();if(!address(s).focused)return;const r=await phone({action:'back'});if(r.status!=='action_accepted')throw Error('Could not restore browser page');await ui.sleep(120);}
  await ui.wait(x=>!address(x).focused);
 }
 async function fullUrl(){
  let s=await edit(),actual;
  if(/^https?:\/\//.test(address(s).text))actual=normalizeBrowserUrl(address(s).text);
  else {
   // Chromium omits the scheme even in its editor on some versions. Android's
   // local share preview exposes the exact URI. Never choose a share target.
   const dropdown=unique(rawId(s,PKG,'omnibox_suggestions_dropdown'),'Current-page suggestions');
   const controls=new Set(descendants(s,dropdown).map(n=>n.id));
   const share=ui.one(s,n=>controls.has(n.id)&&/^Share link to webpage /.test(n.description||''));
   if(!share)throw Error('Current-page URL preview is unavailable');
   await tapOnce(ui,s,share);
   const end=Date.now()+5000;let preview;
   do {const next=await phone({action:'inspect_ui'});if(next.status==='ok'&&next.packageName==='com.android.intentresolver'&&!next.truncated&&!next.nodes.some(n=>n.password)){preview=next;break;}if(next.status==='ok'&&next.packageName!==PKG)throw Error('Unexpected app opened instead of the Android link preview');await ui.sleep(100);}while(Date.now()<end);
   if(!preview)throw Error('Android link preview did not appear');
   try{const uri=unique(preview.nodes.filter(n=>n.viewId==='android:id/content_preview_text'),'Link preview URL');actual=normalizeBrowserUrl(uri.text);}
   finally{const r=await phone({action:'back'});if(r.status!=='action_accepted')throw Error('Could not dismiss the link preview');const end=Date.now()+5000;let restored=false;do{const state=await phone({action:'inspect_ui'});if(state.status==='ok'&&state.packageName===PKG){restored=true;break;}if(state.status==='ok'&&state.packageName!=='com.android.intentresolver')throw Error('Unexpected app after closing link preview');await ui.sleep(100);}while(Date.now()<end);if(!restored)throw Error('Browser did not return after link preview');}
  }
  await closeEditor();return actual;
 }
 async function read(){const initial=await ui.screen();if(address(initial).focused)return {status:'needs_assistance',reason:'The address is being edited; finish or cancel that edit before reading the current page'};const url=await fullUrl(),s=await ui.screen(),text=visiblePage(s);if(!text.length)return {status:'needs_assistance',url,urlVerified:true,reason:'Vanadium exposes the address but no page text to accessibility. Read the page with the visual tools.',scope:'Current visible page'};return {status:'verified',url,text,scope:'Visible accessibility text only; not full HTML',possiblyTruncated:text.some(t=>t.length===200)};}
 async function open({url}){url=normalizeBrowserUrl(url);await edit();await writeField(ui,address,url,{submit:true});await ui.wait(x=>!address(x).focused&&address(x).text!=='',7000);const actual=await fullUrl();if(actual!==url)return {status:'needs_assistance',requestedUrl:url,actualUrl:actual,reason:'The observed final URL differs; it may be a redirect or interstitial',navigationSubmitted:true};const s=await ui.screen(),text=visiblePage(s);return {status:'verified',requestedUrl:url,url:actual,urlVerified:true,verification:'Exact current URL read from browser controls or local Android link preview',pageTextAvailable:text.length>0,text,scope:'Visible accessibility text only; not full HTML',possiblyTruncated:text.some(t=>t.length===200),navigationSubmitted:true};}
 async function search({query}){query=requireText(query,'Browser search',100);const url='https://www.google.com/search?q='+encodeURIComponent(query);if(url.length>190)throw Error('Encoded search exceeds the full URL verification limit');return {...await open({url}),query};}
 const handlers={browser_open:open,browser_read:read,browser_search:search};
 return {packageName:PKG,reuseForeground:true,voiceContext:"Vanadium can open URLs and read visible page text when exposed. Full URLs are verified through native browser controls or the local Android link preview; no share recipient is selected.",tools:[...browserTools,...(enableSearch?[browserSearchTool]:[])],execute:(name,args={})=>ui.run(()=>assertKnown(name,handlers)(args))};
}
