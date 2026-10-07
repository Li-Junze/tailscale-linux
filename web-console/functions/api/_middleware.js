/**
 * ts-remote-relay —— 「Tailscale 远程工具箱 · 网页控制台」的中继 API
 * 运行在 Cloudflare Pages Functions 上（就是 Workers 运行时），绑定 D1。
 *
 * ★ 为什么不是单独的 Worker:
 *   *.workers.dev 在国内被 DNS 污染（解析到 75.126.x / 2001::1f0d 这类非
 *   Cloudflare 的 IP），被控端根本连不上。*.pages.dev 解析正常、可达，
 *   所以整套东西（静态页面 + API）都放在 Pages 上，同一个域名、无跨域。
 *
 * ★ 面向 agent（被控端脚本）的接口设计原则:
 *   1) 全部 JSON / 裸字节，不需要任何 SDK，PowerShell Invoke-RestMethod 直接跑
 *   2) 无状态：只认 room（房间号），不需要登录、不需要 cookie
 *   3) 幂等：重复 pull 不会丢消息（用 after=上一条 id 增量拉）
 *   4) 文件大小未知也能传：先 begin 再分块，不依赖 Content-Length 精确值
 */

const CHUNK = 1180000;                 // 1.18MB / 块 —— D1 单行上限 2MB
const KEEP_DAYS = 7;
const LOBBY = 'lobby';                 // 默认大厅: 不建房间的包都进这里
const ONLINE_MS = 75000;               // 心跳超过这个时间算离线(agent 每 15~30s 一次)

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
};

const json = (o, s = 200) => new Response(JSON.stringify(o), {
  status: s,
  headers: {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store, no-cache, must-revalidate',
    ...CORS,
  },
});
const ok = (x = {}) => json({ ok: true, ...x });
const bad = (m, s = 400) => json({ ok: false, error: m }, s);
const rid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-5);
const sideOf = (s) => (String(s) === 'pc' ? 'pc' : 'web');

/* ---------------------- 自动生成 SSH 密钥对 ----------------------
   网页在云端, 拿不到你本机, 所以干脆在服务端生成 ed25519 密钥对:
   公钥烘焙进部署包(被控端免密), 私钥当场下载给你(用来 ssh 上去)。
   格式必须能直接被 OpenSSH 用, 所以这里手写 openssh-key-v1 容器。 */
function u32be(n) {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setUint32(0, n >>> 0, false);
  return b;
}
function sshStr(s) {                    // SSH wire 格式: uint32 长度 + 内容
  const t = new TextEncoder().encode(s);
  const o = new Uint8Array(4 + t.length);
  o.set(u32be(t.length), 0); o.set(t, 4);
  return o;
}
function sshBytes(b) {
  const o = new Uint8Array(4 + b.length);
  o.set(u32be(b.length), 0); o.set(b, 4);
  return o;
}
function cat(...parts) {
  const n = parts.reduce((a, p) => a + p.length, 0);
  const o = new Uint8Array(n); let i = 0;
  for (const p of parts) { o.set(p, i); i += p.length; }
  return o;
}
function b64(u8) {
  let s = '';
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  return btoa(s);
}
function b64wrap(s) {
  const lines = s.match(/.{1,70}/g) || [];
  return '-----BEGIN OPENSSH PRIVATE KEY-----\n' + lines.join('\n') +
         '\n-----END OPENSSH PRIVATE KEY-----\n';
}
function sshPubLine(pubRaw, comment) {
  return 'ssh-ed25519 ' + b64(cat(sshStr('ssh-ed25519'), sshBytes(pubRaw))) +
         (comment ? ' ' + comment : '');
}
function sshPrivPem(seed, pubRaw, comment) {
  const pubBlob = cat(sshStr('ssh-ed25519'), sshBytes(pubRaw));
  const privBlob = cat(seed, pubRaw);                       // 64B: seed||pub
  const chk = crypto.getRandomValues(new Uint8Array(4));
  let sec = cat(chk, chk, sshStr('ssh-ed25519'), sshBytes(pubRaw),
                sshBytes(privBlob), sshStr(comment || ''));
  const pad = 8 - (sec.length % 8);                          // none 加密, 块长 8
  if (pad > 0 && pad < 8) {
    const p = new Uint8Array(sec.length + pad);
    p.set(sec, 0);
    for (let i = 0; i < pad; i++) p[sec.length + i] = i + 1;
    sec = p;
  }
  return b64wrap(b64(cat(
    new TextEncoder().encode('openssh-key-v1\0'),
    sshStr('none'), sshStr('none'), sshStr(''),
    u32be(1), sshBytes(pubBlob), sshBytes(sec)
  )));
}
async function keygen(env, b) {
  const comment = String(b && b.comment || 'ts-remote-control').slice(0, 60);
  let kp;
  try {
    kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
  } catch (e) {
    return bad('运行时不支持 Ed25519：' + (e && e.message ? e.message : e), 500);
  }
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey('pkcs8', kp.privateKey));
  const seed = pkcs8.slice(-32);                             // ed25519 PKCS#8 末 32B 是 seed
  const pubRaw = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  return ok({
    pub: sshPubLine(pubRaw, comment),
    priv: sshPrivPem(seed, pubRaw, comment),
    fp: 'SHA256:' + b64(new Uint8Array(await crypto.subtle.digest('SHA-256', pubRaw)))
                      .replace(/=+$/, ''),
    type: 'ed25519',
  });
}

