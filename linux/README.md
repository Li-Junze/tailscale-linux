# Linux 被控端（离线部署）

让一台 **Linux 机器**离线、零下载工具依赖、免密地被远程连入。

> 角色：**被控端（被连入）**。你的控制端（连出到这台 Linux）保持现状——装好 Tailscale 客户端 +
> 用系统自带 `ssh` / `tailscale ssh` 即可，无需本脚本。

## 核心特性

| 特性 | 说明 |
|---|---|
| **离线零下载** | Tailscale 静态二进制预置在 `assets/`，运行期完全不碰 curl / wget / python3 / tar |
| **免密通道** | authkey 入网 + Tailscale 内置 SSH（tailscaled 内置服务端，端口 22，tailnet 身份认证） |
| **无 sudo** | 固定用户态 `~/.tailscale`，不动系统 `/etc`、路由、iptables、tun 设备 |
| **一键清洗** | `clean.sh` 抹除本机所有连接痕迹（进程 / 状态目录 / crontab / known_hosts） |
| **最小依赖** | 被控端运行期只需 `bash + 常见 coreutils + assets/ 预置二进制` |

## 快速开始

### 1. 解压（用自带解压助手，省心不踩层）

```bash
bash extract.sh                 # 自动找最新的 .tar.gz 并解压 + 赋权
# 或指定包:  bash extract.sh ../tailscale-remote-full.tar.gz
```

### 2. 被控端（目标机）运行

```bash
bash connect-offline.sh     # 回车默认选 [2] 被控端
```

脚本会：① 从 `assets/` 复制二进制到 `~/.tailscale`（零下载）→ ② 启动用户态 tailscaled →
③ 提示粘贴/自动读取 auth key → ④ 打印本机 Tailscale IP。

> 想免粘贴？把 key 写进 `keys/authkey.local.txt` 的 `TS_AUTHKEY=` 行，脚本自动授权。

### 3. 主控端（你自己的电脑）连接

```bash
ssh <用户名>@<目标机Tailscale IP>        # 走 Tailscale SSH, 免密码
# 或:  tailscale ssh <用户名>@<目标机IP>
```

### 4. 用完清洗（完全不留痕）

```bash
bash clean.sh          # 交互式, 每步确认
bash clean.sh -y       # 非交互, 一次性清本机全部
```

## 文件

| 文件 | 作用 |
|---|---|
| `connect-offline.sh` | 离线部署主脚本（核心） |
| `clean.sh` | 用完清洗 —— 完全不留痕 |
| `stage-tailscale.sh` | 预置助手：在有网机器把二进制打进 `assets/` |
| `extract.sh` | 解压助手（一键解包 + 修复执行位） |
| `assets/` | 预置 `tailscale` / `tailscaled`（见内部 README，不入库） |
| `keys/` | ★ authkey 临时存放点（`authkey.local.txt` 不入库） |

## 详细说明

完整中文使用说明见同目录 [`使用说明(离线版).txt`](使用说明(离线版).txt)。
