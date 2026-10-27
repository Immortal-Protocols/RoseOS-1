import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import {readState, savePrivate, digest} from './device-auth.mjs';
const dir = os.homedir() + '/.config/pixel-voice';
const file = dir + '/paired-devices.json';
const state = readState(file);
const action = process.argv[2];
if (action === 'invite') {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const code = Array.from({length:12}, () => alphabet[crypto.randomInt(alphabet.length)]).join('');
  state.invite = {hash: digest(code), expiresAt: Date.now() + 10 * 60 * 1000, attempts: 0};
  savePrivate(file, state);
  console.log(JSON.stringify({code: code.match(/.{1,4}/g).join('-'), expiresAt: state.invite.expiresAt}));
} else if (action === 'migration') {
  state.legacyUntil = Date.now() + 60 * 60 * 1000; savePrivate(file, state);
  console.log('Temporary migration enabled for one hour.');
} else if (action === 'finalize') {
  if (!state.devices.some(d => !d.revokedAt)) throw Error('Pair a device before revoking the migration credential');
  delete state.legacyUntil; delete state.invite;
  savePrivate(file, state);
  const tokenFile = dir + '/pair-token', tmp = tokenFile + '.new';
  fs.writeFileSync(tmp, crypto.randomBytes(48).toString('base64url') + '\n', {mode: 0o600});
  fs.renameSync(tmp, tokenFile);
  // Retire the two other secrets previously distributed in the same APK.
  for (const [configFile, field] of [[os.homedir()+'/.config/pixel-clipboard/config.json','receiveToken'], [dir+'/mac-screen/config.json','viewerSecret']]) {
    const config=JSON.parse(fs.readFileSync(configFile,'utf8'));
    config[field]=crypto.randomBytes(48).toString('base64url');savePrivate(configFile,config);
  }
  console.log('Legacy remote access disabled; host-only token rotated.');
} else if (action === 'status') console.log(JSON.stringify({...state, invite: state.invite ? {expiresAt: state.invite.expiresAt, attempts: state.invite.attempts} : null}, null, 2));
else if (action === 'revoke') {
  const device = state.devices.find(d => d.id === process.argv[3]);
  if (!device) throw Error('Unknown device'); device.revokedAt = Date.now(); savePrivate(file, state); console.log('Device revoked.');
} else throw Error('Use invite, migration, finalize, status, or revoke DEVICE_ID');
