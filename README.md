# tailscale-remote

纯 Tailscale 离线远程连接工具包 —— 面向**「目标机没有 curl / wget / python3，无法联网下载」**的机器，
把依赖砍到极致：**零额外下载、免密连接、用完一键不留痕**。

覆盖两类被控端（被连入的机器）：

| 被控端 | 方案 | 登录方式 |
|---|---|---|
| **Linux** | Tailscale 静态二进制（预置）+ 用户态 tailscaled | Tailscale 内置 SSH（端口 22，免密、无需 root / sshd） |
| **Windows** | Tailscale MSI（预置）+ 系统自带 OpenSSH Server | 公钥免密（Tailscale SSH 不支持 Windows 服务端，故改用 OpenSSH） |

> 控制端（你自己的电脑，连出到上述被控端）只需装好 Tailscale 客户端 + 系统自带 `ssh`，**无需本工具包**。

---

## 目录结构（已按平台清晰拆分）

```
tailscale-remote/
├── README.md                  # 本文件：总览 + 安全说明
├── 我的连接信息.example.txt    # 连接信息脱敏模板（真实文件不入库）
│
├── linux/                     # ★ Linux 被控端（离线零下载）
│   ├── connect-offline.sh     #   离线部署主脚本（核心）
│   ├── clean.sh               #   用完清洗 —— 完全不留痕
│   ├── stage-tailscale.sh     #   预置助手：在有网机器把二进制打进 assets/
│   ├── extract.sh             #   解压助手（一键解包 + 修复执行位）
│   ├── 使用说明(离线版).txt    #   详细中文使用说明
│   ├── assets/                #   预置 tailscale / tailscaled（见 README，不入库）
│   └── keys/                  #   ★ authkey 临时存放点（authkey.local.txt 不入库）
│
└── windows/                   # ★ Windows 被控端（离线部署）
    ├── connect-windows.bat    #   入口：自动提权并以管理员运行 .ps1
    ├── connect-windows.ps1    #   部署主逻辑（装 Tailscale / 入网 / 开 OpenSSH / 部署公钥 / 自启）
    ├── clean-windows.bat      #   清洗入口（管理员）
    ├── clean-windows.ps1      #   清洗逻辑
    ├── extract.bat            #   解压助手（解 .tar.gz）
    ├── README.md              #   Windows 详细说明
    ├── assets/                #   预置 Tailscale MSI（见 README，不入库）
    └── keys/                  #   authkey + 控制端公钥临时存放点
```

> 两个平台完全平级、互不嵌套。`linux/` 里只放 Linux 相关文件，`windows/` 里只放 Windows 相关文件，
> 顶部只放总览文档与共享模板，结构一目了然。

---

## 快速开始

### 0. 拿到带二进制的离线包

二进制约 75MB，不进 git。二选一：

- **A. 下载开箱即用完整包（推荐）**：到本仓库 **Releases** 下载 `tailscale-remote-full.tar.gz`
  （已同时包含 Linux 二进制与 Windows MSI）。
- **B. 自己预置**：在有网机器上 `cd linux && bash stage-tailscale.sh amd64`，再把整个目录打包传过去
  （Windows 的 MSI 也需单独放入 `windows/assets/`）。

### 1. 解压

```bash
# Linux / macOS / WSL 终端，在包所在目录执行：
tar xzf tailscale-remote-full.tar.gz
cd tailscale-remote
```

Windows 上可用系统自带 `tar -xzf tailscale-remote-full.tar.gz`，或 7-Zip 右键解压。

### 2. Linux 被控端

```bash
cd tailscale-remote/linux
bash connect-offline.sh          # 回车默认选 [2] 被控端
```

详见 [`linux/使用说明(离线版).txt`](linux/使用说明(离线版).txt) 与 [`linux/README.md`](linux/README.md)。

### 3. Windows 被控端

```bat
cd tailscale-remote\windows
:: 右键 connect-windows.bat → 以管理员身份运行
```

详见 [`windows/README.md`](windows/README.md)。

### 4. 用完清洗（完全不留痕）

```bash
# Linux
cd tailscale-remote/linux && bash clean.sh        # 或 bash clean.sh -y 非交互
# Windows：右键 clean-windows.bat → 以管理员身份运行
```

---

## 安全说明（公开仓库红线）

本仓库**不包含任何真实凭证**：

- `我的连接信息.txt`（含真实 IP / 主机名 / 账号）→ 已 `.gitignore`，仓库只放 `.example` 模板。
- `linux/keys/authkey.local.txt`（真实 key）→ 已 `.gitignore`，本地可用、远程不传。
- `linux/assets/` 二进制、`windows/assets/*.msi` → 已 `.gitignore`，通过 Release 或 `stage-tailscale.sh` 获取。

⚠ **服务器侧仍需手动清理**：Tailscale 账号「设备列表」里这台机器仍会显示。彻底消失需两步（要联网）：
① 重跑脚本授权后 `clean.sh` / `clean-windows.ps1` 的 logout 让设备转 offline；
② 到 https://login.tailscale.com/admin/machines 删除该节点。

---

## 适用边界

- **Linux 被控端**：无需 root、无需 sshd、无需传统 OpenSSH 组件（用 Tailscale 内置 SSH）。
- **Windows 被控端**：需管理员权限；依赖系统自带 OpenSSH Server 可选功能（多数镜像可离线启用）。
- 你自己的控制端电脑（连出到上述被控端）只需装 Tailscale 客户端 + 系统自带 `ssh`，无需本仓库脚本。
- 目标机需能联网到 Tailscale 控制面（用于 authkey 授权与建立隧道）；二进制本身无需下载。
- Linux 用户态模式不修改系统网络栈，本机清洗能做到真正"零残留"；Windows 端卸载服务/功能/公钥后同样干净。