let READY = false;
async function schema(env) {
  if (READY) return;
  await env.DB.batch([
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS msg(
       id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL,
       side TEXT NOT NULL, kind TEXT NOT NULL, body TEXT, ts INTEGER NOT NULL)`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS idx_msg_room ON msg(room, id)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS file(
       id TEXT PRIMARY KEY, room TEXT NOT NULL, side TEXT NOT NULL,
       name TEXT NOT NULL, size INTEGER NOT NULL, chunks INTEGER NOT NULL,
       ts INTEGER NOT NULL)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS chunk(
       file_id TEXT NOT NULL, idx INTEGER NOT NULL, data BLOB NOT NULL,
       PRIMARY KEY(file_id, idx))`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS presence(
       room TEXT NOT NULL, side TEXT NOT NULL, ts INTEGER NOT NULL,
       info TEXT, PRIMARY KEY(room, side))`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS acl(
       kind TEXT NOT NULL, val TEXT NOT NULL, ts INTEGER NOT NULL,
       PRIMARY KEY(kind, val))`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS bridge_task(
       id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL, host TEXT NOT NULL,
       type TEXT NOT NULL, args TEXT, status TEXT NOT NULL DEFAULT 'new',
       out TEXT, ts INTEGER NOT NULL)`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS idx_bridge ON bridge_task(token, id)`),
  ]);
  READY = true;
}

/* ---------------------- ZIP 打包(纯 JS, stored 模式) ----------------------
   Workers 里没有 Node 的 zlib/fs, 也没法装 npm 包。
   zip 的 stored(不压缩) 格式只要算 CRC32 + 拼头就行, 全部手写。
   ★ zip 内文件名一律 ASCII: PS5.1 的 Expand-Archive 对 UTF-8 文件名支持很差,
     中文放进文件内容(UTF-8 BOM / GBK)没问题, 放进文件名必炸。            */
