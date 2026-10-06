# assets/ —— Tailscale 静态二进制存放处

本目录应存放两个预置静态二进制（约 75MB）：

- `tailscale`   （约 33MB，客户端 CLI）
- `tailscaled`  （约 42MB，守护进程）

**这两个文件不入库**（已在 `.gitignore`），获取方式二选一：

## 方式 A：下载开箱即用完整包（推荐）
到本仓库的 **Releases** 页面下载 `tailscale-linux-offline-full.tar.gz`，
它已包含预置好的二进制，解压即用，无需联网。

## 方式 B：自己预置（需一台有网的机器）
在本目录执行：

    bash stage-tailscale.sh amd64      # x86_64 / amd64
    # 其他架构: bash stage-tailscale.sh arm64

脚本会从官方源下载静态二进制并放到 `assets/`，然后你再把整个
目录重新打包传过去即可。

> 为什么不直接把二进制提交到 git？体积大、且 GitHub 不适合托管频繁更新的
> 二进制。用 Release 附件 + stage-tailscale.sh 更干净。
