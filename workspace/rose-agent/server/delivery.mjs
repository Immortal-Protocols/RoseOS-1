import fs from 'node:fs';
export function delivery(req,res,url,dir,deviceId=null){
 if(req.method!=='GET')return false;
 const send=(code,obj)=>{res.writeHead(code,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(obj));};
 const local=['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket?.remoteAddress);
 const allowed=entry=>!entry.deviceId||entry.deviceId===deviceId||local;
 if(url.pathname==='/apps'){
  const apps=fs.existsSync(dir)?fs.readdirSync(dir).filter(f=>/^[a-f0-9]{64}\.json$/.test(f)).map(f=>JSON.parse(fs.readFileSync(dir+'/'+f,'utf8'))).filter(allowed).sort((a,b)=>b.created-a.created):[];
  const latest=[...new Map(apps.map(a=>a.packageName).map(name=>[name,apps.find(a=>a.packageName===name)] )).values()];send(200,{apps:latest});return true;
 }
 const match=url.pathname.match(/^\/apps\/([a-f0-9]{64})\/(apk|manifest|idsig)$/);if(!match)return false;
 const metadata=dir+'/'+match[1]+'.json';
 if(!fs.existsSync(metadata)){send(404,{error:'App not found'});return true;}
 const entry=JSON.parse(fs.readFileSync(metadata,'utf8'));
 if(!allowed(entry)){send(404,{error:'App not found'});return true;}
 if(match[2]==='manifest'){send(200,entry);return true;}
 const sidecar=match[2]==='idsig';
 const file=dir+'/'+match[1]+(sidecar?'.apk.idsig':'.apk');if((sidecar&&!entry.v4Signature)||!fs.existsSync(file)){send(404,{error:'App not found'});return true;}
 res.writeHead(200,{'content-type':sidecar?'application/octet-stream':'application/vnd.android.package-archive','content-length':fs.statSync(file).size,'cache-control':'no-store'});
 const stream=fs.createReadStream(file);stream.on('error',()=>res.destroy());res.on('close',()=>stream.destroy());stream.pipe(res);return true;
}