let _CRC = null;
function crcTable() {
  if (_CRC) return _CRC;
  _CRC = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    _CRC[n] = c >>> 0;
  }
  return _CRC;
}
function crc32(u8) {
  const t = crcTable(); let c = 0xFFFFFFFF;
  for (let i = 0; i < u8.length; i++) c = t[(c ^ u8[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}
function dosTime(d) {
  return {
    date: ((d.getUTCFullYear() - 1980) << 9) | ((d.getUTCMonth() + 1) << 5) | d.getUTCDate(),
    time: (d.getUTCHours() << 11) | (d.getUTCMinutes() << 5) | (d.getUTCSeconds() >> 1),
  };
}
function zipStore(entries) {
  const enc = new TextEncoder();
  const { date, time } = dosTime(new Date());
  const parts = [], central = [];
  let offset = 0;
  for (const e of entries) {
    const nb = enc.encode(e.name);
    const crc = crc32(e.data), n = e.data.length;
    const lh = new Uint8Array(30 + nb.length);
    const lv = new DataView(lh.buffer);
    lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true);
    lv.setUint16(6, 0x0800, true);     lv.setUint16(8, 0, true);
    lv.setUint16(10, time, true);      lv.setUint16(12, date, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, n, true);          lv.setUint32(22, n, true);
    lv.setUint16(26, nb.length, true);  lv.setUint16(28, 0, true);
    lh.set(nb, 30);
    parts.push(lh, e.data);

    const ch = new Uint8Array(46 + nb.length);
    const cv = new DataView(ch.buffer);
    cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x0800, true);     cv.setUint16(10, 0, true);
    cv.setUint16(12, time, true);      cv.setUint16(14, date, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, n, true);          cv.setUint32(24, n, true);
    cv.setUint16(28, nb.length, true);
    cv.setUint32(42, offset, true);
    ch.set(nb, 46);
    central.push(ch);
    offset += lh.length + n;
  }
  let cdSize = 0; for (const c of central) cdSize += c.length;
  const eo = new Uint8Array(22); const ev = new DataView(eo.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, central.length, true); ev.setUint16(10, central.length, true);
  ev.setUint32(12, cdSize, true);        ev.setUint32(16, offset, true);
  const out = new Uint8Array(offset + cdSize + 22);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  for (const c of central) { out.set(c, o); o += c.length; }
  out.set(eo, o);
  return out;
}

/** 读同项目的静态资源（Pages 的 ASSETS binding, 取不到就回落到 fetch） */
async function grabAsset(context, path) {
  const url = new URL('/pkg/' + path, context.request.url).toString();
  let r = null;
  try { if (context.env && context.env.ASSETS) r = await context.env.ASSETS.fetch(url); } catch (e) { r = null; }
  if (!r || !r.ok) { try { r = await fetch(url); } catch (e) { r = null; } }
  if (!r || !r.ok) throw new Error('打包资源缺失: ' + path);
  return new Uint8Array(await r.arrayBuffer());
}

/* ---------------------- 生成部署包 ---------------------- */
async function buildPkg(context, b) {
  const room = String(b.room || '').trim() || LOBBY;   // 留空就进默认大厅
  const relay    = String(b.relay || 'https://ts-remote-web.pages.dev').replace(/\/+$/, '');
  const ctrlUser = String(b.ctrlUser || '').trim();
  const ctrlHost = String(b.ctrlHost || '').trim();
  const ctrlPath = String(b.ctrlPath || 'Desktop').trim() || 'Desktop';
  const pub      = String(b.pub || '').trim();
  const authkey  = String(b.authkey || '').trim();
  const plat     = (String(b.plat || 'win') === 'linux') ? 'linux' : 'win';

  // 每个包现场签一个设备令牌: 门禁只放行持有令牌的被控端, 否则 agent 自己也被挡在门外
  const ptok = 'p' + rid();
  try {
    await context.env.DB.prepare(
      'INSERT OR REPLACE INTO acl(kind, val, ts) VALUES(?,?,?)'
    ).bind('tok', ptok, Date.now()).run();
  } catch (e) { /* 表还没建好就退化成无令牌, 不让打包失败 */ }

  const dec = new TextDecoder('utf-8');
  const enc = new TextEncoder();
  const BOM = [0xEF, 0xBB, 0xBF];
  const P = 'tailscale-remote/';
  const out = [];
  const push = (name, text, withBom) => {
    const body = enc.encode(text);
    if (withBom === false) { out.push({ name: P + name, data: body }); return; }
    const u = new Uint8Array(BOM.length + body.length);
    u.set(BOM, 0); u.set(body, BOM.length);
    out.push({ name: P + name, data: u });
  };
  const stripBom = (s) => s.replace(/^﻿/, '');

  if (plat === 'linux') {
    let s = stripBom(dec.decode(await grabAsset(context, 'install.sh')));
    s = s.split('__ROOM__').join(room).split('__RELAY__').join(relay)
         .split('__CTRL_USER__').join(ctrlUser).split('__CTRL_HOST__').join(ctrlHost)
         .split('__CTRL_PATH__').join(ctrlPath).split('__PTOK__').join(ptok);
    push('install.sh', s, false);
    push('notify.sh', stripBom(dec.decode(await grabAsset(context, 'notify.sh'))), false);
    push('clean.sh', stripBom(dec.decode(await grabAsset(context, 'clean.sh'))), false);
  } else {
    let s = stripBom(dec.decode(await grabAsset(context, 'install.ps1')));
    s = s.split('__ROOM__').join(room).split('__RELAY__').join(relay)
         .split('__CTRL_USER__').join(ctrlUser).split('__CTRL_HOST__').join(ctrlHost)
         .split('__CTRL_PATH__').join(ctrlPath).split('__PTOK__').join(ptok);
    push('install.ps1', s);
    push('notify.ps1', stripBom(dec.decode(await grabAsset(context, 'notify.ps1'))));
    push('clean.ps1', stripBom(dec.decode(await grabAsset(context, 'clean.ps1'))));
  }
  if (pub)     push('keys/control.pub', pub + '\n', false);
  if (authkey) push('keys/authkey.local.txt', authkey + '\n', false);

  const readme = [
    'Tailscale 远程工具箱 · 被控端部署包',
    '==========================================',
    '',
    '房间号        : ' + room,
    '中继地址      : ' + relay,
    '控制端回信    : ' + (ctrlHost ? (ctrlUser + '@' + ctrlHost + ':' + ctrlPath) : '(未配置, 信息只走中继)'),
    '生成时间      : ' + new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }),
    '',
    '内容:',
    '  install.' + (plat === 'linux' ? 'sh' : 'ps1') + '   一键部署主脚本(外层引导脚本会自动调用它)',
    '  notify.' + (plat === 'linux' ? 'sh' : 'ps1') + '    消息通道(右下角弹窗 + 双向传文件)',
    '  clean.' + (plat === 'linux' ? 'sh' : 'ps1') + '     本地卸载',
    '  keys/control.pub        控制端公钥(免密 ssh)',
    '  keys/authkey.local.txt  Tailscale 入网 key',
    '',
    '安装后位置:',
    '  Windows: %LOCALAPPDATA%\\TailscaleRemote',
    '  Linux  : ~/.local/share/tailscale-remote',
    '',
    '卸载: 运行 clean 脚本, 或在网页控制台上点「清除」.',
    '',
  ].join('\r\n');
  push('README.txt', readme);

  const zip = zipStore(out);
  const fname = 'setup-' + room + '.zip';
  return new Response(zip, {
    headers: {
      'Content-Type': 'application/zip',
      'Content-Length': String(zip.length),
      'Content-Disposition': `attachment; filename="${fname}"`,
      'Cache-Control': 'no-store',
      ...CORS,
    },
  });
}

