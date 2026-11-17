#!/usr/bin/env node
// The app writer's live denial check (docs/APP-BUILDER.md §4A), model-free: each probe runs as one command under
// exactly the writer's permission profile (the same -c overrides the bridge passes Codex, from app-build.mjs),
// through Codex's own sandbox command, in a project source folder as its workspace. No model, writer or network.
//
//   node app-build-sandbox-check.mjs --source DIR [--protected DIR]... [--codex BIN] [--subcommand "sandbox macos"]
//
// --source:    a project source folder (where the writer's turn runs: the builder's root/projects/<id>/source).
// --protected: folders the writer must not read, such as ROSE_CONFIG_DIR, the builder root's keys/ or the owner's
//              home. Each is only listed, never written.
// The check makes a sibling folder beside --source and a file in the system temp folder, and removes both after.
//
// Each probe must be denied under the profile, or allowed inside the source: a denial counts only when the same
// action works unsandboxed (its control). Network is probed against a listener this script opens on 127.0.0.1;
// the profile's network denial covers loopback too. One JSON line per probe, then {"enforced": true|false}.
// Exit 0 only when every probe is as required; 1 when one is not; 2 on usage or when a control fails.
import {spawnSync} from 'node:child_process';
import {createServer} from 'node:http';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync, readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve, dirname, basename} from 'node:path';
import {appBuildLaunchArgs, supportsAppBuildVersion} from './app-build.mjs';

export function profileArgs() {  // the bridge's own overrides, without app-server's --strict-config
 return appBuildLaunchArgs().filter(arg => arg !== '--strict-config');
}

function parse(argv) {
 const out = {protected: [], codex: process.env.PIXEL_CODEX_BIN || 'codex', subcommand: 'sandbox macos'};
 for (let i = 0; i < argv.length; i++) {
  const [key, value] = [argv[i], argv[i + 1]];
  if (value === undefined) throw Error(`${key} needs a value`);
  if (key === '--source') out.source = resolve(value);
  else if (key === '--protected') out.protected.push(resolve(value));
  else if (key === '--codex') out.codex = value;
  else if (key === '--subcommand') out.subcommand = value;
  else throw Error(`unknown option ${key}`);
  i++;
 }
 if (!out.source || !existsSync(out.source)) throw Error('--source must be an existing project source folder');
 return out;
}

const quote = s => `'${String(s).replaceAll("'", "'\\''")}'`;

export async function check(opts, report = line => console.log(JSON.stringify(line))) {
 const version = spawnSync(opts.codex, ['--version'], {encoding: 'utf8'});
 const versionText = (version.stdout || '').trim();
 if (version.status !== 0 || !supportsAppBuildVersion(versionText)) {
  report({check: 'version', ok: false, detail: `Codex ${versionText || 'did not run'}; the writer needs 0.167.1 or later`});
  return {enforced: false, usage: true};
 }
 report({check: 'version', ok: true, detail: versionText});
 const work = mkdtempSync(join(dirname(opts.source), `.${basename(opts.source)}-sibling-`));
 const temp = mkdtempSync(join(tmpdir(), 'rose-sandbox-check-'));
 const inside = join(opts.source, '.rose-sandbox-check');
 mkdirSync(inside, {recursive: true});
 writeFileSync(join(inside, 'read'), 'inside\n');
 writeFileSync(join(work, 'read'), 'sibling\n');
 writeFileSync(join(temp, 'read'), 'temp\n');
 const server = createServer((req, res) => res.end('reachable'));
 await new Promise(done => server.listen(0, '127.0.0.1', done));
 const url = `http://127.0.0.1:${server.address().port}/`;
 const probes = [
  {name: 'read inside the source', allow: true, cmd: `cat ${quote(join(inside, 'read'))}`},
  {name: 'write inside the source', allow: true, cmd: `printf x > ${quote(join(inside, 'written'))}`},
  {name: 'read a sibling project', allow: false, cmd: `cat ${quote(join(work, 'read'))}`},
  {name: 'write a sibling project', allow: false, cmd: `printf x > ${quote(join(work, 'written'))}`},
  {name: 'read the temp folder', allow: false, cmd: `cat ${quote(join(temp, 'read'))}`},
  {name: 'write the temp folder', allow: false, cmd: `printf x > ${quote(join(temp, 'written'))}`},
  ...opts.protected.map(p => ({name: `list ${p}`, allow: false, cmd: `ls -a ${quote(p)}`})),
  {name: 'reach the network (127.0.0.1)', allow: false, cmd: `/usr/bin/curl -s -m 5 ${quote(url)}`},
 ];
 let enforced = true, usage = false;
 try {
  for (const probe of probes) {
   // The control: the same command unsandboxed must work, or a denial would prove nothing.
   const control = spawnSync('/bin/sh', ['-c', probe.cmd], {cwd: opts.source, encoding: 'utf8', timeout: 20000});
   for (const f of ['written']) for (const d of [inside, work, temp]) rmSync(join(d, f), {force: true});
   if (control.status !== 0) {
    report({check: probe.name, ok: false, detail: 'the control (unsandboxed) failed, so this probe cannot judge'});
    enforced = false; usage = true; continue;
   }
   const [first, ...rest] = opts.subcommand.split(' ').filter(Boolean);
   const sandboxed = spawnSync(opts.codex, [...profileArgs(), first, ...rest, '--', '/bin/sh', '-c', probe.cmd],
    {cwd: opts.source, encoding: 'utf8', timeout: 60000});
   const allowed = sandboxed.status === 0;
   const ok = allowed === probe.allow;
   if (!ok) enforced = false;
   report({check: probe.name, ok, detail: `${allowed ? 'allowed' : 'denied'} under the profile (required: ${probe.allow ? 'allowed' : 'denied'})`,
           ...(ok ? {} : {stderr: (sandboxed.stderr || '').slice(-300)})});
  }
 } finally {
  server.close();
  rmSync(work, {recursive: true, force: true});
  rmSync(temp, {recursive: true, force: true});
  rmSync(inside, {recursive: true, force: true});
 }
 report({enforced});
 return {enforced, usage};
}

if (import.meta.url === `file://${process.argv[1]}`) {
 let opts;
 try { opts = parse(process.argv.slice(2)); }
 catch (error) { console.error(`app-build-sandbox-check: ${error.message}`); process.exit(2); }
 const {enforced, usage} = await check(opts);
 process.exit(enforced ? 0 : usage ? 2 : 1);
}
