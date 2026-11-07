// Offline: the CLI and conversation's registered app tools share one catalog
// and emit the identical dev.pixel.voice app_command. No network, no phone.
import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync, spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {loadAppToolCatalog} from '../server/app-tool-catalog.mjs';
import {loadCatalog, validateArgs, appCommand, parseArgv, describeCatalog, UsageError} from './controls.mjs';

const catalog = loadCatalog();
const cli = fileURLToPath(new URL('./rose-customize.mjs', import.meta.url));

async function serverCatalog() {
 const commands = [];
 const loaded = await loadAppToolCatalog({
  phone: async cmd => { commands.push(cmd); return {status: 'verified'}; },
  packages: ['dev.pixel.voice'], catalogs: {'dev.pixel.voice': {status: 'ok', tools: catalog}},
 });
 return {...loaded, commands};
}

test('catalog loads with the four controls plus state', () => {
 assert.deepEqual(catalog.map(t => t.name), ['customization_state', 'customization_home_columns',
  'customization_wallpaper', 'customization_font', 'customization_glasses_hook']);
 for (const t of catalog) assert.equal(t.operation, t.name);
});

test('the real server loader accepts every catalog tool', async () => {
 const {definitions} = await serverCatalog();
 assert.deepEqual(definitions.map(d => d.name), catalog.map(t => t.name));
 for (const d of definitions) assert.deepEqual(d.inputSchema, catalog.find(t => t.name === d.name).inputSchema);
});

test('conversation and CLI emit the identical app_command', async () => {
 const {handleToolCall, commands} = await serverCatalog();
 const out = await handleToolCall({threadId: 't', callId: 'c1', tool: 'customization_home_columns', arguments: {columns: 5}});
 assert.equal(out.success, true);
 assert.equal(commands.length, 1, 'background-safe catalog entry must not open the app first');
 assert.deepEqual(commands[0], appCommand('customization_home_columns', {columns: 5}));
 const parsed = parseArgv(['columns', '5']);
 assert.deepEqual(appCommand(parsed.tool, parsed.args), commands[0]);
});

const cases = [
 ['customization_state', {}],
 ['customization_state', {verbose: true}],
 ['customization_home_columns', {columns: 0}],
 ['customization_home_columns', {columns: 8}],
 ['customization_home_columns', {columns: 9}],
 ['customization_home_columns', {columns: 2}],
 ['customization_home_columns', {columns: '5'}],
 ['customization_home_columns', {columns: 4.5}],
 ['customization_home_columns', {}],
 ['customization_wallpaper', {variant: 'dusk'}],
 ['customization_wallpaper', {variant: 'teal'}],
 ['customization_wallpaper', {variant: 3}],
 ['customization_font', {font: 'mono'}],
 ['customization_font', {font: 'comic'}],
 ['customization_font', {font: 'serif', size: 12}],
 ['customization_glasses_hook', {enabled: true}],
 ['customization_glasses_hook', {enabled: 'yes'}],
 ['customization_glasses_hook', {action: 'app', packageName: 'com.example.notes'}],
 ['customization_glasses_hook', {action: 'launcher'}],
 ['customization_glasses_hook', {packageName: 7}],
 ['customization_home_columns', [5]],
 ['customization_home_columns', null],
];

test('server and CLI validators agree, with the same messages', async () => {
 const {handleToolCall} = await serverCatalog();
 let accepted = 0, rejected = 0;
 for (const [i, [tool, args]] of cases.entries()) {
  const spec = catalog.find(t => t.name === tool);
  const mine = validateArgs(spec, args);
  let server = null;
  try { await handleToolCall({threadId: 't', callId: 'v' + i, tool, arguments: args}); }
  catch (error) { server = error.message; }
  assert.equal(mine, server, tool + ' ' + JSON.stringify(args));
  mine ? rejected++ : accepted++;
 }
 assert.ok(accepted >= 6 && rejected >= 10);
});