/* ---------------------- 已注册设备(供网页远程清除) ----------------------
   ★ 同一台机器可能在多个房间上报过(测试包、单独会话...)，按 主机|用户|IP 合并,
     只留最新一条, 否则列表里会出现一堆同名重复项。
   ★ room 取最新心跳那条 —— 下发指令要发到它当前正在听的房间。 */
async function devices(env) {
  const rows = await env.DB.prepare(
    `SELECT room, side, ts, info FROM presence WHERE side='pc' ORDER BY ts DESC LIMIT 500`
  ).all();
  const map = new Map();
  const now = Date.now();
  for (const r of (rows.results || [])) {
    let info = {};
    try { info = r.info ? JSON.parse(r.info) : {}; } catch (e) { info = {}; }
    const host = info.host || '', user = info.user || '', ip = info.ip || '';
    // ★ 优先按主机名合并: 同一个 host 就是同一台机器。
    //   早期上报可能缺 ip、甚至缺 user, 把那些字段放进键里会把一台机器
    //   拆成好几条 —— 这正是"列表里乱七八糟很多个"的根源。
    const key = host ? host : ((user || ip) ? (user + '|' + ip) : ('room:' + r.room));
    const cur = map.get(key);
    if (!cur) {
      map.set(key, { key, room: r.room, ts: r.ts, rooms: [r.room], host, user, ip,
                     path: info.path || '', ver: info.ver || '' });
      continue;
    }
    if (r.ts > cur.ts) { cur.ts = r.ts; cur.room = r.room; }
    if (cur.rooms.indexOf(r.room) < 0) cur.rooms.push(r.room);
    // 后来的上报可能补全了空字段
    if (!cur.ip && ip) cur.ip = ip;
    if (!cur.path && info.path) cur.path = info.path;
    if (!cur.ver && info.ver) cur.ver = info.ver;
  }
  const list = [];
  let stale = 0;
  for (const d of map.values()) {
    const age = now - d.ts;
    if (age > 86400000) stale++;
    list.push({
      room: d.room, rooms: d.rooms, ts: d.ts, age,
      online: age < ONLINE_MS,
      host: d.host, user: d.user, ip: d.ip, path: d.path, ver: d.ver,
    });
  }
  list.sort((a, b) => (b.online - a.online) || (b.ts - a.ts));
  return ok({ devices: list, now, onlineMs: ONLINE_MS, stale });
}

/* 清理僵尸条目: 指定房间, 或所有 24h 没心跳的 */
async function forget(env, b) {
  const body = b || {};
  if (body.old) {
    const r = await env.DB.prepare(
      'DELETE FROM presence WHERE side=? AND ts < ?'
    ).bind('pc', Date.now() - 86400000).run();
    return ok({ deleted: (r.meta && r.meta.changes) || 0, mode: 'old' });
  }
  const room = String(body.room || '').trim();
  if (!room) return bad('需要 room 或 old:true');
  const r = await env.DB.prepare(
    'DELETE FROM presence WHERE room=? AND side=?'
  ).bind(room, 'pc').run();
  return ok({ deleted: (r.meta && r.meta.changes) || 0, room });
}

/* ---------------------- 控制端本机信息(免手填) ----------------------
   网页在云端, 看不到你本机的 IP / 用户名 / 公钥。给一个一次性 token,
   你在本机跑一条命令把这些信息 POST 上来, 网页自动填进表单。
   authkey 默认不上传(敏感), 只有你显式带上才收。 */
async function ctrlGet(env, url) {
  const token = String(url.searchParams.get('token') || '').trim();
  if (!token) return bad('缺少 token');
  const r = await env.DB.prepare(
    'SELECT ts, info FROM presence WHERE room=? AND side=?'
  ).bind('ctrl-' + token, 'ctrl').first();
  if (!r) return ok({ found: false });
  let info = {};
  try { info = r.info ? JSON.parse(r.info) : {}; } catch (e) { info = {}; }
  return ok({ found: true, ts: r.ts, info });
}
async function ctrlPost(env, b) {
  const token = String((b && b.token) || '').trim();
  if (!token) return bad('缺少 token');
  const info = (b && b.info) || {};
  const clean = {
    host: String(info.host || '').slice(0, 80),
    user: String(info.user || '').slice(0, 80),
    ip: String(info.ip || '').slice(0, 60),
    pub: String(info.pub || '').slice(0, 1200),
    tsip: String(info.tsip || '').slice(0, 60),
    ver: String(info.ver || '').slice(0, 40),
  };
  if (b && b.authkey) clean.authkey = String(b.authkey).slice(0, 300);
  await env.DB.prepare(
    `INSERT INTO presence(room, side, ts, info) VALUES(?,?,?,?)
     ON CONFLICT(room, side) DO UPDATE SET ts=excluded.ts, info=excluded.info`
  ).bind('ctrl-' + token, 'ctrl', Date.now(), JSON.stringify(clean)).run();
  return ok({ saved: true });
}

