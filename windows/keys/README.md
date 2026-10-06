# keys/ —— 临时存放点

被控端部署时从这里读取两样东西：

## 1. authkey（入网密钥）
- 文件：`authkey.local.txt`（不入库，本地可用）
- 内容写一行：`TS_AUTHKEY=tskey-xxxxxxx`
- 留空或不提供 → 脚本会交互提示你粘贴
- 获取：Tailscale 后台 `Settings → Keys → Generate auth key`（建议勾选 `Reusable` + `Tags`，勿勾 expiry 或设长）

## 2. 控制端公钥（免密登录）
- 把**你的控制端**公钥文件放这里，文件名任意，满足以下后缀之一即可被自动读取：
  - `*.pub`（例如 `id_ed25519.pub`）
  - `*.pub.local`（例如 `id_ed25519.pub.local`，推荐，避免误提交）
- 示例见 `id_ed25519.pub.example`（**不会被读取**，仅格式参考）
- 控制端公钥怎么来：
  - Linux/macOS：`cat ~/.ssh/id_ed25519.pub`
  - Windows：`type %USERPROFILE%\.ssh\id_ed25519.pub`
  - 没有就生成：`ssh-keygen -t ed25519`

> 公钥会被写入 Windows 的 `C:\ProgramData\ssh\administrators_authorized_keys`（管理员账户专用位置），
> 并设置严格 ACL，从而实现**免密码**登录被控端。

## ⚠ 安全
- 本目录下 `*.local` 文件已被 `.gitignore` 忽略，**不会提交到 GitHub**。
- 不要在这里放私钥，只放公钥和 authkey。
