/**
 * 全站门禁 —— 控制台只给「本人」用
 *
 * 三条通道，任一通过即放行：
 *   1) 授权码：输对了给一个长期 Cookie（默认 180 天）
 *   2) 授权 IP：登录时可勾「记住这台 IP」，写入 D1，30 天有效
 *   3) 设备令牌 ptok：每个部署包现场生成一个，烘焙进包里。
 *      ★ 这条是给被控端 agent 用的 —— 没有它，门禁会把对方的心跳/消息
 *        一起挡掉，整个链路直接瘫痪。ptok 只能访问 /api/*，不能进页面。
 *
 * 授权码不存明文，只存 SHA-256（源码里看不到原始码）。
 */

const CODE_HASH = 'e85b9215ce76e739f1781abb2fe9f2175887bc8424f397224404d7b4ed60e99d';
const IP_TTL = 30 * 86400000;        // 记住 IP 30 天
const COOKIE_DAYS = 180;

async function sha256hex(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function cookie(req, name) {
  const raw = req.headers.get('Cookie') || '';
  for (const part of raw.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) return decodeURIComponent(part.slice(i + 1).trim());
  }
  return '';
}

let ACL_READY = false;
async function aclTable(env) {
  if (ACL_READY || !env.DB) return;
  try {
    await env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS acl(
         kind TEXT NOT NULL, val TEXT NOT NULL, ts INTEGER NOT NULL,
         PRIMARY KEY(kind, val))`
    ).run();
    ACL_READY = true;
  } catch (e) { /* 并发建表时忽略 */ }
}

async function aclHas(env, kind, val) {
  if (!env.DB || !val) return false;
  const r = await env.DB.prepare('SELECT ts FROM acl WHERE kind=? AND val=?').bind(kind, val).first();
  if (!r) return false;
  if (kind === 'ip' && Date.now() - r.ts > IP_TTL) {
    await env.DB.prepare('DELETE FROM acl WHERE kind=? AND val=?').bind(kind, val).run();
    return false;
  }
  return true;
}

async function validCode(env, code) {
  if (!code) return false;
  const h = await sha256hex(code);
  if (h === CODE_HASH) return true;
  return aclHas(env, 'code', h);        // 允许在控制台里换码，换完存这里
}

function loginPage(ip, wrong) {
  const w = wrong ? '<div class="err">授权码不对，再试一次</div>' : '';
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>远程控制台 · 需要授权</title>
<style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:linear-gradient(135deg,#1e3a8a,#2563eb 55%,#4f8df7);
 font:15px/1.6 "Microsoft YaHei",system-ui,sans-serif;color:#0b1220}
.box{background:#fff;border-radius:20px;padding:32px 30px;width:min(420px,92vw);
 box-shadow:0 20px 50px rgba(15,23,42,.25)}
h1{margin:0 0 6px;font-size:21px}
p{margin:0 0 18px;color:#64748b;font-size:13px}
input{width:100%;border:2px solid #e3e9f4;border-radius:12px;padding:13px 14px;
 font-size:16px;outline:none;box-sizing:border-box}
input:focus{border-color:#2563eb}
label{display:flex;gap:8px;align-items:flex-start;margin:14px 0 18px;font-size:13px;color:#475569}
button{width:100%;background:#2563eb;color:#fff;border:none;border-radius:13px;
 padding:14px;font-size:16px;font-weight:700;cursor:pointer}
button:hover{background:#1d4ed8}
.err{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;border-radius:10px;
 padding:10px 12px;font-size:13px;margin-bottom:14px}
.ip{font-size:12px;color:#94a3b8;margin-top:14px;text-align:center}
</style></head><body><div class="box">
<h1>远程控制台</h1>
<p>这个控制台只给本人使用。请输入授权码。</p>
${w}
<form method="POST" action="/">
<input name="code" type="password" placeholder="授权码" autofocus autocomplete="off">
<label><input type="checkbox" name="remember_ip" value="1" style="width:auto;margin-top:3px">
<span>记住这台设备的 IP（30 天内不用再输）</span></label>
<button type="submit">进入</button>
</form>
<div class="ip">你的出口 IP：${ip}</div>
</div></body></html>`;
}

const H = { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' };

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const ip = request.headers.get('CF-Connecting-IP') || '';

  /* ---- 登录表单提交 ---- */
  if (request.method === 'POST' && url.pathname === '/' && url.searchParams.get('code') === null) {
    try {
      const form = await request.formData();
      const code = String(form.get('code') || '');
      const remember = String(form.get('remember_ip') || '') === '1';
      if (!(await validCode(env, code))) {
        return new Response(loginPage(ip, true), { status: 200, headers: H });
      }
      await aclTable(env);
      if (remember && ip && env.DB) {
        await env.DB.prepare(
          'INSERT OR REPLACE INTO acl(kind, val, ts) VALUES(?,?,?)'
        ).bind('ip', ip, Date.now()).run();
      }
      const clean = url.pathname + (url.search || '');
      return new Response(null, {
        status: 302,
        headers: {
          Location: clean,
          'Set-Cookie': `tsr_auth=${await sha256hex(code)}; Path=/; Max-Age=${COOKIE_DAYS * 86400}; HttpOnly; SameSite=Lax; Secure`,
          'Cache-Control': 'no-store',
        },
      });
    } catch (e) { /* 不是表单就往下走 */ }
  }

  /* ---- 通道 1：授权码（query / header），对了直接下发 Cookie ---- */
  const qCode = url.searchParams.get('code') || request.headers.get('X-Tsr-Code') || '';
  if (qCode) {
    if (await validCode(env, qCode)) {
      const u = new URL(request.url);
      u.searchParams.delete('code');
      return new Response(null, {
        status: 302,
        headers: {
          Location: u.pathname + (u.search || ''),
          'Set-Cookie': `tsr_auth=${await sha256hex(qCode)}; Path=/; Max-Age=${COOKIE_DAYS * 86400}; HttpOnly; SameSite=Lax; Secure`,
          'Cache-Control': 'no-store',
        },
      });
    }
    return new Response(loginPage(ip, true), { status: 403, headers: H });
  }

  /* ---- 通道 2：Cookie ---- */
  const ck = cookie(request, 'tsr_auth');
  if (ck && (ck === CODE_HASH || (await aclHas(env, 'code', ck)))) {
    return context.next();
  }

  /* ---- 通道 3：授权 IP ---- */
  await aclTable(env);
  if (ip && (await aclHas(env, 'ip', ip))) return context.next();

  /* ---- 通道 4：令牌 ----
     btok = 你自己本机的桥接/上报通道，可以看页面、也能下脚本
     tok  = 部署包里的设备令牌，只能调 /api/*（agent 心跳/消息），进不了控制台页面 */
  const tok = url.searchParams.get('t') || request.headers.get('X-Tsr-Token') || '';
  if (tok) {
    if (await aclHas(env, 'btok', tok)) return context.next();
    if (url.pathname.startsWith('/api/') && (await aclHas(env, 'tok', tok))) return context.next();
  }
  if (url.pathname.startsWith('/api/')) {
    return new Response(JSON.stringify({ ok: false, error: 'unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
    });
  }

  /* ---- 其余一律登录页 ---- */
  return new Response(loginPage(ip, false), { status: 200, headers: H });
}