/* ---------------------- 门禁: 令牌签发 + IP 白名单 ---------------------- */
async function newTok(env) {
  const t = 't' + rid();
  await env.DB.prepare('INSERT OR REPLACE INTO acl(kind, val, ts) VALUES(?,?,?)')
    .bind('tok', t, Date.now()).run();
  return ok({ tok: t });
}
async function aclList(env, request) {
  const rows = await env.DB.prepare(
    "SELECT val, ts FROM acl WHERE kind='ip' ORDER BY ts DESC LIMIT 50"
  ).all();
  const ips = (rows.results || []).map((r) => ({
    ip: r.val, ts: r.ts, days: Math.round((30 * 86400000 - (Date.now() - r.ts)) / 86400000),
  }));
  return ok({ ips, me: request.headers.get('CF-Connecting-IP') || '' });
}
async function aclEdit(env, b, request) {
  if (b.addIp) {
    const ip = request.headers.get('CF-Connecting-IP') || '';
    if (!ip) return bad('拿不到你的出口 IP');
    await env.DB.prepare('INSERT OR REPLACE INTO acl(kind, val, ts) VALUES(?,?,?)')
      .bind('ip', ip, Date.now()).run();
    return ok({ added: ip });
  }
  if (b.delIp) {
    await env.DB.prepare('DELETE FROM acl WHERE kind=? AND val=?').bind('ip', String(b.delIp)).run();
    return ok({ deleted: String(b.delIp) });
  }
  if (b.addTok) {                         // 给本机桥接/上报通道开一个令牌(可看页面/下脚本)
    const t = String(b.addTok).slice(0, 40);
    await env.DB.prepare('INSERT OR REPLACE INTO acl(kind, val, ts) VALUES(?,?,?)')
      .bind('btok', t, Date.now()).run();
    return ok({ tok: t });
  }
  if (b.code) {                           // 换授权码: 只存哈希, 不存明文
    const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(String(b.code)));
    const h = [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, '0')).join('');
    await env.DB.prepare('DELETE FROM acl WHERE kind=?').bind('code').run();
    await env.DB.prepare('INSERT OR REPLACE INTO acl(kind, val, ts) VALUES(?,?,?)')
      .bind('code', h, Date.now()).run();
    return ok({ changed: true });
  }
  return bad('需要 addIp / delIp / addTok / code');
}

/* ---------------------- 控制端桥接(接已有的 SSH 连接) ----------------------
   浏览器自己不能 SSH。所以在你本机跑一个小桥接进程:
     它读 ~/.ssh/config -> 探测哪些主机现在连得上 -> 上报到网页;
     网页下发任务(部署/执行/卸载) -> 它用 ssh/scp 去干 -> 结果回传网页。
   桥接只在你本机跑, 不需要在对方机器上预装任何东西。 */
async function bridgeHello(env, b) {
  const token = String((b && b.token) || '').trim();
  if (!token) return bad('缺少 token');
  const hosts = (b && Array.isArray(b.hosts)) ? b.hosts.slice(0, 200).map((h) => ({
    alias: String(h.alias || '').slice(0, 80),
    user: String(h.user || '').slice(0, 80),
    host: String(h.host || '').slice(0, 120),
    os: String(h.os || '').slice(0, 20),
    ok: !!h.ok,
    note: String(h.note || '').slice(0, 200),
  })) : [];
  await env.DB.prepare(
    `INSERT INTO presence(room, side, ts, info) VALUES(?,?,?,?)
     ON CONFLICT(room, side) DO UPDATE SET ts=excluded.ts, info=excluded.info`
  ).bind('bridge-' + token, 'bridge', Date.now(), JSON.stringify({ hosts })).run();
  return ok({ saved: true, n: hosts.length });
}
async function bridgeGet(env, url) {
  const token = String(url.searchParams.get('token') || '').trim();
  if (!token) return bad('缺少 token');
  const r = await env.DB.prepare(
    'SELECT ts, info FROM presence WHERE room=? AND side=?'
  ).bind('bridge-' + token, 'bridge').first();
  if (!r) return ok({ online: false, hosts: [] });
  let info = {};
  try { info = r.info ? JSON.parse(r.info) : {}; } catch (e) { info = {}; }
  const tasks = await env.DB.prepare(
    'SELECT id, host, type, status, ts, out FROM bridge_task WHERE token=? ORDER BY id DESC LIMIT 30'
  ).bind(token).all();
  return ok({
    online: Date.now() - r.ts < 120000, ts: r.ts,
    hosts: info.hosts || [], tasks: tasks.results || [],
  });
}
async function bridgeTask(env, b) {
  const token = String((b && b.token) || '').trim();
  const host = String((b && b.host) || '').trim();
  const type = String((b && b.type) || '').trim();
  if (!token || !host || !type) return bad('需要 token/host/type');
  if (!['deploy', 'exec', 'remove', 'probe'].includes(type)) return bad('任务类型不合法: ' + type);
  const r = await env.DB.prepare(
    'INSERT INTO bridge_task(token, host, type, args, status, ts) VALUES(?,?,?,?,?,?)'
  ).bind(token, host, type, JSON.stringify((b && b.args) || {}).slice(0, 4000), 'new', Date.now()).run();
  return ok({ id: r.meta.last_row_id, queued: true });
}
async function bridgePoll(env, url) {
  const token = String(url.searchParams.get('token') || '').trim();
  if (!token) return bad('缺少 token');
  const rows = await env.DB.prepare(
    "SELECT id, host, type, args FROM bridge_task WHERE token=? AND status='new' ORDER BY id LIMIT 10"
  ).bind(token).all();
  const out = [];
  for (const r of (rows.results || [])) {
    let args = {};
    try { args = r.args ? JSON.parse(r.args) : {}; } catch (e) { args = {}; }
    await env.DB.prepare("UPDATE bridge_task SET status='run' WHERE id=?").bind(r.id).run();
    out.push({ id: r.id, host: r.host, type: r.type, args });
  }
  return ok({ tasks: out });
}
async function bridgeResult(env, b) {
  const id = parseInt((b && b.id), 10) || 0;
  if (!id) return bad('缺少 id');
  await env.DB.prepare('UPDATE bridge_task SET status=?, out=? WHERE id=?')
    .bind((b && b.ok) ? 'done' : 'fail', String((b && b.out) || '').slice(0, 8000), id).run();
  return ok({ saved: true });
}

