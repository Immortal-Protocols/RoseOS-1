#!/usr/bin/env node
// QA stand-in for the Codex app-server (stdio JSON-RPC). Answers every request with a minimal
// plausible result and logs each method and params to QA_CODEX_LOG. No model or network.
import fs from 'node:fs';import readline from 'node:readline';
const LOG=process.env.QA_CODEX_LOG;let turn=0;
const results={'thread/start':()=>({thread:{id:'qa-thread'}}),'thread/resume':()=>({thread:{id:'qa-thread'}}),'turn/start':()=>({turn:{id:'qa-turn-'+(++turn)}}),'model/list':()=>({data:[]}),'account/read':()=>({account:{type:'qa-fixture'}})};
readline.createInterface({input:process.stdin}).on('line',line=>{let m;try{m=JSON.parse(line);}catch{return;}
 if(LOG)fs.appendFileSync(LOG,JSON.stringify({method:m.method,id:m.id,params:m.params})+'\n');
 if(m.id!==undefined&&m.method)process.stdout.write(JSON.stringify({id:m.id,result:(results[m.method]||(()=>({})))(m.params)})+'\n');});
