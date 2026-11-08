import fs from 'node:fs';
import path from 'node:path';
const assets = process.argv[2];
if (!assets) throw Error('Usage: node scripts/check-public-assets.mjs ASSETS_DIRECTORY');
for (const name of ['connection.json', 'clipboard-connection.json']) {
  const value = JSON.parse(fs.readFileSync(path.join(assets, name), 'utf8'));
  const allowed = name === 'clipboard-connection.json' ? ['endpoint'] : ['endpoint', 'pin'];
  if (Object.keys(value).some(key => !allowed.includes(key))) throw Error('Unexpected connection field: ' + name);
  const endpoint = new URL(value.endpoint);
  if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw Error('Expected HTTPS endpoint without credentials/query/fragment: ' + name);
  }
  if (allowed.includes('pin') && !/^[a-f0-9]{64}$/i.test(value.pin)) throw Error('Certificate pin required: ' + name);
}
console.log('Public connection asset checks passed.');
