// 验证: WebCrypto 生成 ed25519 -> OpenSSH 公钥/私钥格式, 能否被本机 ssh-keygen 接受
import { writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const b64 = (u8) => Buffer.from(u8).toString('base64');

function u32(n) {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setUint32(0, n >>> 0, false);
  return b;
}
function str(s) {
  const t = new TextEncoder().encode(s);
  const out = new Uint8Array(4 + t.length);
  out.set(u32(t.length), 0);
  out.set(t, 4);
  return out;
}
function cat(...parts) {
  const n = parts.reduce((a, p) => a + p.length, 0);
  const o = new Uint8Array(n);
  let i = 0;
  for (const p of parts) { o.set(p, i); i += p.length; }
  return o;
}
function b64wrap(b64str) {
  const lines = b64str.match(/.{1,70}/g) || [];
  return '-----BEGIN OPENSSH PRIVATE KEY-----\n' + lines.join('\n') +
         '\n-----END OPENSSH PRIVATE KEY-----\n';
}

function sshPub(pubRaw, comment) {
  const blob = cat(str('ssh-ed25519'), strBytes(pubRaw));
  return 'ssh-ed25519 ' + b64(blob) + (comment ? ' ' + comment : '');
}

function sshPriv(seed, pubRaw, comment) {
  const pubBlob = cat(str('ssh-ed25519'), strBytes(pubRaw));
  const check = crypto.getRandomValues(new Uint8Array(4));
  const privBlob = cat(seed, pubRaw);              // 64 bytes
  let sec = cat(check, check, str('ssh-ed25519'), strBytes(pubRaw), strBytes(privBlob), str(comment || ''));
  // padding to block size 8 (none cipher)
  const pad = 8 - (sec.length % 8);
  if (pad < 8) {
    const p = new Uint8Array(sec.length + pad);
    p.set(sec, 0);
    for (let i = 0; i < pad; i++) p[sec.length + i] = i + 1;
    sec = p;
  }
  const body = cat(
    new TextEncoder().encode('openssh-key-v1\0'),
    str('none'), str('none'), str(''),
    u32(1), strBytes(pubBlob), strBytes(sec)
  );
  return b64wrap(b64(body));
}
function strBytes(b) {
  const out = new Uint8Array(4 + b.length);
  out.set(u32(b.length), 0);
  out.set(b, 4);
  return out;
}

const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
const pkcs8 = new Uint8Array(await crypto.subtle.exportKey('pkcs8', kp.privateKey));
const seed = pkcs8.slice(-32);
const pubRaw = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));

const pub = sshPub(pubRaw, 'ts-remote-control');
const priv = sshPriv(seed, pubRaw, 'ts-remote-control');
console.log('pub =', pub);
console.log('pkcs8 len =', pkcs8.length, 'seed len =', seed.length, 'pub len =', pubRaw.length);

writeFileSync(process.env.TEMP + '/probe_ed25519', priv);
writeFileSync(process.env.TEMP + '/probe_ed25519.pub', pub + '\n');

try {
  const y = execFileSync('ssh-keygen', ['-y', '-f', process.env.TEMP + '/probe_ed25519'], { encoding: 'utf8' });
  console.log('ssh-keygen -y ->', y.trim());
  console.log('MATCH =', y.trim().split(' ').slice(0, 2).join(' ') === pub.split(' ').slice(0, 2).join(' '));
} catch (e) {
  console.log('ssh-keygen -y FAILED:', String(e.stderr || e.message).slice(0, 400));
}
try {
  console.log('ssh-keygen -l ->', execFileSync('ssh-keygen', ['-l', '-f', process.env.TEMP + '/probe_ed25519'], { encoding: 'utf8' }).trim());
} catch (e) {
  console.log('ssh-keygen -l FAILED:', String(e.stderr || e.message).slice(0, 300));
}