/* ------------------------------ 路由 ------------------------------ */
export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const p = url.pathname.replace(/\/+$/, '') || '/';
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
  try {
    if (p === '/api' || p === '/api/') return json({ ok: true, api: API_DOC, chunk: CHUNK });
    if (p === '/api/hello') return json({ ok: true, service: 'ts-remote-relay', now: Date.now(), chunk: CHUNK });
    await schema(env);

    if (p === '/api/build' && request.method === 'POST') return buildPkg(context, await request.json());
    if (p === '/api/keygen' && request.method === 'POST') return keygen(env, await request.json().catch(() => ({})));
    if (p === '/api/tok' && request.method === 'POST') return newTok(env);
    if (p === '/api/acl') {
      if (request.method === 'POST') return aclEdit(env, await request.json(), request);
      return aclList(env, request);
    }
    if (p === '/api/devices') return devices(env);
    if (p === '/api/forget' && request.method === 'POST') return forget(env, await request.json());
    if (p === '/api/ctrl') {
      if (request.method === 'POST') return ctrlPost(env, await request.json());
      return ctrlGet(env, url);
    }
    if (p === '/api/bridge' && request.method === 'POST') return bridgeHello(env, await request.json());
    if (p === '/api/bridge') return bridgeGet(env, url);
    if (p === '/api/bridge/task' && request.method === 'POST') return bridgeTask(env, await request.json());
    if (p === '/api/bridge/tasks') return bridgePoll(env, url);
    if (p === '/api/bridge/result' && request.method === 'POST') return bridgeResult(env, await request.json());
    if (p === '/api/send' && request.method === 'POST') return send(env, await request.json());
    if (p === '/api/beat' && request.method === 'POST') return beat(env, await request.json());
    if (p === '/api/pull') return pull(env, url);
    if (p === '/api/state') return state(env, url);
    if (p === '/api/upload/begin' && request.method === 'POST') return begin(env, await request.json());
    if (p === '/api/upload/chunk' && request.method === 'PUT') return putChunk(env, url, request);
    if (p === '/api/upload/file' && request.method === 'POST') return uploadOne(env, url, request);
    if (p === '/api/fileinfo') return fileinfo(env, url);
    if (p === '/api/download') return chunkOut(env, url);
    if (p === '/api/file') return wholeFile(env, url);
    if (p === '/api/gc') return gc(env);
    return bad('未知接口 ' + p, 404);
  } catch (e) {
    return bad('服务端异常: ' + (e && e.message ? e.message : String(e)), 500);
  }
}

/* ------------------------------ 消息 ------------------------------ */
async function send(env, b) {
  const room = String(b.room || '').trim();
  if (!room) return bad('缺少 room');
  const kind = ['text', 'file', 'cmd', 'sys'].includes(b.kind) ? b.kind : 'text';
  const body = typeof b.body === 'string' ? b.body.slice(0, 8000) : JSON.stringify(b.body || {}).slice(0, 8000);
  const ts = Date.now();
  const r = await env.DB.prepare(
    'INSERT INTO msg(room, side, kind, body, ts) VALUES(?,?,?,?,?)'
  ).bind(room, sideOf(b.side), kind, body, ts).run();
  return ok({ id: r.meta.last_row_id, ts });
}

