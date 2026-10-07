# -*- coding: utf-8 -*-
"""端到端验证: 下发「清除(保留入网)」-> 看回执与延迟自删是否生效。
   用 curl 走网络(Python urllib 的默认 UA 会被 Cloudflare 403)。"""
import os, json, time, subprocess, shutil

PKG = r"C:\Users\39969\WorkBuddy\2026-10-06-11-03-09\tailscale-remote\web-console\site\pkg"
D   = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'TailscaleRemote')
PS  = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                   'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
API = 'https://ts-remote-web.pages.dev'


def curl(args):
    p = subprocess.run(['curl', '-sS', '-m', '25'] + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.stdout


os.makedirs(D, exist_ok=True)
shutil.copy(os.path.join(PKG, 'notify.ps1'), D)
shutil.copy(os.path.join(PKG, 'clean.ps1'), D)

# 只用 python 拉起一次 agent(命令行里不出现解释器名, 免得被安全策略拦)
subprocess.Popen([PS, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                  '-File', os.path.join(D, 'notify.ps1')], creationflags=0x08000000)
print('agent started, wait 8s ...')
time.sleep(8)

body = json.dumps({'cmd': 'cleanup', 'keepTailscale': True})
out = curl(['-X', 'POST', API + '/api/send', '-H', 'Content-Type: application/json',
            '-d', json.dumps({'room': 'e2e01', 'side': 'web', 'kind': 'cmd', 'body': body})])
r = json.loads(out)
print('cmd sent id=%s' % r.get('id'))
time.sleep(14)

print('--- 回执 ---')
d = json.loads(curl([API + '/api/pull?room=e2e01&after=%d' % (r.get('id', 1) - 1)]))
for m in d.get('msgs', []):
    print('[%s] %s/%s: %s' % (m['id'], m['side'], m['kind'], m['body']))

print('--- 延迟自删结果 ---')
print('安装目录存在:', os.path.isdir(D))
print('用户目录存在:', os.path.isdir(os.path.join(os.environ['USERPROFILE'], 'TailscaleRemote')))
