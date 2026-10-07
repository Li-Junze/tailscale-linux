# web-console —— 网页远程控制台（Cloudflare Pages + D1）

在浏览器里把远程连接这件事一次做完：**现场生成部署包** → 对方下载两个文件 →
双击引导脚本 → 零交互装好（Windows 最多一次 UAC）→ 信息自动回传 →
网页上随时对话、传文件、一键清除。

不经过任何第三方网盘：zip 由 Cloudflare Pages Functions 用纯 JS 现场打包。

```
                ┌─────────────────────────────┐
   浏览器 ─────▶│  Cloudflare Pages Functions │◀───── 被控端 PowerShell agent
  (手机也行)    │  /api/*  +  D1 (消息/文件块) │
                └─────────────────────────────┘
```

## 线上地址

| 用途 | 地址 |
|---|---|
| 网页控制台 | https://ts-remote-web.pages.dev |
| 中继 API | https://ts-remote-web.pages.dev/api |
| D1 数据库 | `ts-remote-db`（id `c191eac8-3fd8-4023-8030-c52eca8d2338`） |

## ★ 访问控制（只有本人能用）

`functions/_middleware.js` 是全站门禁，四条通道任一通过才放行：

| 通道 | 说明 |
|---|---|
| 授权码 | 输对了下发长期 Cookie（180 天）。**只存 SHA-256，不存明文** |
| 授权 IP | 登录页勾「记住这台 IP」，写进 D1，30 天有效 |
| `btok` 令牌 | 你**本机**的桥接/上报通道，可看页面、可下载脚本 |
| `tok` 设备令牌 | **打包时现场签发、烘焙进包里**，被控端靠它发心跳。只能调 `/api/*`，进不了页面 |

★ 第三条是关键：**没有它，门禁会把被控端的心跳和消息一起挡掉，整条链路直接瘫痪。**
`POST /api/build` 每生成一个包就签一个 `tok`；桥接部署时先向 `/api/tok` 要一个再写进
`agent.ini`。所有 agent 请求自动带 `?t=<tok>`（`notify.ps1` 的 `Add-Tok`、
`notify.sh` 的 `u()`）。

未授权访问：页面请求返回登录页，`/api/*` 返回 `401 {"ok":false,"error":"unauthorized"}`。

## ★ 大厅（lobby）模式

不再一台机器一个随机房间号。**默认房间就是 `lobby`**：不指定房间的包全部汇进大厅，
网页打开即在大厅里。想单独跟某一台聊，才点「+ 单独会话」生成新房间号再打包。

设备列表按**主机名**去重合并（同一台机器在多个房间上报过只显示一条，取最新心跳），
房间号取它当前正在听的那个——下发指令要发到对的房间。

## ★ 为什么不是 Workers，而是 Pages

`*.workers.dev` 在国内被 **DNS 污染**（实测解析到 `75.126.150.210`、`2001::1f0d:5709`
这类非 Cloudflare 的 IP），被控端根本连不上；`*.pages.dev` 解析正常、可达。
Pages Functions 本身就是 Workers 运行时，能力完全一样，所以整套东西
（静态页面 + API）都放在 Pages 上——同一个域名，顺带没有跨域问题。

## 目录

| 路径 | 说明 |
|---|---|
| `site/index.html` | 控制台单页（三个页签：对话 / 生成部署包 / 设备与远程清除） |
| `functions/api/_middleware.js` | API 全部实现（含纯手写的 zip 打包器，无任何 npm 依赖） |
| `site/pkg/` | **打包源**（权威版本）。`install.*` 主部署脚本、`notify.*` 消息 agent、`clean.*` 卸载 |
| `site/pkg/launcher.bat` / `.sh` | 外层引导脚本（静态下载，配置全在 zip 里所以无需替换） |
| `site/pkg/_gen_launcher.py` | 生成引导脚本最终编码形态（bat=GBK+CRLF，sh=UTF-8+LF） |
| `worker/` | 等价的独立 Workers 版本（海外网络可用，国内不可达，留作备份） |
| `tests/` | 端到端验证脚本（全链路 / 单例 / 清除） |