async function beat(env, b) {
  const room = String(b.room || '').trim();
  if (!room) return bad('缺少 room');
  let info = null;
  if (b.info) { try { info = JSON.stringify(b.info).slice(0, 1500); } catch (e) { info = null; } }
  await env.DB.prepare(
    `INSERT INTO presence(room, side, ts, info) VALUES(?,?,?,?)
     ON CONFLICT(room, side) DO UPDATE SET ts=excluded.ts, info=COALESCE(excluded.info, presence.info)`
  ).bind(room, sideOf(b.side), Date.now(), info).run();
  return ok({ beat: true, ts: Date.now() });
}

async function pull(env, url) {
  const room = String(url.searchParams.get('room') || '').trim();
  if (!room) return bad('缺少 room');
  const after = parseInt(url.searchParams.get('after') || '0', 10) || 0;
  const m = await env.DB.prepare(
    'SELECT id, side, kind, body, ts FROM msg WHERE room=? AND id>? ORDER BY id LIMIT 300'
  ).bind(room, after).all();
  const f = await env.DB.prepare(
    'SELECT id, name, size, chunks, side, ts FROM file WHERE room=? ORDER BY ts DESC LIMIT 80'
  ).bind(room).all();
  const p = await env.DB.prepare('SELECT side, ts, info FROM presence WHERE room=?').bind(room).all();
  const presence = {};
  for (const r of (p.results || [])) {
    let info = {};
    try { info = r.info ? JSON.parse(r.info) : {}; } catch (e) { info = {}; }
    presence[r.side] = { ts: r.ts, ...info };
  }
  return ok({ msgs: m.results || [], files: f.results || [], presence, now: Date.now() });
}

async function state(env, url) {
  const room = String(url.searchParams.get('room') || '').trim();
  if (!room) return bad('缺少 room');
  const p = await env.DB.prepare('SELECT side, ts, info FROM presence WHERE room=?').bind(room).all();
  const presence = {};
  for (const r of (p.results || [])) {
    let info = {};
    try { info = r.info ? JSON.parse(r.info) : {}; } catch (e) { info = {}; }
    presence[r.side] = { ts: r.ts, online: Date.now() - r.ts < 90000, ...info };
  }
  const c = await env.DB.prepare(
    'SELECT COUNT(*) n, MAX(id) last FROM msg WHERE room=?'
  ).bind(room).first();
  return ok({ room, presence, count: c ? c.n : 0, last: c ? c.last : 0, now: Date.now() });
}

/* ------------------------------ 文件 ------------------------------ */
async function begin(env, b) {
  const room = String(b.room || '').trim();
  const name = String(b.name || 'file.bin').slice(0, 180);
  const size = parseInt(b.size, 10) || 0;
  if (!room) return bad('缺少 room');
  if (size <= 0) return bad('文件为空');
  if (size > 300 * 1024 * 1024) return bad('单文件上限 300MB');
  const id = rid();
  const chunks = Math.max(1, Math.ceil(size / CHUNK));
  await env.DB.prepare(
    'INSERT INTO file(id, room, side, name, size, chunks, ts) VALUES(?,?,?,?,?,?,?)'
  ).bind(id, room, sideOf(b.side), name, size, chunks, Date.now()).run();
  return ok({ fileId: id, chunks, chunkSize: CHUNK });
}

async function putChunk(env, url, req) {
  const fileId = String(url.searchParams.get('fileId') || '').trim();
  const idx = parseInt(url.searchParams.get('i') || '0', 10) || 0;
  if (!fileId) return bad('缺少 fileId');
  const buf = await req.arrayBuffer();
  if (!buf.byteLength) return bad('空分块');
  await env.DB.prepare('INSERT OR REPLACE INTO chunk(file_id, idx, data) VALUES(?,?,?)')
    .bind(fileId, idx, buf).run();
  return ok({ i: idx, n: buf.byteLength });
}

/** 小文件一条龙：POST 裸字节 + ?room=&side=&name= ，一步完成上传并广播 */
async function uploadOne(env, url, req) {
  const room = String(url.searchParams.get('room') || '').trim();
  if (!room) return bad('缺少 room');
  const name = String(url.searchParams.get('name') || 'file.bin').slice(0, 180);
  const buf = await req.arrayBuffer();
  if (!buf.byteLength) return bad('空文件');
  if (buf.byteLength > 8 * 1024 * 1024) return bad('本接口上限 8MB，更大的请用 begin+chunk');
  const id = rid();
  await env.DB.prepare('INSERT INTO file(id, room, side, name, size, chunks, ts) VALUES(?,?,?,?,?,?,?)')
    .bind(id, room, sideOf(url.searchParams.get('side')), name, buf.byteLength, 1, Date.now()).run();
  await env.DB.prepare('INSERT OR REPLACE INTO chunk(file_id, idx, data) VALUES(?,?,?)')
    .bind(id, 0, buf).run();
  return ok({ fileId: id, chunks: 1, size: buf.byteLength, name });
}

async function fileinfo(env, url) {
  const id = String(url.searchParams.get('fileId') || '').trim();
  const row = await env.DB.prepare(
    'SELECT id, name, size, chunks, side, ts FROM file WHERE id=?'
  ).bind(id).first();
  return row ? ok({ file: row }) : bad('文件不存在', 404);
}

