# -*- coding: utf-8 -*-
"""验证单例保护: 连开 3 个 agent, 下发一条 info, 只应有 1 条回执。"""
import os, json, time, subprocess, shutil

PKG  = r"C:\Users\39969\WorkBuddy\2026-10-06-11-03-09\tailscale-remote\web-console\site\pkg"
INST = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'TailscaleRemote')
PS   = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                    'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
API  = 'https://ts-remote-web.pages.dev'
ROOM = 'e2e03'

shutil.rmtree(INST, ignore_errors=True)
os.makedirs(INST, exist_ok=True)
for f in ('notify.ps1', 'clean.ps1', 'install.ps1'):
    shutil.copy(os.path.join(PKG, f), INST)
with open(os.path.join(INST, 'agent.ini'), 'w', encoding='utf-8') as fh:
    fh.write('room=%s\nrelay=%s\ninstalldir=%s\nhost=MYLAPTOP\nuser=39969\n' % (ROOM, API, INST))


def curl(args):
    p = subprocess.run(['curl', '-sS', '-m', '30'] + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.stdout


def jcurl(args):
    try:
        return json.loads(curl(args))
    except Exception:
        return {}


print('[1] 连开 3 个 agent ...')
for _ in range(3):
    subprocess.Popen([PS, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                      '-File', os.path.join(INST, 'notify.ps1')], creationflags=0x08000000)
    time.sleep(1)
time.sleep(9)

st = jcurl([API + '/api/state?room=' + ROOM]).get('presence', {}).get('pc', {})
print('    在线:', st.get('online'), ' host:', st.get('host'))

print('[2] 下发 info ...')
r = jcurl(['-X', 'POST', API + '/api/send', '-H', 'Content-Type: application/json',
           '-d', json.dumps({'room': ROOM, 'side': 'web', 'kind': 'cmd',
                             'body': json.dumps({'cmd': 'info'})})])
cid = r.get('id', 0)
time.sleep(10)
d = jcurl([API + '/api/pull?room=%s&after=%d' % (ROOM, max(cid - 1, 0))])
replies = [m for m in d.get('msgs', []) if m['side'] == 'pc']
print('    回执条数 =', len(replies), '（应为 1）')
for m in replies:
    print('    ', m['body'])

print('[3] 收尾: 下发 cleanup 让 agent 自删')
jcurl(['-X', 'POST', API + '/api/send', '-H', 'Content-Type: application/json',
       '-d', json.dumps({'room': ROOM, 'side': 'web', 'kind': 'cmd',
                         'body': json.dumps({'cmd': 'cleanup', 'keepTailscale': True})})])
time.sleep(12)
print('    安装目录存在:', os.path.isdir(INST))
