# tailscale-remote

纯 Tailscale 远程连接部署包 —— 面向**「目标机没有 curl / wget / python3，无法联网下载」**的机器，
把依赖砍到极致：**零额外下载、免密连接、用完一键不留痕**。

覆盖两类被控端（被连入的机器）：

| 被控端 | 方案 | 登录方式 |
|---|---|---|
| **Linux** | Tailscale 静态二进制（预置）+ 用户态 tailscaled | Tailscale 内置 SSH（端口 22，免密、无需 root / sshd） |
| **Windows** | Tailscale MSI（预置）+ 系统自带 OpenSSH Server | 公钥免密（Tailscale SSH 不支持 Windows 服务端，故改用 OpenSSH） |

> 控制端（你自己的电脑，连出到上述被控端）只需装好 Tailscale 客户端 + 系统自带 `ssh`，**无需本工具包**。

---

## 最快路径：双击 `builder/★运行生成器.bat`

不想手动拼包、填 key、选模式？跑生成器，四步点完就出包：

```
① 目标环境   Linux / Windows / 双端
② 运行模式   离线（自带二进制，67MB）/ 轻量（不带，几十KB）
③ 密钥       贴 authkey；点一下自动生成 SSH 密钥对
④ 打包选项   格式 / 输出目录 / 便携 7-Zip / 要不要带传文件工具
```

> 所有选择都会被**记住**（存到 `~/.tailscale_remote/generator_config.json`），
> 下次打开自动恢复；authkey 需勾「记住」才会明文落盘。
> ④ 区底部有**实时摘要**，点生成前能核对平台/模式/密钥是否选对。

生成的包**顶层只有 4 个要碰的东西**，其余全部收进 `程序/`：

```
tailscale-remote-<平台>-<离线|轻量>-<日期>/
├── deploy.sh / deploy.bat    ★ 部署 —— 对方只需要跑这个
├── clean.sh  / clean.bat     ★ 清洗 —— 用完只需要跑这个
├── README.md                 说明（写给拿到包的人看）
├── 使用说明.txt               三步极简说明
└── 程序/                     实现细节，不用打开
    ├── linux/                脚本 + 预置二进制 + 你的 authkey
    └── windows/              脚本 + 预置 MSI   + 你的 authkey / 公钥
```

对方拿到包后：解压 → 跑 `deploy` → 记下屏幕打印的 `100.x.x.x` → 用完跑 `clean`。就这些。

---

## 仓库结构（本仓库 = 生成器的素材源）

```
tailscale-remote/
├── README.md                  本文件
├── 我的连接信息.example.txt    连接信息脱敏模板（真实文件不入库）
│
├── linux/                     ★ Linux 被控端
│   ├── connect-offline.sh     #   部署主脚本（离线优先，缺二进制则联网自取）
│   ├── clean.sh               #   用完清洗 —— 完全不留痕
│   ├── stage-tailscale.sh     #   预置助手：在有网机器把二进制打进 assets/
│   ├── extract.sh             #   解压助手（一键解包 + 修复执行位）
│   ├── 使用说明(离线版).txt    #   详细中文使用说明
│   ├── assets/                #   预置 tailscale / tailscaled（不入库）
│   └── keys/                  #   authkey 临时存放点（不入库）
│
├── windows/                   ★ Windows 被控端
│   ├── connect-windows.bat    #   入口：自动提权并以管理员运行 .ps1
│   ├── connect-windows.ps1    #   部署主逻辑（装 Tailscale / 入网 / 开 OpenSSH / 部署公钥 / 自启）
│   ├── clean-windows.bat      #   清洗入口（管理员）
│   ├── clean-windows.ps1      #   清洗逻辑
│   ├── extract.bat            #   解压助手（解 .tar.gz）
│   ├── README.md              #   Windows 详细说明
│   ├── assets/                #   预置 Tailscale MSI（不入库）
│   └── keys/                  #   authkey + 控制端公钥临时存放点
│
├── tools/                     ★ 点对点文件传输（双向）
│   ├── p2p.py                 #   核心 CLI: serve / send / fetch
│   ├── dragdrop-send.bat      #   Windows 拖拽发送入口
│   ├── receive-files.bat      #   Windows 一键接收入口
│   ├── 收文件.sh              #   Linux / macOS 一键接收
│   └── README.md              #   传文件详细说明
│
└── builder/                   ★ 配置生成器
    ├── ★运行生成器.bat         #   双击启动（醒目入口）
    ├── build_gui.py           #   图形界面主程序
    ├── find_python.py         #   帮 bat 找带 PyQt5 的解释器
    ├── fetch_7zip.py          #   拉取官方便携 7-Zip 二进制（可选）
    └── README.md              #   生成器详细说明
```

