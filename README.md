# tailscale-linux

纯 Tailscale 离线远程连接方案 —— 面向**「目标机没有 curl / wget / python3，无法联网下载」**的 Linux / Windows 机器，
把依赖砍到极致：**零额外下载、免密连接、用完一键不留痕**。

> 适用于：临时借用 / 机房裸机 / 受限网络环境下的远程接入。
> - **Linux 被控端**：Tailscale SSH 内置服务端，免密、无需 root、无需 sshd。
> - **Windows 被控端**：Tailscale 组网 + 系统自带 OpenSSH Server + 公钥免密（Tailscale SSH 不支持 Windows 服务端）。

---

## 核心特性

| 特性 | 说明 |
|---|---|
| **离线零下载** | Tailscale 静态二进制预置（或走 Release 完整包），运行期完全不碰 curl / wget / python3 / tar |
| **免密通道** | 用 authkey 入网 + Tailscale SSH（tailscaled 内置服务端，端口 22，tailnet 身份认证） |
| **无 sudo** | 固定用户态 `~/.tailscale`，不动系统 `/etc`、路由、iptables、tun 设备 |
| **一键清洗** | `clean.sh` 抹除本机所有连接痕迹（进程 / 状态目录 / crontab / known_hosts） |
| **最小依赖** | 被控端运行期只需 `bash + 常见 coreutils + assets/ 预置二进制` |

---

## 目录结构

```
tailscale-linux/
├── connect-offline.sh     # 离线部署主脚本 (Linux 被控端零额外依赖)  ★核心
├── clean.sh               # Linux 用完清洗 —— 完全不留痕
├── stage-tailscale.sh     # 预置助手: 在有网机器把二进制打进 assets/
├── extract.sh             # Linux 解压助手: 一键解包 + 修复权限
├── assets/                # 存放预置的 tailscale / tailscaled (见内部 README, 不入库)
├── keys/                  # ★ authkey 临时存放点 (authkey.local.txt 不入库)
│   └── authkey.local.txt  #   本地占位, 放真实 key, 自动被脚本读取, 不提交 GitHub
├── windows/               # ★ Windows 被控端 (离线部署, 详见 windows/README.md)
│   ├── connect-windows.bat/.ps1  # 部署入口(自动提权) + 主逻辑
│   ├── clean-windows.bat/.ps1    # 清洗
│   ├── extract.bat               # 解压助手
│   ├── assets/                  # 预置 Tailscale MSI (见内部 README, 不入库)
│   └── keys/                    # authkey + 控制端公钥临时存放
├── 我的连接信息.example.txt  # 连接信息脱敏模板
├── 使用说明(离线版).txt       # 详细中文使用说明 (Linux)
└── README.md              # 本文件
```

---

## 快速开始

### 0. 拿到带二进制的离线包

二选一（二进制约 75MB，不进 git）：

- **A. 下载开箱即用完整包**（推荐）：到本仓库 **Releases** 下载 `tailscale-linux-offline-full.tar.gz`。
- **B. 自己预置**：在有网机器上 `bash stage-tailscale.sh amd64`，再把整个目录打包传过去。

### 1. 解压（用自带解压助手，省心不踩层）

把包传到目标机（或你自己的机器）后，在包所在目录执行：

```bash
bash extract.sh                 # 自动找最新的 .tar.gz 并解压 + 赋权
# 或指定包:  bash extract.sh 远程连接工具包-离线版.tar.gz
```

### 2. 被控端（目标机）运行

```bash
cd tailscale-linux          # 或解压出的目录
bash connect-offline.sh     # 回车默认选 [2] 被控端
```

脚本会：① 从 `assets/` 复制二进制到 `~/.tailscale`（零下载）→ ② 启动用户态 tailscaled →
③ 提示粘贴/自动读取 auth key → ④ 打印本机 Tailscale IP。

> 想免粘贴？把 key 写进 `keys/authkey.local.txt` 的 `TS_AUTHKEY=` 行，脚本自动授权。

### 3. 主控端（你自己的电脑）连接

在自己电脑终端直接：

```bash
ssh <用户名>@<目标机Tailscale IP>        # 走 Tailscale SSH, 免密码
# 或:  tailscale ssh <用户名>@<目标机IP>
```

### 4. 用完清洗（完全不留痕）

```bash
bash clean.sh          # 交互式, 每步确认
bash clean.sh -y       # 非交互, 一次性清本机全部
```

---

## 安全说明（公开仓库红线）

本仓库**不包含任何真实凭证**：

- `我的连接信息.txt`（含真实 IP / 主机名 / 账号）→ 已 `.gitignore`，仓库只放 `.example` 模板。
- `keys/authkey.local.txt`（真实 key）→ 已 `.gitignore`，本地可用、远程不传。
- `assets/` 二进制 → 已 `.gitignore`，通过 Release 或 `stage-tailscale.sh` 获取。

⚠ **服务器侧仍需手动清理**：Tailscale 账号「设备列表」里这台机器仍会显示。彻底消失需两步
（要联网）：① 重跑脚本授权后 `clean.sh` 的 logout 让设备转 offline；② 到
https://login.tailscale.com/admin/machines 删除该节点。

---

## Windows 被控端（新增）

Windows 机器作为被控端（被连入）的离线部署方案见 **[`windows/README.md`](windows/README.md)**。

要点：Tailscale SSH 不支持 Windows 服务端，因此改用 **Tailscale 组网 + 系统自带 OpenSSH Server + 公钥免密**，
效果与 Linux 版一致（控制端 `ssh 用户@100.x.x.x` 直连，免密码、无公网暴露）。
预置 `tailscale-setup-*.msi` 离线安装，运行期不依赖任何下载工具。

## 适用边界

- **Linux 被控端**：无需 root、无需 sshd、无需传统 OpenSSH 组件（用 Tailscale 内置 SSH）。
- **Windows 被控端**：需管理员权限；依赖系统自带 OpenSSH Server 可选功能（多数镜像可离线启用）。
- 你自己的控制端电脑（连出到上述被控端）只需装 Tailscale 客户端 + 系统自带 `ssh`，无需本仓库脚本。
- 目标机需能联网到 Tailscale 控制面（用于 authkey 授权与建立隧道）；二进制本身无需下载。
- Linux 用户态模式不修改系统网络栈，本机清洗能做到真正"零残留"；Windows 端卸载服务/功能/公钥后同样干净。

---

详见 [`使用说明(离线版).txt`](使用说明(离线版).txt)（Linux）与 [`windows/README.md`](windows/README.md)（Windows）。
