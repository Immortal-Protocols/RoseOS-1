import https from 'node:https';

// Keep the image worker and its in-flight jobs alive behind the shared listener.
// Its own token remains mandatory: never substitute the voice credential.
export function animeProxy(ca) {
 return (req,res,url)=>{
  if(!url.pathname.startsWith('/anime/'))return false;
  const route=url.pathname.slice('/anime'.length);
  if(url.search || !(/^\/health$|^\/jobs\/[a-f0-9-]{36}(?:\/image)?$|^\/uploads\/[a-f0-9-]{36}(?:\/\d+\/\d+)?$/).test(route) || !['GET','POST'].includes(req.method)){
   res.writeHead(404);res.end();return true;
  }
  const started=Date.now();let received=0;
  req.on('data',chunk=>{received+=chunk.length;});
  req.on('end',()=>console.log('ANIME_UPLOAD',req.method,received,Date.now()-started));
  req.on('aborted',()=>console.log('ANIME_ABORT',received,Date.now()-started));
  const headers={};
  for(const name of ['authorization','content-type','content-length'])if(req.headers[name])headers[name]=req.headers[name];
  const upstream=https.request({hostname:'127.0.0.1',port:9444,path:route,method:req.method,headers,ca,
   // The fixed loopback worker uses the same private, explicitly trusted cert.
   checkServerIdentity:()=>undefined},reply=>{
    const output={'cache-control':'no-store'};
    for(const name of ['content-type','content-length'])if(reply.headers[name])output[name]=reply.headers[name];
    if(req.socket.remoteAddress!=='127.0.0.1')console.log('ANIME_GATEWAY',req.method,route.endsWith('/image')?'image':route==='/health'?'health':'job',reply.statusCode);
    res.writeHead(reply.statusCode,output);reply.pipe(res);
    reply.on('error',()=>res.destroy());
   });
  upstream.setTimeout(180000,()=>upstream.destroy(new Error('Image worker timeout')));
  upstream.on('error',()=>{if(!res.headersSent){res.writeHead(502,{'content-type':'application/json'});res.end(JSON.stringify({error:'Image service connection interrupted. Tap Check again.'}));}else res.destroy();});
  req.on('aborted',()=>upstream.destroy());
  res.on('close',()=>{if(!res.writableEnded)upstream.destroy();});
  req.pipe(upstream);return true;
 };
}
