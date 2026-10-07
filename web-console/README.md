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
| `POST /api/build` | `{room,plat:win\|linux,relay?,ctrlUser?,ctrlHost?,ctrlPath?,pub?,authkey?}` | **现场生成部署包**，直接返回 zip 二进制流 |
| `GET /api/devices` | — | 最近上报过的被控端列表（供网页远程清除） |
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