> 两个被控端目录完全平级、互不嵌套；生成器会把它们收进目标包的 `程序/` 下，
> 顶层只留醒目入口与文档。

---

## 不用生成器时：手动跑

### 0. 预置二进制（约 110MB，不进 git）

- **Linux**：`cd linux && bash stage-tailscale.sh amd64`（有网机器执行，会把二进制放进 `linux/assets/`）
- **Windows**：把官方 `tailscale-setup-*.msi` 放进 `windows/assets/`
- 或者到本仓库 **Releases** 下载已含二进制的完整包。

### 1. 部署

```bash
# Linux 被控端
cd linux && bash connect-offline.sh       # 回车默认选 [2] 被控端
```

```bat
:: Windows 被控端：右键 connect-windows.bat → 以管理员身份运行
```

### 2. 用完清洗

```bash
# Linux
bash clean.sh            # 交互式
bash clean.sh -y         # 非交互
```

```bat
:: Windows：右键 clean-windows.bat → 以管理员身份运行
```

详见 [`linux/README.md`](linux/README.md)、[`linux/使用说明(离线版).txt`](linux/使用说明(离线版).txt)、[`windows/README.md`](windows/README.md)。

---

## 离线 vs 轻量

| | 离线模式（默认） | 轻量模式 |
|---|---|---|
| 包体积 | ~67 MB | ~几十 KB |
| 目标机要求 | **完全不需要联网**，没 curl/wget 也行 | 需能联网，脚本自动下载官方安装包 |
| 适合 | 目标机可能断网 / 环境极简 | 目标机能上网，在意传输体积 |

两种模式下脚本逻辑一致：有预置二进制就用，没有就自动联网获取。

---

## 传文件（双向，零依赖）

建好通道后传文件**不用 scp / rsync / sftp**，只靠 python3 标准库。

```bash
# 被控端：启动接收（Windows 双击 接收文件.bat / Linux bash 收文件.sh）
python tools/p2p.py serve --gui
#   屏幕显示: 100.x.x.x 的 IP + 6 位口令, 收件在 程序/文件传输/inbox/

# 控制端：送文件
python tools/p2p.py send 100.x.x.x 报告.pdf
python tools/p2p.py send 100.x.x.x -a 整个目录      # 自动打包 zip

# 控制端：从对方取文件（反向）
python tools/p2p.py fetch 100.x.x.x -l               # 先列清单
python tools/p2p.py fetch 100.x.x.x 截图.png
```

**Windows 拖拽**：把文件/文件夹直接拖到 `dragdrop-send.bat` 图标上，按提示填 IP 和口令。

| 依赖 | 说明 |
|---|---|
| 被控端 `serve` | **python3 本体即可**（socket/threading/zipfile 全内置） |
| `--gui` 弹窗 | +tkinter，**纯可选**；精简 Linux 缺了自动降级命令行，收文件不受影响 |

安全：默认 6 位口令校验、路径越界拒绝、文件名净化、`.part` 临时文件原子改名、同名自动加序号。
详见 [tools/README.md](tools/README.md)。

---

## 安全说明（公开仓库红线）

本仓库**不包含任何真实凭证**：

- `我的连接信息.txt`（含真实 IP / 主机名 / 账号）→ 已 `.gitignore`，仓库只放 `.example` 模板。
- `*/keys/authkey.local.txt`（真实 key）→ 已 `.gitignore`，本地可用、远程不传。
- `linux/assets/` 二进制、`windows/assets/*.msi`、便携 7-Zip → 已 `.gitignore`，通过 Release 或脚本获取。
- 生成的部署包内含**明文 authkey**，只发给可信目标；对方跑 clean 会清除。

⚠ **服务器侧仍需手动清理**：Tailscale 账号「设备列表」里这台机器仍会显示。彻底消失需两步（要联网）：
① 重跑脚本授权后 `clean` 的 logout 让设备转 offline；
② 到 https://login.tailscale.com/admin/machines 删除该节点。

---

## 适用边界

- **Linux 被控端**：无需 root、无需 sshd、无需传统 OpenSSH 组件（用 Tailscale 内置 SSH）。
- **Windows 被控端**：需管理员权限；依赖系统自带 OpenSSH Server 可选功能（多数镜像可离线启用）。
- 你自己的控制端电脑（连出到上述被控端）只需装 Tailscale 客户端 + 系统自带 `ssh`，无需本仓库脚本。
- 目标机需能联网到 Tailscale 控制面（用于 authkey 授权与建立隧道）；**二进制本身**在离线模式下无需下载。
- Linux 用户态模式不修改系统网络栈，本机清洗能做到真正"零残留"；Windows 端卸载服务/功能/公钥后同样干净。
