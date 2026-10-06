# keys/ —— authkey 临时存放点

本目录用于**临时存放 Tailscale auth key**，方便快速查看与自动授权：

- `authkey.local.txt` —— **本地文件，已被 `.gitignore` 忽略，不会上传到 GitHub**。
  用生成器打包时会自动写入；手动使用则自己创建（格式见 `authkey.local.txt.example`）。
  把你从 https://login.tailscale.com/admin/settings/keys
  生成的 auth key（以 `tskey-auth-` 开头）按下面格式粘贴进去即可：

  ```
  TS_AUTHKEY=tskey-auth-k1dHxxxxxxxxxxxxxxxxxxxxxxxx
  ```

- `connect-offline.sh` 运行时会**自动读取**本文件的 `TS_AUTHKEY` 行完成授权，
  免去交互粘贴。优先级：环境变量 `TS_AUTHKEY` > `keys/authkey.local.txt` > `我的连接信息.txt`。

- 用完记得用 `clean.sh` 清除痕迹，或手动清空本文件。

> 安全提示：真实 key 永远不要提交到公开仓库。本目录的设计就是"本地可用、远程不传"。
