import {createHash,randomUUID} from 'node:crypto';
import {createAppControls} from './controls.mjs';
import {visibleText,hasId,rawId,unique,requireText,tool,descendants,writeField,assertKnown,tapOnce} from './suite-common.mjs';
const PKG='com.android.documentsui';
export const filesTools=[
 tool('files_list','Read visible files and folders in the current Files location. Returns observed names, metadata and short-lived result IDs; does not enumerate hidden or off-screen documents.'),
 tool('files_search','Search from the current Files location and verify the submitted query and result list. Searches only what this DocumentsUI provider exposes; no global filesystem access.',{query:{type:'string'}}),
 tool('files_open','Open an exact file or folder returned by the latest Files list/search. Requires a fresh result ID. Verifies the folder breadcrumb or viewer title; never claims a successful open from a tap alone.',{resultId:{type:'string'}})
];
export function readFiles(s){
 const roots=s.nodes.filter(n=>hasId(n,PKG,'item_root'));
 const rows=roots.map(root=>{const children=descendants(s,root),titles=children.filter(n=>n.viewId==='android:id/title'&&n.text);if(titles.length!==1)return null;const name=titles[0].text;return {name,metadata:[...new Set(children.filter(n=>['metadata','details','date'].some(id=>hasId(n,PKG,id))).map(visibleText).filter(Boolean))],target:root.clickTarget??titles[0].clickTarget,label:visibleText(root)||visibleText(titles[0]),rootId:root.id};}).filter(Boolean);
 const breadcrumbs=rawId(s,PKG,'breadcrumb_text').map(visibleText);
 const search=rawId(s,PKG,'search_src_text');
 const empty=s.nodes.some(n=>['No items','No results','No files'].includes(visibleText(n)));
 const loading=s.nodes.some(n=>n.class==='android.widget.ProgressBar');
 const listing=s.nodes.some(n=>['dir_list','dir_container'].some(id=>hasId(n,PKG,id)));
 return {rows,breadcrumbs,query:search[0]?.text??null,empty,listing,loading};
}
export function createFilesAdapter(phone){
 const ui=createAppControls({phone,packageName:PKG});let saved=new Map();
 function list(s){const state=readFiles(s);if(state.loading)throw Error('Files is still loading');if(!state.listing&&!state.empty)throw Error('Files listing is not exposed on this screen');saved.clear();const generation=randomUUID();const identity=JSON.stringify([state.breadcrumbs,state.query]);const results=state.rows.map((r,i)=>{const resultId=createHash('sha256').update(identity+'\n'+r.name+'\n'+i+'\n'+generation).digest('hex').slice(0,20);saved.set(resultId,{...r,identity,created:Date.now()});return {resultId,name:r.name,metadata:r.metadata};});return {status:'verified',location:state.breadcrumbs,query:state.query,results,scope:'Visible items only',empty:state.empty};}
 async function search({query}){query=requireText(query,'File search');let s=await ui.screen();const before=readFiles(s),signature=JSON.stringify(before.rows.map(r=>[r.name,r.metadata]));if(before.query===query&&!before.loading&&(before.rows.length||before.empty))return list(s);if(!rawId(s,PKG,'search_src_text').length){const n=ui.label(s,'Search');await ui.tap(s,n,{physical:false});await ui.wait(x=>rawId(x,PKG,'search_src_text').length===1);}
 await writeField(ui,s=>unique(rawId(s,PKG,'search_src_text'),'Files search field'),query,{submit:true});
 s=await ui.wait(x=>{const state=readFiles(x);return state.query===query&&!state.loading&&(state.empty||state.rows.length>0&&JSON.stringify(state.rows.map(r=>[r.name,r.metadata]))!==signature);});return list(s);}
 // This chooser and Compose viewer shape were observed on the physical Pixel.
 // Recognize only the PDF path; never guess an app for other formats/choosers.
 const visibleNodes=s=>s.nodes.filter(n=>n.visible!==false);
 const labelled=(s,label)=>visibleNodes(s).filter(n=>visibleText(n)===label);
 function pdfChooser(s,name){
  if(s.packageName!=='android'||! /\.pdf$/i.test(name))return null;
  // Android remembers the last single-use choice in an explicit resolver title.
  // Only this exact title identifies the already selected app safely.
  const titles=visibleNodes(s).filter(n=>n.viewId==='android:id/title');
  if(titles.length===1&&titles[0].text==='Open with PDF Viewer'&&labelled(s,'Use a different app').length===1){
   const once=labelled(s,'Just once'),always=labelled(s,'Always');
   if(once.length===1&&once[0].viewId==='android:id/button_once'&&always.length===1&&always[0].viewId==='android:id/button_always')return {once:once[0],remembered:true};
   return null;
  }
  if(labelled(s,'Open with').length!==1||labelled(s,'PDF Viewer').length!==1||labelled(s,'Just once').length!==1||labelled(s,'Always').length!==1)return null;
  const viewer=labelled(s,'PDF Viewer')[0],once=labelled(s,'Just once')[0];
  if(viewer.enabled===false||viewer.clickTarget===undefined)return null;
  return {viewer,once};
 }
 function documentVerified(s,name){
  const nodes=visibleNodes(s);
  // Preserve standard title verification; GrapheneOS PDF Viewer is Compose and
  // has no view IDs, so its exact filename also needs the observed viewer chrome.
  if(s.packageName===PKG||s.packageName==='android'||s.packageName==='com.android.intentresolver')return false;
  if(nodes.some(n=>n.text===name&&n.viewId&&/(:id\/title|:id\/toolbar_title|:id\/action_bar_title)$/.test(n.viewId)))return true;
  return /\.pdf$/i.test(name)&&s.packageName==='app.grapheneos.pdfviewer'&&
   nodes.filter(n=>n.text===name&&n.class==='android.widget.TextView').length===1&&
   ['Previous page','Next page','Open document','overflow_menu'].every(label=>labelled(s,label).length===1);
 }
 async function open({resultId}){
  const r=saved.get(resultId);
  if(!r||Date.now()-r.created>300000)throw Error('File result expired; list or search again');
  const s=await ui.screen(),state=readFiles(s);
  if(JSON.stringify([state.breadcrumbs,state.query])!==r.identity)throw Error('Files location changed; list or search again');
  const matching=state.rows.filter(x=>x.name===r.name&&JSON.stringify(x.metadata)===JSON.stringify(r.metadata));
  if(matching.length!==1)throw Error('File is missing or ambiguous; list again');
  const row=matching[0],n=s.nodes.find(n=>n.id===row.rootId);
  const targets=descendants(s,n).filter(n=>visibleText(n)===row.name&&n.clickTarget===row.target);
  const target=unique(targets,'File item');
  // Consume a selected result even when the tap becomes stale. A second attempt
  // requires a fresh list, rather than risking a duplicate document launch.
  saved.delete(resultId);
  await tapOnce(ui,s,target);
  ui.markMutation();
  let selectedViewer=false,confirmedOnce=false;
  const end=Date.now()+6500;
  do{
   const next=await phone({action:'inspect_ui'});
   if(next.status!=='ok'||next.truncated||next.nodes.some(n=>n.password))throw Error('Opened document cannot be inspected safely');
   if(next.packageName===PKG){
    const state=readFiles(next);
    if(state.breadcrumbs.at(-1)===r.name&&state.query===null&&JSON.stringify(state.breadcrumbs)!==JSON.stringify(JSON.parse(r.identity)[0]))return {...list(next),opened:r.name,kind:'folder'};
   }else if(documentVerified(next,r.name))return {status:'verified',opened:r.name,kind:'document',viewerPackage:next.packageName,verification:'Viewer title exactly matches selected file'};
   else if(next.packageName==='android'||next.packageName==='com.android.intentresolver'){
    const chooser=pdfChooser(next,r.name);
    if(!chooser)return {status:'uncertain',opened:r.name,reason:'An unrecognized document chooser is open; choose an app on the phone. The file is not verified open.'};
    if(chooser.remembered)selectedViewer=true;
    if(!selectedViewer){
     // A pre-enabled confirmation could belong to another selected app. Do not
     // infer its identity or change a previously selected/default application.
     if(chooser.once.enabled!==false||chooser.once.clickTarget!==undefined)return {status:'uncertain',opened:r.name,reason:'Document chooser already has an unverified selection; the file is not verified open.'};
     selectedViewer=true;
     await tapOnce(ui,next,chooser.viewer);
     continue;
    }
    if(!confirmedOnce&&chooser.once.enabled!==false&&chooser.once.clickTarget!==undefined){
     confirmedOnce=true;
     await tapOnce(ui,next,chooser.once);
     continue;
    }
   }
   await ui.sleep(100);
  }while(Date.now()<end);
  return {status:'uncertain',opened:r.name,reason:'Open was requested but neither the selected folder breadcrumb nor a matching document viewer title was observed. Do not assume the file opened.'};
 }
 const handlers={files_list:async()=>list(await ui.screen()),files_search:search,files_open:open};
 return {packageName:PKG,reuseForeground:true,voiceContext:"Files can list visible items, search the current provider, and open a fresh exact result with verification. No filesystem-wide access or file edits.",tools:filesTools,execute:(name,args={})=>ui.run(()=>assertKnown(name,handlers)(args))};
}
