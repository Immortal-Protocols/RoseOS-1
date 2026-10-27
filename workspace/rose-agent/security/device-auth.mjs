import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export function savePrivate(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true, mode: 0o700});
  const tmp = file + '.tmp-' + crypto.randomUUID();
  fs.writeFileSync(tmp, JSON.stringify(value, null, 2), {mode: 0o600});
  fs.renameSync(tmp, file);
}
export function readState(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (e) { if (e.code === 'ENOENT') return {devices: []}; throw e; }
}
export const digest = value => crypto.createHash('sha256').update(value).digest('hex');
export const equal = (a, b) => typeof a === 'string' && typeof b === 'string' && Buffer.byteLength(a) === Buffer.byteLength(b) && crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
export function peerIdentity(req) {
  // TLS proves possession of the private key. We trust explicitly paired keys,
  // not arbitrary self-signed certificates and not client-supplied HTTP headers.
  const raw = req.socket?.getPeerCertificate?.()?.raw;
  if (!req.socket?.encrypted || !raw) return null;
  try {
    const cert = new crypto.X509Certificate(raw);
    if (Date.now() < Date.parse(cert.validFrom) || Date.now() > Date.parse(cert.validTo)) return null;
    if (cert.publicKey.asymmetricKeyType !== 'ec' || cert.publicKey.asymmetricKeyDetails?.namedCurve !== 'prime256v1') return null;
    return {id: digest(cert.publicKey.export({type: 'spki', format: 'der'}))};
  } catch { return null; }
}
export class DeviceAuth {
  constructor(file) { this.file = file; this.seen = new Map(); }
  paired(req) {
    const peer = peerIdentity(req);
    if (!peer || !readState(this.file).devices.some(d => d.id === peer.id && !d.revokedAt)) return false;
    this.seen.set(peer.id, Date.now()); return true;
  }
  legacyAllowed() { return Number(readState(this.file).legacyUntil) > Date.now(); }
  authorize(req, tokenFile) {
    return this.authorizeSecret(req, tokenFile ? fs.readFileSync(tokenFile, 'utf8').trim() : null);
  }
  authorizeSecret(req, secret) {
    if (this.paired(req)) return true;
    const local = ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket?.remoteAddress);
    // The old phone credential is accepted only during an explicit, expiring
    // migration. Afterward, the rotated host token works on loopback only.
    if ((!local && !this.legacyAllowed()) || !secret) return false;
    return equal(req.headers.authorization || '', 'Bearer ' + secret);
  }
  enroll(req, code, label) {
    const peer = peerIdentity(req), state = readState(this.file), invite = state.invite;
    if (!peer || !invite || invite.expiresAt <= Date.now() || invite.attempts >= 10) throw Error('Pairing code unavailable or expired');
    invite.attempts++;
    const normalized = String(code || '').replace(/[ -]/g, '').toUpperCase();
    if (!equal(digest(normalized), invite.hash)) { savePrivate(this.file, state); throw Error('Pairing code did not match'); }
    state.devices = state.devices.filter(d => d.id !== peer.id);
    state.devices.push({id: peer.id, label: String(label || 'Pixel').slice(0, 80), pairedAt: Date.now()});
    delete state.invite; savePrivate(this.file, state);
    return {paired: true, deviceId: peer.id};
  }
  status() { const s = readState(this.file); return {legacyAllowed: this.legacyAllowed(), devices: s.devices.map(d => ({...d, lastSeen: this.seen.get(d.id) || null}))}; }
}
