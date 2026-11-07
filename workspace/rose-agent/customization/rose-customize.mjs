#!/usr/bin/env node
// Rose customization CLI (Nov 7 plan 1O). Uses the same catalog as conversation
// (../app/assets/customization-agent-tools.json): it validates a control change
// exactly as the conversation tool does and builds the identical dev.pixel.voice
// app_command. It never sends that command itself: --say hands the request to
// Rose's own conversation through the Mac dashboard's loopback /api/say, the
// path a typed request takes, so the change runs through the same conversation
// tool, Stop and trust rules as voice. No new endpoint, origin or credential.
import http from 'node:http';
import {loadCatalog, validateArgs, appCommand, parseArgv, describeCatalog, UsageError} from './controls.mjs';

export const SAY_LIMIT = 400; // rose/app.py /api/say keeps the first 400 characters
const DEFAULT_PORT = 8091;    // rose.app --port default

const HELP = `Usage: rose-customize.mjs [--dry-run | --say [--port N]] <command>

Commands:
  state                                  read all four controls
  columns <auto|0|3..8>                  Home column count (auto = 0)
  wallpaper <variant>                    curated Oxblood variant (see list)
  font <font>                            bundled font (see list)
  glasses-hook [--on|--off] [--tray | --app <package>]
                                         glasses-on hook; give at least one option
  list                                   controls and allowed values from the catalog
  help                                   this text

Modes:
  --dry-run (default)  validate and print the exact app_command JSON that the
                       conversation tool sends for this change. Exit 0, or 2
                       with the validator's message for invalid input.
  --say                submit the validated change as a typed request to Rose's
                       conversation on this Mac (POST http://127.0.0.1:PORT/api/say,
                       loopback only, port ${DEFAULT_PORT} unless --port). Rose runs
                       the same customization tool and reports the phone's
                       result in the conversation. The CLI confirms only that
                       Rose accepted the request, not the phone's outcome:
                       read it in Rose's reply, or run "--say state".

Exit codes: 0 printed (dry run) or submitted (say), 1 Rose unreachable or not
ready, 2 invalid input. The CLI never sends a phone command directly.`;

/** The typed request for one validated change: names the catalog tool and its exact arguments. */
export function sayText(tool, args) {
 const text = tool === 'customization_state'
  ? 'Read my Rose customization with customization_state and tell me the returned state.'
  : 'Change my Rose customization: call ' + tool + ' with ' + JSON.stringify(args) + ' and tell me the returned state.';
 if (text.length > SAY_LIMIT) throw new UsageError('Request is too long for Rose\'s typed input');
 return text;
}

function say(port, text) {
 return new Promise((resolve, reject) => {
  const body = Buffer.from(JSON.stringify({text, typed: true}));
  // Host 127.0.0.1:PORT and no Origin: what the dashboard's LoopbackOnly middleware admits.
  const req = http.request({host: '127.0.0.1', port, path: '/api/say', method: 'POST', timeout: 15000,
   headers: {'Content-Type': 'application/json', 'Content-Length': body.length}}, res => {
   const parts = []; res.on('data', p => parts.push(p));
   res.on('end', () => {
    let data; try { data = JSON.parse(Buffer.concat(parts).toString('utf8')); } catch { data = null; }
    resolve({statusCode: res.statusCode, data});
   });
  });
  req.on('timeout', () => req.destroy(Error('Rose did not answer in time')));
  req.on('error', reject);
  req.end(body);
 });
}

export async function main(argv, out = console) {
 let mode = 'dry-run', port = DEFAULT_PORT;
 const rest = [];
 for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--dry-run' || a === '--say') mode = a.slice(2);
  else if (a === '--port') {
   port = Number(argv[++i]);
   if (!Number.isInteger(port) || port < 1 || port > 65535) { out.error('--port needs a port number\n\n' + HELP); return 2; }
  } else rest.push(a);
 }
 let parsed, catalog;
 try { parsed = parseArgv(rest); catalog = loadCatalog(); }
 catch (error) { if (error instanceof UsageError) { out.error(error.message + '\n\n' + HELP); return 2; } throw error; }
 if (parsed.kind === 'help') { out.log(HELP); return 0; }
 if (parsed.kind === 'list') { out.log(describeCatalog(catalog)); return 0; }
 const spec = catalog.find(t => t.name === parsed.tool);
 if (!spec) { out.error('Catalog has no tool ' + parsed.tool); return 2; }
 const invalid = validateArgs(spec, parsed.args);
 if (invalid) { out.error(invalid); return 2; }
 const command = appCommand(spec.name, parsed.args);
 if (mode === 'dry-run') { out.log(JSON.stringify(command)); return 0; }
 let text;
 try { text = sayText(spec.name, parsed.args); } catch (error) { out.error(error.message); return 2; }
 let reply;
 try { reply = await say(port, text); }
 catch (error) { out.error('Rose is not reachable on 127.0.0.1:' + port + ' (' + error.message + ')'); return 1; }
 if (reply.statusCode === 200 && reply.data?.ok) {
  out.log(JSON.stringify({submitted: true, mode: reply.data.mode, text, command}));
  out.log('Submitted to Rose\'s conversation; the phone\'s result is in Rose\'s reply (not confirmed by this CLI).');
  return 0;
 }
 out.error('Rose did not accept the request (HTTP ' + reply.statusCode + '): ' + (reply.data?.error ?? 'no reply'));
 return 1;
}

if (process.argv[1]?.endsWith('rose-customize.mjs')) { // run as the CLI, not imported by its test
 process.exitCode = await main(process.argv.slice(2));
}