> ⚠️ `site/pkg/*.ps1` 必须是 **UTF-8 + BOM**，否则 PS 5.1 按 ANSI 读、中文乱码并解析崩。
> zip 生成时会统一补 BOM，但源文件本身也带 BOM 更保险。
> ⚠️ zip 内文件名**一律 ASCII**：PS 5.1 的 `Expand-Archive` 对 UTF-8 文件名支持很差。
> 中文只出现在文件内容里（ps1=UTF-8 BOM，bat=GBK）。

> ⚠️ `wrangler pages deploy` 要求 **`functions/` 在“当前工作目录”下**，
> 不是在被部署的静态目录下。所以命令必须在 `web-console/` 里执行，
> 静态资源目录是 `site/`。

## 接口（agent 可直接调用，无需 SDK / 登录）

约定：`room` = 房间号（配对凭证）；`side` = `web`（网页）或 `pc`（被控端）；
`after` = 上一条消息 id（增量拉取用）。

| 方法 路径 | 参数 | 说明 |
|---|---|---|
| `GET /api` | — | 接口清单（自检用） |
| `GET /api/hello` | — | 存活探测 |
| `POST /api/build` | `{room?,plat:win\|linux,relay?,ctrlUser?,ctrlHost?,ctrlPath?,pub?,authkey?}` | **现场生成部署包**，直接返回 zip 二进制流。room 留空=大厅；顺带签发设备令牌 |
| `POST /api/keygen` | `{}` | 服务端生成 ed25519 密钥对 → `{pub,priv,fp}`，`priv` 是能直接给 ssh 用的 OpenSSH 私钥 |
| `POST /api/tok` | — | 签发一个设备令牌（桥接给新机器装机时用） |
| `GET/POST /api/acl` | 增/删授权 IP、登记本机令牌、换授权码 | 门禁管理 |
| `GET /api/devices` | — | 被控端列表（按主机名去重，只留最新心跳） |
| `POST /api/forget` | `{old:true}` 或 `{room}` | 清掉 24h 没心跳的僵尸 / 指定房间 |
| `GET/POST /api/ctrl` | `?token=` | 控制端本机信息上报（IP/用户名/公钥自动填表单） |
| `GET/POST /api/bridge`、`GET /api/bridge/tasks`、`POST /api/bridge/result` | 见下 | 已有 SSH 连接的桥接通道 |
| `POST /api/send` | `{room,side,kind:text\|file\|cmd\|sys,body}` | 发一条消息 |
| `POST /api/beat` | `{room,side,info:{host,user,ip,ver}}` | 心跳 + 上报机器信息（建议 15s） |
| `GET /api/pull` | `?room=&after=` | 增量拉消息 → `{msgs,files,presence}` |
| `GET /api/state` | `?room=` | 在线状态 + 消息总数 |
| `POST /api/upload/begin` | `{room,side,name,size}` | 开始分块上传 → `{fileId,chunks,chunkSize}` |
| `PUT /api/upload/chunk` | `?fileId=&i=` + 裸字节 | 上传第 i 块（块大小 1.18MB） |
| `POST /api/upload/file` | `?room=&side=&name=` + 裸字节 | ≤8MB 一步上传 |
| `GET /api/fileinfo` | `?fileId=` | 文件元信息 |
| `GET /api/download` | `?fileId=&i=` | 取第 i 块（裸字节） |
| `GET /api/file` | `?fileId=` | ≤20MB 整文件直出（带 Content-Disposition） |
| `GET /api/gc` | — | 清理 7 天前的消息与文件 |

## 远程指令（网页 → 被控端，`kind:'cmd'`）

只认白名单，**不做通用 shell**：

| body | 动作 |
|---|---|
| `{"cmd":"info"}` | 重新上报主机名 / 用户 / IP / 安装目录 / 自启状态 |
| `{"cmd":"restart"}` | agent 自我重启 |
| `{"cmd":"cleanup"}` | 卸载：移除开机任务 → 撤销公钥 → 退出 tailnet → 删除安装目录 |
| `{"cmd":"cleanup","keepTailscale":true}` | 同上，但**保留** tailnet 入网 |
| `{"cmd":"join","authkey":"tskey-…"}` | 远程补入网：打包时没填 authkey 也能事后静默拉进 tailnet，对方零交互 |

## 已有 SSH 连接（本机桥接）