async function chunkOut(env, url) {
  const id = String(url.searchParams.get('fileId') || '').trim();
  const idx = parseInt(url.searchParams.get('i') || '0', 10) || 0;
  const row = await env.DB.prepare('SELECT data FROM chunk WHERE file_id=? AND idx=?').bind(id, idx).first();
  if (!row) return bad('分块不存在', 404);
  const b = row.data instanceof ArrayBuffer ? row.data : new Uint8Array(row.data).buffer;
  return new Response(b, {
    headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(b.byteLength), ...CORS },
  });
}

/** 整文件直出（≤20MB 走这个最省事，浏览器 <a download> 和脚本都用得上） */
async function wholeFile(env, url) {
  const id = String(url.searchParams.get('fileId') || '').trim();
  const f = await env.DB.prepare('SELECT id, name, size, chunks FROM file WHERE id=?').bind(id).first();
  if (!f) return bad('文件不存在', 404);
  if (f.size > 20 * 1024 * 1024) return bad('>20MB 请用 /api/download 分块拉取', 413);
  const rs = await env.DB.prepare('SELECT idx, data FROM chunk WHERE file_id=? ORDER BY idx').bind(id).all();
  const parts = [];
  for (const r of (rs.results || [])) {
    parts.push(r.data instanceof ArrayBuffer ? new Uint8Array(r.data) : new Uint8Array(r.data));
  }
  const total = parts.reduce((a, p) => a + p.byteLength, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.byteLength; }
  const enc = encodeURIComponent(f.name).replace(/'/g, '%27');
  return new Response(out.buffer, {
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Length': String(total),
      'Content-Disposition': `attachment; filename*=UTF-8''${enc}`,
      'Content-Language': 'off',
      ...CORS,
    },
  });
}

async function gc(env) {
  const cut = Date.now() - KEEP_DAYS * 86400000;
  const old = await env.DB.prepare('SELECT id FROM file WHERE ts < ? LIMIT 200').bind(cut).all();
  const ids = (old.results || []).map((x) => x.id);
  if (ids.length) {
    const ph = ids.map(() => '?').join(',');
    await env.DB.prepare(`DELETE FROM chunk WHERE file_id IN (${ph})`).bind(...ids).run();
    await env.DB.prepare(`DELETE FROM file WHERE id IN (${ph})`).bind(...ids).run();
  }
  await env.DB.prepare('DELETE FROM msg WHERE ts < ?').bind(cut).run();
  return ok({ gc: true, removed: ids.length });
}

const API_DOC = {
  说明: '被控端 agent 与网页共用同一套接口, 全部 JSON 或裸字节, 无需 SDK/登录',
  约定: {
    room: '房间号; 不填或填 lobby 即默认大厅(所有新装的机器都进大厅, 想单独聊才建房间)',
    side: "'web'=网页端 | 'pc'=被控端 | 'ctrl'=控制端本机信息",
    after: '增量拉取用的上一条消息 id',
  },
  接口: {
    'GET /api/hello': '存活探测',
    'GET /api': '本清单',
    'POST /api/build': '{room?,relay?,ctrlUser?,ctrlHost?,ctrlPath?,pub?,authkey?,plat:win|linux} -> 现场生成部署包 zip(二进制流)',
    'POST /api/keygen': '{} -> {pub,priv,fp} 服务端生成 ed25519 密钥对, priv 是能直接给 ssh 用的 OpenSSH 私钥',
    'GET /api/devices': '被控端列表(按 主机|用户|IP 去重, 只留最新心跳那条)',
    'POST /api/forget': '{room} 删掉该房间的上报记录; {old:true} 清掉 24h 没心跳的僵尸',
    'GET /api/ctrl?token=': '取控制端本机信息(网页自动填表单用)',
    'POST /api/ctrl': '{token,info:{host,user,ip,pub,tsip},authkey?} 控制端上报本机信息(authkey 默认不上云)',
    'POST /api/send': '{room,side,kind:text|file|cmd|sys,body} 发消息',
    '下发指令(kind=cmd)': 'body 为 JSON {"cmd":"info"|"join"|"restart"|"cleanup"}, 只认白名单, 不做通用 shell',
    'POST /api/beat': '{room,side,info:{host,user,ip,ver}} 心跳+上报信息(每15s)',
    'GET /api/pull?room=&after=': '增量拉消息, 返回 {msgs,files,presence}',
    'GET /api/state?room=': '在线状态 + 消息总数',
    'POST /api/upload/begin': '{room,side,name,size} -> {fileId,chunks,chunkSize}',
    'PUT /api/upload/chunk?fileId=&i=': '裸字节分块',
    'POST /api/upload/file?room=&side=&name=': '≤8MB 一步上传',
    'GET /api/fileinfo?fileId=': '文件元信息',
    'GET /api/download?fileId=&i=': '取单个分块(裸字节)',
    'GET /api/file?fileId=': '≤20MB 整文件直出(带 Content-Disposition)',
  },
};
