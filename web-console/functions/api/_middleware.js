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
  const room = String(b.room || '').trim();
  if (!room) return bad('缺少房间号 room');
  const relay    = String(b.relay || 'https://ts-remote-web.pages.dev').replace(/\/+$/, '');
  const ctrlUser = String(b.ctrlUser || '').trim();
  const ctrlHost = String(b.ctrlHost || '').trim();
  const ctrlPath = String(b.ctrlPath || 'Desktop').trim() || 'Desktop';
  const pub      = String(b.pub || '').trim();
  const authkey  = String(b.authkey || '').trim();
  const plat     = (String(b.plat || 'win') === 'linux') ? 'linux' : 'win';

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
         .split('__CTRL_PATH__').join(ctrlPath);
    push('install.sh', s, false);
    push('notify.sh', stripBom(dec.decode(await grabAsset(context, 'notify.sh'))), false);
    push('clean.sh', stripBom(dec.decode(await grabAsset(context, 'clean.sh'))), false);
  } else {
    let s = stripBom(dec.decode(await grabAsset(context, 'install.ps1')));
    s = s.split('__ROOM__').join(room).split('__RELAY__').join(relay)
         .split('__CTRL_USER__').join(ctrlUser).split('__CTRL_HOST__').join(ctrlHost)
         .split('__CTRL_PATH__').join(ctrlPath);
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

/* ---------------------- 已注册设备(供网页远程清除) ---------------------- */
async function devices(env) {
  const rows = await env.DB.prepare(
    `SELECT room, side, ts, info FROM presence WHERE side='pc' ORDER BY ts DESC LIMIT 200`
  ).all();
  const list = [];
  for (const r of (rows.results || [])) {
    let info = {};
    try { info = r.info ? JSON.parse(r.info) : {}; } catch (e) { info = {}; }
    list.push({
      room: r.room, ts: r.ts, online: Date.now() - r.ts < 90000,
      host: info.host || '', user: info.user || '', ip: info.ip || '',
      path: info.path || '', ver: info.ver || '',
    });
  }
  return ok({ devices: list, now: Date.now() });
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
    if (p === '/api/devices') return devices(env);
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
  约定: { room: '房间号(配对凭证)', side: "'web'=网页端 | 'pc'=被控端", after: '增量拉取用的上一条消息 id' },
  接口: {
    'GET /api/hello': '存活探测',
    'GET /api': '本清单',
    'POST /api/build': '{room,relay?,ctrlUser?,ctrlHost?,ctrlPath?,pub?,authkey?,plat:win|linux} -> 现场生成部署包 zip(二进制流)',
    'GET /api/devices': '最近上报过的被控端列表(供网页远程清除)',
    'POST /api/send': '{room,side,kind:text|file|cmd|sys,body} 发消息',
    '下发指令(kind=cmd)': 'body 为 JSON {"cmd":"info"|"restart"|"cleanup"}, 只认白名单, 不做通用 shell',
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