浏览器自己不能 SSH，所以 `site/pkg/bridge.ps1` 跑在**你自己电脑**上：
读 `~/.ssh/config` → 并发探测哪些连得上 → 上报网页 → 网页下发
「装 agent / 探测 / 卸载」→ 它用 ssh/scp 去干 → 结果回传。

```powershell
curl -sSL https://ts-remote-web.pages.dev/pkg/bridge.ps1?t=<token> -o %TEMP%\tsr-bridge.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File %TEMP%\tsr-bridge.ps1 -Token <token>
```

这样对面哪怕从没装过本工具，也能直接远程装上（不用对方双击任何东西）。

★ 两个坑（都踩过，别改回去）：

1. **远程命令不能含 `>` `|` `&`**——Windows 的 ssh 会直接报"系统找不到指定的路径"
   且 rc=1。所以部署一律先 `scp` 上传脚本，再执行一句没有元字符的命令
   （如 `sh tsr-boot.sh`）。
2. **ssh 要用 `C:\Windows\System32\OpenSSH\ssh.exe`**。`Get-Command ssh` 常常先命中
   PortableGit 的 ssh，非交互启动时一个字都不输出，探测永远失败。

## 一键部署的完整流程

```
网页「生成部署包」 -> setup.zip（内含烘焙好的房间号/公钥/authkey）
                  -> 再下载 launcher.bat（几 KB，外层引导）
对方把这两个文件放同一目录，双击 bat：
  1. 找 setup.zip -> 用 tar.exe 解压（没有则回落 Expand-Archive）
  2. 调 install.ps1；不是管理员就 -Verb RunAs 重启自己（= 唯一一次 UAC）
  3. 装 Tailscale -> 入网 -> 开 OpenSSH -> 写公钥免密
  4. robocopy 到 %LOCALAPPDATA%\TailscaleRemote（Linux: ~/.local/share/tailscale-remote）
  5. 写 agent.ini -> 注册开机任务（Linux: crontab @reboot）-> 拉起 agent
  6. 回传回执：① 中继（网页立刻可见）② scp 到控制端指定目录（需控制端开 sshd）
  7. 自清理：删掉下载目录里的 setup.zip / launcher.bat + 解压临时目录
  8. 最后一行单独框出 ssh 命令
```

每一步都独立容错（`Step` 包一层 try/catch），**单步失败不会中断整条链**，
失败的步骤会列在末尾"未完成的步骤"里。加 `-KeepSource` 可跳过第 7 步。

## 被控端

`site/pkg/notify.ps1`（纯 PowerShell 5.1 + WinForms，零依赖）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File notify-windows.ps1 -Room 房间号
```

自检：加 `-Once` 只拉一轮就退出；加 `-TestToast "文字"` 只弹一个测试弹窗。

房间号三级回退（保证 agent 永远能自举）：`-Room` 参数 → `agent.ini` →
从 `install.ps1` 的烘焙参数里正则抠出来。

启动时先把消息游标对齐到最新 —— **否则每次重启都会把历史指令重放一遍**
（比如上次下发过的 cleanup，会让刚起来的 agent 把自己卸掉）。

进程单例靠 `Global\TailscaleRemoteAgent` 互斥量：连开多个实例只有第一个生效，
避免一条指令被重复执行 N 遍。

目录约定（自动创建，不用确认）：

- `%USERPROFILE%\TailscaleRemote\Inbox` —— 网页发来的文件自动存这里
- `%USERPROFILE%\TailscaleRemote\Outbox` —— 丢文件进去就自动传回网页（传完移到 `sent\`）

## 重新部署

```bash
cd web-console
wrangler pages deploy site --project-name=ts-remote-web --branch=main --commit-dirty=true
```

D1 绑定（每个 Pages 项目只需配一次，**配置后必须再部署一次才生效**）：

```bash
curl -X PATCH "https://api.cloudflare.com/client/v4/accounts/<账号ID>/pages/projects/ts-remote-web" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"deployment_configs":{"production":{"d1_databases":{"DB":{"id":"<库ID>"}}}}}'
```

## 已知限制

- 单文件上限 300MB（D1 分块，1.18MB/块）；>20MB 必须走分块接口下载。
- 消息与文件保留 7 天（`/api/gc` 清理）。
- 房间号就是唯一凭证：知道房间号的人都能收发，别用太好猜的。
