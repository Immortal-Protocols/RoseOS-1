import test from 'node:test';
import assert from 'node:assert/strict';
import {isBackgroundSafeAppCommand, filterBackgroundNativeCatalogs,
 backgroundAppCommandRejection} from './background-app-policy.mjs';

const command = (packageName, operation, extra = {}) => ({action: 'app_command', packageName, operation, ...extra});

test('headless native calls survive while known foreground paths fail closed', () => {
 for (const [pkg, operation] of [
  ['dev.pixel.calculator.agent', 'evaluate'], ['dev.pixel.voice', 'brightness'],
  ['dev.pixel.voice', 'youtube_state'], ['dev.pixel.weather', 'forecast'],
  ['com.android.contacts', 'contacts_get'], ['com.android.gallery3d', 'gallery_search'],
  ['dev.pixel.mail', 'mail_send_status'], ['org.fossify.calendar.pixel', 'calendar_create_event'],
 ]) assert.equal(isBackgroundSafeAppCommand(command(pkg, operation)), true, pkg + ':' + operation);
 for (const [pkg, operation] of [
  ['dev.pixel.voice', 'open_panel'], ['dev.pixel.voice', 'request_access'],
  ['dev.pixel.voice', 'youtube_search'], ['dev.pixel.podcasts', 'search'],
  ['dev.pixel.weather', 'open_location'], ['dev.pixel.books', 'get_state'],
  ['dev.pixel.reminders', 'list'], ['dev.pixel.compass', 'heading'],
  ['com.android.contacts', 'contacts_open'], ['com.android.gallery3d', 'gallery_open_media'],
  ['dev.pixel.mail', 'mail_open_message'], ['org.fossify.calendar.pixel', 'calendar_open_event'],
  ['dev.pixel.aicamera', 'inspect'], ['dev.pixel.unknown', 'get_state'],
 ]) assert.equal(isBackgroundSafeAppCommand(command(pkg, operation, {foregroundRequired: false})), false, pkg + ':' + operation);
 assert.equal(isBackgroundSafeAppCommand(command('dev.pixel.voice', 'new_future_operation')), false);
 assert.equal(isBackgroundSafeAppCommand(command('dev.pixel.voice', 'brightness', {action: 'open_app'})), false);
 assert.equal(isBackgroundSafeAppCommand(null), false);
});

test('capability reads require an audited provider identity', () => {
 assert.equal(isBackgroundSafeAppCommand(command('dev.pixel.podcasts', 'capabilities')), true);
 assert.equal(isBackgroundSafeAppCommand(command('dev.pixel.unknown', 'capabilities')), false);
});

test('catalog filtering removes dangerous definitions and unnecessary app opens without mutating input', () => {
 const schema = {type: 'object', properties: {percent: {type: 'number'}}};
 const input = {
  'dev.pixel.voice': {status: 'ok', versionCode: 2, tools: [
   {name: 'settings_brightness', operation: 'brightness', inputSchema: schema, foregroundRequired: true, launchPackage: 'dev.pixel.voice'},
   {name: 'settings_open_panel', operation: 'open_panel', foregroundRequired: false},
  ]},
  'dev.pixel.books': {status: 'ok', tools: [{name: 'books_state', operation: 'get_state', foregroundRequired: false}]},
 };
 const before = structuredClone(input), result = filterBackgroundNativeCatalogs(input);
 assert.deepEqual(input, before);
 assert.equal(result['dev.pixel.voice'].tools.length, 1);
 assert.equal(result['dev.pixel.voice'].versionCode, 2);
 assert.equal(result['dev.pixel.voice'].tools[0].foregroundRequired, false);
 assert.equal('launchPackage' in result['dev.pixel.voice'].tools[0], false);
 assert.deepEqual(result['dev.pixel.voice'].tools[0].inputSchema, schema);
 assert.deepEqual(result['dev.pixel.books'].tools, []);
 result['dev.pixel.voice'].tools[0].inputSchema.properties.percent.type = 'string';
 assert.equal(schema.properties.percent.type, 'number');
 assert.throws(() => filterBackgroundNativeCatalogs({'dev.pixel.voice': {tools: null}}), TypeError);
});

test('rejected native operations explicitly direct background UI without claiming dispatch', () => {
 const result = backgroundAppCommandRejection(command('dev.pixel.podcasts', 'search'));
 assert.equal(result.status, 'not_executed');
 assert.equal(result.fallback, 'background_ui');
 assert.equal(result.nativeOperationDispatched, false);
 assert.match(result.reason, /generic background phone UI/);
});
