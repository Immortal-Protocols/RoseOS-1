import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {atomicJSON} from './memory.mjs';

const source=path.dirname(fileURLToPath(import.meta.url));
const home=os.homedir();const label='local.pixel-voice.personal-memory';
const runtime=path.join(home,'.local/share/rose-agent/personal-memory');
const data=path.join(home,'.config/pixel-voice/personal-memory-trial');
const plist=path.join(home,'Library/LaunchAgents',label+'.plist');
const target='gui/'+process.getuid();
const xml=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
function loaded(){try{execFileSync('/bin/launchctl',['print',target+'/'+label],{stdio:'pipe'});return true;}catch{return false;}}

if(process.argv[2]==='stop'){
 let control={};try{control=JSON.parse(fs.readFileSync(path.join(data,'control.json'),'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 atomicJSON(path.join(data,'control.json'),{...control,collectionEnabled:false,useEnabled:false,changedAt:Date.now()});
 if(loaded())execFileSync('/bin/launchctl',['bootout',target+'/'+label]);
 if(fs.existsSync(plist))fs.renameSync(plist,plist+'.disabled');
 console.log('Personal-memory service stopped. Evidence preserved. Rose Agent unchanged.');
}else if(process.argv[2]==='install'){
 const files=['memory.mjs','context.mjs','review-state.mjs','cli.mjs','package.json'];
 const hashes=Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(path.join(source,file))).digest('hex')]));
 const version=crypto.createHash('sha256').update(JSON.stringify(hashes)).digest('hex').slice(0,16);
 const release=path.join(runtime,'releases',version);fs.mkdirSync(release,{recursive:true,mode:0o700});
 for(const file of files){const bytes=fs.readFileSync(path.join(source,file));const dest=path.join(release,file);if(fs.existsSync(dest)&&!fs.readFileSync(dest).equals(bytes))throw Error('Immutable release differs');fs.writeFileSync(dest,bytes,{mode:0o600});}
 atomicJSON(path.join(release,'manifest.json'),{version,files:hashes});
 if(fs.existsSync(plist)&&!fs.readFileSync(plist,'utf8').includes(runtime))throw Error('Existing launch service is not owned by this installer');
 if(loaded())execFileSync('/bin/launchctl',['bootout',target+'/'+label]);
 const current=path.join(runtime,'current');const next=current+'.next';
 if(fs.existsSync(next))fs.unlinkSync(next);fs.symlinkSync(release,next);fs.renameSync(next,current);
 fs.mkdirSync(data,{recursive:true,mode:0o700});fs.chmodSync(data,0o700);
 fs.mkdirSync(path.dirname(plist),{recursive:true});
 const content=`<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>Label</key><string>${label}</string><key>ProgramArguments</key><array><string>${xml(process.execPath)}</string><string>${xml(path.join(current,'cli.mjs'))}</string><string>run</string></array><key>WorkingDirectory</key><string>${xml(runtime)}</string><key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer><key>StandardOutPath</key><string>${xml(path.join(data,'service.log'))}</string><key>StandardErrorPath</key><string>${xml(path.join(data,'service-error.log'))}</string></dict></plist>`;
 fs.writeFileSync(plist,content,{mode:0o600});execFileSync('/usr/bin/plutil',['-lint',plist]);
 execFileSync('/bin/launchctl',['bootstrap',target,plist]);
 let control={};try{control=JSON.parse(fs.readFileSync(path.join(data,'control.json'),'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 console.log(JSON.stringify({installed:true,label,version,release,data,injectionEnabled:control.useEnabled===true}));
}else throw Error('Use install or stop');