test('CLI argv parsing', () => {
 const spec = name => catalog.find(t => t.name === name);
 const check = argv => { const p = parseArgv(argv); return {p, err: validateArgs(spec(p.tool), p.args)}; };
 assert.deepEqual(parseArgv(['state']), {kind: 'tool', tool: 'customization_state', args: {}});
 assert.deepEqual(check(['columns', 'auto']), {p: {kind: 'tool', tool: 'customization_home_columns', args: {columns: 0}}, err: null});
 assert.equal(check(['columns', '6']).err, null);
 assert.equal(check(['columns', '9']).err, 'Unsupported argument: columns');
 assert.equal(check(['columns', 'many']).err, 'Invalid argument: columns');
 assert.equal(check(['wallpaper', 'garnet']).err, null);
 assert.equal(check(['wallpaper', 'teal']).err, 'Unsupported argument: variant');
 assert.equal(check(['font', 'condensed']).err, null);
 assert.deepEqual(parseArgv(['glasses-hook', '--app', 'com.example.notes']).args, {action: 'app', packageName: 'com.example.notes'});
 assert.deepEqual(parseArgv(['glasses-hook', '--on', '--tray']).args, {enabled: true, action: 'tray'});
 assert.deepEqual(parseArgv(['glasses-hook', '--off']).args, {enabled: false});
 for (const bad of [['glasses-hook'], ['glasses-hook', '--on', '--off'], ['glasses-hook', '--app'],
  ['glasses-hook', '--tray', '--app', 'x.y'], ['glasses-hook', '--worn'], ['glasses-hook', '--app', 'foo'], ['glasses-hook', '--app', '1x.y'], ['columns'], ['wallpaper', 'a', 'b'], ['theme', 'dark'], ['state', 'x']])
  assert.throws(() => parseArgv(bad), UsageError, JSON.stringify(bad));
 assert.deepEqual(parseArgv([]), {kind: 'help'});
 assert.deepEqual(parseArgv(['list']), {kind: 'list'});
});

test('list is derived from the catalog enums', () => {
 const text = describeCatalog(catalog);
 for (const spec of catalog) for (const f of Object.values(spec.inputSchema.properties))
  for (const v of f.enum || []) assert.ok(text.includes(String(v)), String(v));
 const altered = structuredClone(catalog);
 altered[2].inputSchema.properties.variant.enum.push('test_only_variant');
 assert.ok(describeCatalog(altered).includes('test_only_variant'));
});

test('CLI --dry-run prints the command or exits 2', () => {
 const out = execFileSync(process.execPath, [cli, '--dry-run', 'wallpaper', 'ember'], {encoding: 'utf8'});
 assert.deepEqual(JSON.parse(out), appCommand('customization_wallpaper', {variant: 'ember'}));
 const bad = spawnSync(process.execPath, [cli, 'columns', '9'], {encoding: 'utf8'});
 assert.equal(bad.status, 2);
 assert.equal(bad.stdout, '');
 assert.match(bad.stderr, /Unsupported argument: columns/);
});

test('--say hands the validated change to Rose\'s conversation via loopback /api/say', async () => {
 const {main, sayText, SAY_LIMIT} = await import('./rose-customize.mjs');
 const http = await import('node:http');
 const seen = [];
 const server = http.createServer((req, res) => {
  const parts = []; req.on('data', p => parts.push(p));
  req.on('end', () => {
   seen.push({method: req.method, url: req.url, host: req.headers.host, origin: req.headers.origin, auth: req.headers.authorization, body: JSON.parse(Buffer.concat(parts).toString())});
   const ready = seen.length === 1;
   res.writeHead(ready ? 200 : 503, {'Content-Type': 'application/json'});
   res.end(JSON.stringify(ready ? {ok: true, mode: 'typed'} : {ok: false, error: 'Rose is still loading'}));
  });
 });
 await new Promise(r => server.listen(0, '127.0.0.1', r));
 const port = server.address().port, logs = [], errors = [];
 const out = {log: m => logs.push(m), error: m => errors.push(m)};
 try {
  assert.equal(await main(['--say', '--port', String(port), 'columns', '5'], out), 0);
  assert.deepEqual(seen[0], {method: 'POST', url: '/api/say', host: '127.0.0.1:' + port, origin: undefined, auth: undefined,
   body: {text: sayText('customization_home_columns', {columns: 5}), typed: true}});
  assert.match(seen[0].body.text, /customization_home_columns with \{"columns":5\}/);
  assert.match(logs.join('\n'), /not confirmed by this CLI/);
  assert.equal(await main(['--say', '--port', String(port), 'state'], out), 1, 'not ready is not success');
  assert.match(errors.join('\n'), /still loading/);
  assert.equal(await main(['--say', '--port', String(port), 'wallpaper', 'teal'], out), 2, 'invalid input never reaches Rose');
  assert.equal(seen.length, 2);
 } finally { server.close(); }
 assert.throws(() => sayText('customization_glasses_hook', {packageName: 'a.' + 'b'.repeat(400)}), UsageError);
 assert.equal(await main(['--say', '--port', '1', 'state'], out), 1, 'unreachable Rose is a failure');
});

test('Python conversation module builds the identical command', () => {
 const py = spawnSync('python3', ['-I', '-c', 'import json,sys; sys.path.insert(0, sys.argv[1]); from rose import customization as c; print(json.dumps(c.command("customization_home_columns", {"columns": 5})))',
  new URL('../../../prototypes/voice-phone-demo', import.meta.url).pathname], {encoding: 'utf8'});
 assert.equal(py.status, 0, py.stderr);
 assert.deepEqual(JSON.parse(py.stdout), appCommand('customization_home_columns', {columns: 5}));
});
