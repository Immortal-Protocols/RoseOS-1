import fs from 'node:fs';
import path from 'node:path';
const assets=path.resolve(import.meta.dirname,'../app/assets');
for(const name of ['connection.json','clipboard-connection.json']) {
  const value=JSON.parse(fs.readFileSync(path.join(assets,name),'utf8'));
  if(Object.keys(value).some(k=>/token|secret|private|password/i.test(k)))throw Error('Private credential must not be bundled: '+name);
  if(typeof value.endpoint!=='string'||!value.endpoint.startsWith('https://'))throw Error('HTTPS endpoint required: '+name);
}
console.log('Connection assets contain only public configuration.');
