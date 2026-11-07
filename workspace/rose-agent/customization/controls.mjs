// Nov 7 plan 1O: the four live Rose customization controls, shared by
// conversation and this CLI. One catalog (app/assets/customization-agent-tools.json)
// defines them; conversation registers it as dev.pixel.voice app tools and the
// CLI builds the identical app_command from it. Nothing here touches the network.
import fs from 'node:fs';

export const PACKAGE_NAME = 'dev.pixel.voice';
export const DEFAULT_CATALOG = new URL('../app/assets/customization-agent-tools.json', import.meta.url);

export class UsageError extends Error {}

export function loadCatalog(path = DEFAULT_CATALOG) {
 const tools = JSON.parse(fs.readFileSync(path, 'utf8'));
 if (!Array.isArray(tools)) throw Error('Customization catalog must be an array');
 for (const spec of tools)
  if (typeof spec?.name !== 'string' || spec.operation !== spec.name || spec.inputSchema?.type !== 'object')
   throw Error('Invalid customization tool definition');
 return tools;
}

// Mirrors server/app-tool-catalog.mjs execute(): same checks, order and messages.
// Returns null when accepted, or the server's error message.
export function validateArgs(spec, args) {
 const schema = spec.inputSchema;
 if (!args || typeof args !== 'object' || Array.isArray(args)) return 'Arguments must be an object';
 for (const key of schema.required || []) if (!(key in args)) return 'Missing argument: ' + key;
 for (const [key, value] of Object.entries(args)) {
  const field = schema.properties[key]; if (!field) return 'Unknown argument: ' + key;
  if (field.type === 'string' && typeof value !== 'string' || field.type === 'integer' && !Number.isInteger(value)) return 'Invalid argument: ' + key;
  if (field.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value)) || field.type === 'boolean' && typeof value !== 'boolean') return 'Invalid argument: ' + key;
  if (field.enum && !field.enum.includes(value)) return 'Unsupported argument: ' + key;
  if (field.minimum !== undefined && value < field.minimum || field.maximum !== undefined && value > field.maximum) return 'Argument outside supported range: ' + key;
 }
 return null;
}

// The exact command the server sends for a registered dev.pixel.voice app tool.
export function appCommand(toolName, args) {
 return {action: 'app_command', packageName: PACKAGE_NAME, operation: toolName, params: args};
}

const SUBCOMMANDS = {
 state: 'customization_state', columns: 'customization_home_columns', wallpaper: 'customization_wallpaper',
 font: 'customization_font', 'glasses-hook': 'customization_glasses_hook',
};

function one(rest, name, what) {
 if (rest.length !== 1) throw new UsageError(name + ' takes exactly one ' + what);
 return rest[0];
}

// argv (without node/script and without mode flags) -> {kind:'tool', tool, args} | {kind:'list'} | {kind:'help'}.
// Values are converted, not validated: validateArgs gives the server's verdict.
export function parseArgv(argv) {
 const [name, ...rest] = argv;
 if (name === undefined || name === 'help' || name === '--help' || name === '-h') return {kind: 'help'};
 if (name === 'list') { if (rest.length) throw new UsageError('list takes no arguments'); return {kind: 'list'}; }
 const tool = SUBCOMMANDS[name];
 if (!tool) throw new UsageError('Unknown command: ' + name);
 if (name === 'state') { if (rest.length) throw new UsageError('state takes no arguments'); return {kind: 'tool', tool, args: {}}; }
 if (name === 'columns') {
  const raw = one(rest, name, 'value: auto, 0 or 3 to 8');
  return {kind: 'tool', tool, args: {columns: raw === 'auto' ? 0 : /^-?\d+$/.test(raw) ? Number(raw) : raw}};
 }
 if (name === 'wallpaper') return {kind: 'tool', tool, args: {variant: one(rest, name, 'variant')}};
 if (name === 'font') return {kind: 'tool', tool, args: {font: one(rest, name, 'font')}};
 const args = {};
 for (let i = 0; i < rest.length; i++) {
  const flag = rest[i];
  if (flag === '--on' || flag === '--off') {
   if ('enabled' in args) throw new UsageError('Use only one of --on or --off');
   args.enabled = flag === '--on';
  } else if (flag === '--tray' || flag === '--app') {
   if ('action' in args) throw new UsageError('Use only one of --tray or --app <package>');
   args.action = flag.slice(2);
   if (flag === '--app') {
    const pkg = rest[++i];
    if (pkg === undefined || pkg.startsWith('--')) throw new UsageError('--app needs a package name');
    args.packageName = pkg;
   }
  } else throw new UsageError('Unknown glasses-hook option: ' + flag);
 }
 if (!Object.keys(args).length) throw new UsageError('glasses-hook needs at least one of --on, --off, --tray or --app <package>');
 return {kind: 'tool', tool, args};
}

// Controls and allowed values, derived from the catalog's schemas.
export function describeCatalog(catalog) {
 const lines = [];
 const sub = Object.fromEntries(Object.entries(SUBCOMMANDS).map(([k, v]) => [v, k]));
 for (const spec of catalog) {
  const props = Object.entries(spec.inputSchema.properties || {});
  const fields = props.map(([key, f]) => '  ' + key + ' (' + f.type + ')' + (f.enum ? ': ' + f.enum.join(', ') : '')
   + (f.minimum !== undefined || f.maximum !== undefined ? ' range ' + (f.minimum ?? '') + '..' + (f.maximum ?? '') : ''));
  lines.push((sub[spec.name] || spec.name) + '  [' + spec.name + ']' + (props.length ? '' : '  (no arguments)'), ...fields);
 }
 return lines.join('\n');
}
