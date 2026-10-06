# 配置生成器（可视化一键打包）

本目录是给**操作员（你）**用的本地图形界面：按目标机环境勾选 / 填写，
一键生成「配置已烘焙、目标机点击即运行」的离线压缩包。

## 运行

```bat
双击 运行生成器.bat
:: 或手动：
python build_gui.py
```

> 依赖 PyQt5：若未安装，`pip install PyQt5` 即可（你的 OCR 项目环境通常已带）。

## 界面说明

| 区域 | 作用 |
|---|---|
| ① 目标环境 | 选 Linux / Windows / 双端；Linux 还能选 amd64 / arm64 |
| ② 凭证与密钥 | 填 Tailscale Authkey（会写进包内 `keys/authkey.local.txt`）；<br>控制端公钥（仅 Windows 需要，写进 `windows/keys/control.pub`） |
| ③ 打包选项 | 输出格式（.tar.gz / .zip / 两者）、输出目录、包名前缀、<br>**是否内置便携 7-Zip**（Linux / Windows 各一个勾选框） |
| 运行日志 | 实时显示打包过程 |

## 便携 7-Zip 选项

有些目标机连解压缩都没有，那就勾上对应平台的 **「内置便携 7-Zip」**：

| 勾选后打进包 | 体积 | 目标机上怎么用 |
|---|---|---|
| `linux/assets/7zip/7zz`（静态，无依赖） | +2.7 MB | `./assets/7zip/7zz x 包.tar.gz` |
| `windows/assets/7zip/7za.exe`（单文件） | +0.6 MB | `.\assets\7zip\7za.exe x 包.tar.gz` |

- **不勾选**：`assets/7zip` 会被自动从包里剔除，包更小（适合确定目标机自带 tar / 7-Zip 的情况）。
- 7-Zip 是闭源二进制，**不入 Git 仓库**。首次勾选前先拉一次（只需联网一次）：
  ```bat
  python builder\fetch_7zip.py
  :: 或只拉一个：python builder\fetch_7zip.py windows
  ```
  已存在会自动跳过，`--force` 强制重下。
- 两个可执行物在打包时会被强制打上 **755 执行位**，Linux 下解压即可直接跑。

## 生成出的包长什么样

```
tailscale-remote-<os>-<日期>/
├── 连接说明.txt          # 目标机按这个跑就行
├── 一键部署.sh / .bat     # 顶层一键启动（按所选平台生成）
├── README.md             # 仓库总览
├── linux/   (若选了)      # 含已写好的 keys/authkey.local.txt + 运行.sh
│   └── assets/7zip/7zz   (若勾选内置 7-Zip)
└── windows/ (若选了)      # 含已写好的 keys/authkey.local.txt + control.pub
    └── assets/7zip/7za.exe  (若勾选内置 7-Zip)
```

- **Linux 目标**：双击 `一键部署.sh` 或 `linux/运行.sh`，或 `cd linux && bash connect-offline.sh`
- **Windows 目标**：右键 `windows/connect-windows.bat` → 以管理员身份运行
  （也可双击顶层 `一键部署.bat`）

因为 authkey / 公钥已经烘焙进包，**目标机无需联网下载、无需手动填 key，点开就能部署**。

## 安全提示

- Authkey 以明文写入包内 `keys/`，只发给可信目标；对方 `clean` 会清除。
- 建议在 Tailscale 后台用「限设备 / 可过期」的 key。
- 彻底下线还需到 https://login.tailscale.com/admin/machines 删除节点。
