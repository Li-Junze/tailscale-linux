/**
 * ts-remote-relay —— 「Tailscale 远程工具箱 · 网页控制台」的中继服务
 * ------------------------------------------------------------------
 * 一个 Worker 搞定三件事:
 *   1) 消息通道   : 网页 <-> 被控端 的文本互发（聊天 / 通知弹窗）
 *   2) 文件通道   : 双向传文件。文件切块存进 D1（R2 未开通时的等价方案）
 *   3) 在线心跳   : 双方各 15s 打一次点，用来显示"对方在线"
 *
 * 房间号(room) 就是配对凭证: 双方填同一个 room 才能互通。
 * 设计目标: 被控端零依赖(只要 PowerShell)，网页端零构建(纯静态)。
 */

const CHUNK = 1180000;              // 每块 1.18MB —— D1 单行上限 2MB, 留足余量
const KEEP_DAYS = 7;                // 消息/文件保留天数

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
};

const json = (o, status = 200) => new Response(JSON.stringify(o), {
  status,
  headers: { 'Content-Type': 'application/json; charset=utf-8', ...CORS },
});

const bad = (msg, status = 400) => json({ ok: false, error: msg }, status);
const ok = (extra = {}) => json({ ok: true, ...extra });

/* ---------------- 建表（每个 isolate 只跑一次） ---------------- */
let SCHEMA_READY = false;
async function ensureSchema(env) {
  if (SCHEMA_READY) return;
  await env.DB.batch([
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS msg(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room TEXT NOT NULL, side TEXT NOT NULL, kind TEXT NOT NULL,
        body TEXT, ts INTEGER NOT NULL)`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS idx_msg_room ON msg(room, id)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS file(
        id TEXT PRIMARY KEY, room TEXT NOT NULL, side TEXT NOT NULL,
        name TEXT NOT NULL, size INTEGER NOT NULL, chunks INTEGER NOT NULL,
        ts INTEGER NOT NULL)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS chunk(
        file_id TEXT NOT NULL, idx INTEGER NOT NULL,
        data BLOB NOT NULL, PRIMARY KEY(file_id, idx))`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS presence(
        room TEXT NOT NULL, side TEXT NOT NULL, ts INTEGER NOT NULL,
        PRIMARY KEY(room, side))`),
  ]);
  SCHEMA_READY = true;
}

const sid = (p) => (p === 'pc' ? 'pc' : 'web');
const rid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-5);

/* ---------------- 消息 ---------------- */
async function apiSend(env, b) {
  const room = (b.room || '').trim();
  if (!room) return bad('缺少 room');
  const side = sid(b.side);
  const kind = ['text', 'file', 'sys'].includes(b.kind) ? b.kind : 'text';
  const body = typeof b.body === 'string' ? b.body.slice(0, 8000) : '';
  const ts = Date.now();
  const r = await env.DB.prepare(
    'INSERT INTO msg(room, side, kind, body, ts) VALUES(?,?,?,?,?)'
  ).bind(room, side, kind, body, ts).run();
  return ok({ id: r.meta.last_row_id, ts });
}

async function apiPull(env, url) {
  const room = (url.searchParams.get('room') || '').trim();
  if (!room) return bad('缺少 room');
  const after = parseInt(url.searchParams.get('after') || '0', 10) || 0;
  const r = await env.DB.prepare(
    'SELECT id, side, kind, body, ts FROM msg WHERE room=? AND id>? ORDER BY id LIMIT 300'
  ).bind(room, after).all();
  const p = await env.DB.prepare(
    'SELECT side, ts FROM presence WHERE room=?'
  ).bind(room).all();
  const presence = {};
  for (const row of (p.results || [])) presence[row.side] = row.ts;
  const files = await env.DB.prepare(
    'SELECT id, name, size, chunks, side, ts FROM file WHERE room=? ORDER BY ts DESC LIMIT 60'
  ).bind(room).all();
  return ok({ msgs: r.results || [], presence, files: files.results || [], now: Date.now() });
}

async function apiBeat(env, b) {
  const room = (b.room || '').trim();
  if (!room) return bad('缺少 room');
  const side = sid(b.side);
  await env.DB.prepare(
    `INSERT INTO presence(room, side, ts) VALUES(?,?,?)
     ON CONFLICT(room, side) DO UPDATE SET ts=excluded.ts`
  ).bind(room, side, Date.now()).run();
  return ok();
}

/* ---------------- 文件 ---------------- */
async function apiUploadBegin(env, b) {
  const room = (b.room || '').trim();
  const name = String(b.name || 'file.bin').slice(0, 180);
  const size = parseInt(b.size, 10) || 0;
  if (!room) return bad('缺少 room');
  if (size <= 0) return bad('文件为空');
  if (size > 300 * 1024 * 1024) return bad('单文件上限 300MB');
  const id = rid();
  const chunks = Math.ceil(size / CHUNK);
  await env.DB.prepare(
    'INSERT INTO file(id, room, side, name, size, chunks, ts) VALUES(?,?,?,?,?,?,?)'
  ).bind(id, room, sid(b.side), name, size, chunks, Date.now()).run();
  return ok({ fileId: id, chunks, chunkSize: CHUNK });
}

async function apiUploadChunk(env, url, req) {
  const fileId = url.searchParams.get('fileId') || '';
  const idx = parseInt(url.searchParams.get('i') || '0', 10) || 0;
  if (!fileId) return bad('缺少 fileId');
  const buf = await req.arrayBuffer();
  if (!buf.byteLength) return bad('空分块');
  await env.DB.prepare(
    'INSERT OR REPLACE INTO chunk(file_id, idx, data) VALUES(?,?,?)'
  ).bind(fileId, idx, buf).run();
  return ok({ i: idx, n: buf.byteLength });
}

async function apiFileInfo(env, url) {
  const fileId = url.searchParams.get('fileId') || '';
  const row = await env.DB.prepare(
    'SELECT id, name, size, chunks, side, ts FROM file WHERE id=?'
  ).bind(fileId).first();
  if (!row) return bad('文件不存在', 404);
  return ok({ file: row });
}

async function apiDownload(env, url) {
  const fileId = url.searchParams.get('fileId') || '';
  const idx = parseInt(url.searchParams.get('i') || '0', 10) || 0;
  if (!fileId) return bad('缺少 fileId');
  const row = await env.DB.prepare(
    'SELECT data FROM chunk WHERE file_id=? AND idx=?'
  ).bind(fileId, idx).first();
  if (!row) return bad('分块不存在', 404);
  const bytes = row.data instanceof ArrayBuffer
    ? row.data : new Uint8Array(row.data).buffer;
  return new Response(bytes, {
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Length': String(bytes.byteLength),
      ...CORS,
    },
  });
}

/* ---------------- 清理（顺手做，失败不影响主流程） ---------------- */
async function gc(env) {
  const cut = Date.now() - KEEP_DAYS * 86400000;
  try {
    const old = await env.DB.prepare(
      'SELECT id FROM file WHERE ts < ? LIMIT 200'
    ).bind(cut).all();
    const ids = (old.results || []).map((x) => x.id);
    if (ids.length) {
      const ph = ids.map(() => '?').join(',');
      await env.DB.prepare(`DELETE FROM chunk WHERE file_id IN (${ph})`).bind(...ids).run();
      await env.DB.prepare(`DELETE FROM file WHERE id IN (${ph})`).bind(...ids).run();
    }
    await env.DB.prepare('DELETE FROM msg WHERE ts < ?').bind(cut).run();
  } catch (e) { /* 忽略 */ }
}

/* ---------------- 路由 ---------------- */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname.replace(/\/+$/, '') || '/';

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

    try {
      if (p === '/' || p === '/api/hello') {
        return json({ ok: true, service: 'ts-remote-relay', now: Date.now(), chunk: CHUNK });
      }

      await ensureSchema(env);

      if (p === '/api/send' && request.method === 'POST') return apiSend(env, await request.json());
      if (p === '/api/beat' && request.method === 'POST') return apiBeat(env, await request.json());
      if (p === '/api/pull') return apiPull(env, url);
      if (p === '/api/upload/begin' && request.method === 'POST') {
        return apiUploadBegin(env, await request.json());
      }
      if (p === '/api/upload/chunk' && request.method === 'PUT') {
        return apiUploadChunk(env, url, request);
      }
      if (p === '/api/fileinfo') return apiFileInfo(env, url);
      if (p === '/api/download') return apiDownload(env, url);
      if (p === '/api/gc') { await gc(env); return ok({ gc: true }); }

      return bad('未知接口: ' + p, 404);
    } catch (e) {
      return bad('服务端异常: ' + (e && e.message ? e.message : String(e)), 500);
    }
  },
};
