# assets/ —— 预置二进制 (不入库)

这里放 **Tailscale Windows 安装器**，离线部署时被 `connect-windows.ps1` 调用。

- 当前预置：`tailscale-setup-1.102.4-amd64.msi`（约 37MB）
- 获取方式：
  1. **直接用本仓库 Release 的 `tailscale-all-platforms-full.tar.gz`**（已含此 MSI）；
  2. 或自行下载：`https://pkgs.tailscale.com/stable/tailscale-setup-1.102.4-amd64.msi`
  3. 或改架构（arm64 等）：下载对应 MSI 并同步修改 `connect-windows.ps1` 里的 `$MSI` 文件名。

> 该 MSI 已被 `.gitignore` 忽略，**不提交到 GitHub**（体积大 + 版本易过时）。
> 仓库代码通过 Release 完整包或上述链接获取，保持 git 仓库轻量、纯文本。
