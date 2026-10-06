# Windows 被控端（离线部署）

让一台 **Windows 机器**也能像 Linux 被控端那样，离线、零下载工具依赖、免密地被远程连入。

> 角色：**被控端（被连入）**。你的控制端（连出到这台 Windows）保持现状——
> 装好 Tailscale 客户端 + 用系统自带 `ssh` 即可，无需本脚本。

## 与 Linux 版的差异（重要）

Tailscale 内置 SSH **不支持 Windows 服务端**（`tailscale up --ssh` 在 Windows 会报
"SSH server not supported"）。因此 Windows 被控端改用：

- **Tailscale MSI 预置安装**（离线装服务，负责组网）
- **Windows 自带 OpenSSH Server + 公钥免密**（负责登录，同样**不需要密码**）

效果一致：控制端 `ssh 用户名@100.x.x.x` 直接进，走 Tailscale 内网，无公网暴露。

## 依赖

- Windows 10 / 11 / Server 2016+（OpenSSH Server 为系统可选功能，多数镜像自带 payload，可离线启用）
- PowerShell（系统自带）
- **无需** curl / wget / python
- 需**管理员**身份运行（安装服务、启用功能、写 `C:\ProgramData`）

## 文件

| 文件 | 作用 |
|---|---|
| `connect-windows.bat` | 入口：自动提权并以管理员运行 `.ps1` |
| `connect-windows.ps1` | 部署主逻辑（装 Tailscale / 入网 / 开 OpenSSH / 部署公钥 / 自启） |
| `clean-windows.bat` / `.ps1` | 用完一键清洗，抹除所有痕迹 |
| `extract.bat` | 解压助手（一键解 `.tar.gz`） |
| `assets/` | 预置 Tailscale MSI（见内部 README） |
| `keys/` | authkey + 控制端公钥临时存放点 |

## 快速开始

### 1. 解压
```bat
extract.bat          :: 或在资源管理器右键解压
```

### 2. 准备好密钥（authkey + 控制端公钥）

**authkey**（让被控端入网）：
- `keys/authkey.local.txt` 写一行 `TS_AUTHKEY=tskey-...`（从 Tailscale 后台生成，带 Devices 写权限）

**控制端公钥**（免密登录，必填一项）：
- 把控制端电脑上的公钥放进 `keys/`：`id_ed25519.pub`（或 `.pub.local`）
- 或在脚本第 4 步直接粘贴。若不知道去哪拿，在**你的控制端电脑**上执行：

  ```bat
  :: 控制端是 Windows
  type %USERPROFILE%\.ssh\id_ed25519.pub
  :: 若提示找不到文件，先生成（一路回车）
  ssh-keygen -t ed25519 -N "" -f %USERPROFILE%\.ssh\id_ed25519
  ```
  ```bash
  # 控制端是 Linux / macOS / WSL
  cat ~/.ssh/id_ed25519.pub
  # 若没有：ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
  ```
  复制输出的 `ssh-ed25519 AAAA...` 整行即可（脚本运行时也会打印这段指示）。

- **控制端完全没密钥也能用**：脚本第 4 步若检测不到任何公钥，会问你是否"在本机生成一对新密钥"。选 `y` 后它会在
  `windows/keys/id_ed25519` 生成 ed25519 密钥对、自动装好公钥，并打印**把私钥拷到控制端**的步骤与带 `-i` 的连接命令
  （`clean-windows.bat` 会一并清掉这份被控端上的私钥）。

### 3. 部署（必须管理员）
右键 `connect-windows.bat` → **以管理员身份运行**，按提示走完 5 步：
1. 安装 Tailscale（离线 MSI）
2. 入网（authkey）
3. 启用 OpenSSH Server
4. 部署控制端公钥（免密）
5. 打印本机 Tailscale IP

### 4. 控制端连接
```bash
ssh <被控端Windows用户名>@<被控端Tailscale IP>
# 例: ssh Administrator@100.92.17.69
```

### 5. 用完清洗（完全不留痕）
右键 `clean-windows.bat` → 以管理员身份运行。

## 开机自启
Tailscale 服务与 `sshd` 在部署时已设为 `Automatic`，重启自动重连 tailnet，无需重粘贴 authkey
（前提是 Tailscale 后台把本机 **Key expiry 设为 Disable**，否则过期后需重跑一次）。

## 安全边界（同 Linux 版）
- 本机痕迹（服务 / 功能 / 公钥 / 状态目录）可彻底清除。
- **服务器侧设备列表**仍会显示本机，需联网后 `clean-windows.bat` 的 logout 让设备转 offline，
  再到 https://login.tailscale.com/admin/machines 删除节点才算真正消失。
- 仓库不含真实 authkey / 公钥 / MSI（均 `.gitignore`）。
