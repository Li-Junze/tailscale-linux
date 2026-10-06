# 配置生成器（★ 双击 `★运行生成器.bat` 即可）

给**操作员（你）**用的本地图形界面：按目标机环境勾选 / 填密钥，
一键生成「配置已烘焙、对方解压后只跑两个文件」的部署包。

## 运行

```bat
双击 ★运行生成器.bat
:: 或手动：
python build_gui.py
```

> 依赖 PyQt5：若未安装，`pip install PyQt5` 即可。
> `★运行生成器.bat` 会自动找本机 python（python / py），并附带友好报错。

## 怎么启动（三选一）

| 方式 | 文件 | 背后有黑框吗 |
|---|---|---|
| **★推荐** | 双击 `Start-Toolbox.vbs` | **完全没有** |
| 带控制台（能看到报错） | 双击 `★运行生成器.bat` | 有（可关掉窗口但会带走程序） |
| 命令行 | `python build_gui.py` | 取决于你怎么起 |

### 为什么推荐 .vbs

`.bat` 必然带一个 cmd 控制台窗口。GUI 起来之后它就杵在背后，而且**用命令行的
方式关掉那个黑窗口会连带把 GUI 一起杀掉** —— 这正是之前"发个文件程序就退出"
的根源之一。

启动链路分三层，每层只干一件事：

```
Start-Toolbox.vbs   纯 ASCII。找 pythonw.exe, 然后 Run(..., 0) 调 launch.py
      ↓
launch.py           UTF-8 Python。定位项目 / 校验 PyQt5 / 放哨兵 /
      ↓            启动 GUI / 校验哨兵 / 失败弹窗
build_gui.py        图形界面本体
```

### ★ 为什么 VBS 必须纯 ASCII

VBScript 按**系统 ANSI 码页**读源码。你的项目在 WPS 云盘的路径里有中文
（`WPS企业云盘/清华大学/...`），如果直接写进 VBS 字符串字面量，会被误解码
成乱码 → `FileExists` 恒为假 → 报"找不到文件"。

所以：**中文路径一律放在 UTF-8 的 `launch.py` 里**，VBS 只碰纯 ASCII 路径。
`launch.py` 还会在已知位置都找不到时，**递归扫描 `C://Users//39969//WPSDrive`
（深度 7 层）**去找含 `builder\build_gui.py` 的目录 —— 也就是你把整个项目
挪到云盘任何位置都能用。

### 失败反馈：哨兵文件

静默启动（pythonw）拿不到 stderr，失败时你只会看到"双击了但什么都没发生"。
所以：启动前放 `.toolbox-alive`，`build_gui.py` 起来后会删掉它；
5 秒后还在 = 启动失败 → 写 `toolbox-launch-failed.log` + 弹窗告诉你装什么。

### 诊断命令

```bash
python builder/launch.py --probe     # 只打印定位结果, 不启动 GUI
```

输出示例：
```
project : C://Users//39969//WPSDrive//...//P2P//tailscale-remote
pythonw : C:/Python314/pythonw.exe
```

## 界面说明（两页）

**左页 · 生成部署包**

| 区域 | 作用 |
|---|---|
| ① 目标环境 | 选 Linux / Windows / 双端；Linux 可选 amd64 / arm64 |
| ② 运行模式 | **离线模式**（自带二进制，67MB，对方不联网也能装）或 **轻量模式**（几十KB，对方需联网自动下载） |
| ③ 密钥 | Tailscale Authkey（实时校验格式）；**一键在本机生成 SSH 密钥对**并自动填公钥（仅 Windows 被控端需要） |
| ④ 打包选项 | 输出格式、输出目录、包名前缀、**是否内置便携 7-Zip**；底部有**实时摘要**可核对 |
| 运行日志 | 实时显示打包过程 |

**右页 · 发送文件到被控端（单向）**

| 区域 | 作用 |
|---|---|
| ① 发给谁 | 从 tailnet 设备列表里选（显示在线/离线与系统），或手输 `100.x.x.x` |
| 方式 | **Taildrop**（推荐，被控端零配置）/ **scp**（对方需已 deploy） |
| **设备管理表** | 列出本机与所有设备；**双击 IP 复制**；状态列蓝色=本机、绿=在线、灰=离线 |
| 按钮 | **复制选中 IP** / **删除该节点** / **复制取文件命令** |
| ② 发什么 | 文件/文件夹**拖进来**；目录会自动打包成 zip（Taildrop 不支持目录） |
| ③ 传输日志 | 实时输出 tailscale / scp 的进度与报错 |

### 关于「删除该节点」

**tailscale 命令行只能退出【本机】**（`tailscale logout`），删不掉别的设备。
所以按钮会分两种情况：

- 选中的是**本机** → 直接 `tailscale logout`，一步到位
- 选中的是**别的设备** → 自动复制该 IP + 打开管理后台 machines 页，
  你在列表里搜这个 IP、点 `...` → `Delete` 即可

（这是 Tailscale 的权限设计，不是本工具的限制。）

### 关于「复制取文件命令」

一键复制 `tailscale file get .`，你直接发给对方即可，他在自己机器上执行就能取走。

## 调试日志

**任何阶段出问题都可以直接看 `builder/debug.log`**（按天轮转），
里面有时间戳记录：启动信息、解释器路径、每次发送的完整命令行、返回码、
子线程异常堆栈、全局未捕获异常。程序不会因为异常静默退出 ——
未捕获异常会弹窗提示并把堆栈写进这个日志。

★ 被控端**不需要装任何东西、不用跑脚本、不用先建 SSH 通道**：
```bash
控制端: tailscale file cp 文件... 100.x.x.x:
被控端: tailscale file get        # 取走到当前目录
```

## 生成出的包长什么样（刻意做得很干净）

```
tailscale-remote-<平台>-<离线|轻量>-<日期>/
├── deploy.sh / deploy.bat    ★ 部署 —— 对方只需要跑这个
├── clean.sh  / clean.bat     ★ 清洗 —— 用完只需要跑这个
├── README.md                 面向拿到包的人写的说明
├── 使用说明.txt               三步极简说明
└── 程序/                     ← 所有实现细节收纳于此，不用打开
    ├── linux/     (若选了)    脚本 + 预置二进制 + 你的 authkey
    └── windows/   (若选了)    脚本 + 预置 MSI   + 你的 authkey / 公钥
```

- **Linux 目标**：`bash deploy.sh`（会 `cd 程序/linux` 再跑主脚本）
- **Windows 目标**：右键 `deploy.bat` → 以管理员身份运行
- 两种平台的 `clean` 都会删除**整个包根**（不只是脚本所在那层）

## 离线 vs 轻量

| | 离线模式（默认，推荐） | 轻量模式 |
|---|---|---|
| 包体积 | ~67 MB | ~几十 KB |
| 包内二进制 | tailscale / tailscaled / MSI 全带 | 全部剥离 |
| 目标机要求 | **完全不需要联网**，没 curl/wget 也行 | 需能联网，脚本自动下载官方安装包 |
| 适合 | 目标机可能断网 / 环境极简 | 目标机能上网，在意传输体积 |

## 密钥

- **Tailscale Authkey**：本地无法生成，需到 <https://login.tailscale.com/admin/settings/keys> 创建
  （建议勾 Reusable、过期设 Disable）。界面里点「① 去 Tailscale 后台生成」会直接打开该页面，
  粘贴后会实时校验是否以 `tskey-auth-` 开头。
- **控制端 SSH 公钥**（仅 Windows 被控端需要）：点「② 一键在本机生成密钥对」最省事 ——
  会在本机 `~/.ssh/id_ed25519` 生成一对（无口令）并把公钥自动填进输入框。
  私钥必须留在你自己（控制端）电脑上，不要发给任何人。

## 便携 7-Zip 选项

有些目标机连解压缩都没有，那就勾上对应平台的 **「内置便携 7-Zip」**：

| 勾选后打进包 | 体积 | 目标机上怎么用 |
|---|---|---|
| `程序/linux/assets/7zip/7zz`（静态，无依赖） | +2.7 MB | `./程序/linux/assets/7zip/7zz x 包.tar.gz` |
| `程序/windows/assets/7zip/7za.exe`（单文件） | +0.6 MB | `.\程序\windows\assets\7zip\7za.exe x 包.tar.gz` |

- **不勾选**：`assets/7zip` 会被自动剔除，省体积。
- 7-Zip 是闭源二进制，**不入 Git 仓库**。首次勾选前先拉一次（只需联网一次）：
  ```bat
  python builder\fetch_7zip.py
  :: 或只拉一个：python builder\fetch_7zip.py windows
  ```
  已存在会自动跳过，`--force` 强制重下。

## 配置记忆

所有选择自动存到 `~/.tailscale_remote/generator_config.json`，下次打开自动恢复：

- 记忆内容：目标平台、运行模式、架构、格式、包名前缀、输出目录、7-Zip 勾选、传文件勾选、**公钥**。
- **authkey 默认不记**（明文落盘有风险），需要时勾「记住 authkey」。
- 任何改动都会静默存一次，关窗口时再存一次 —— 闪退/误关都不会丢选择。
- 想清空记忆：直接删掉上面那个 json 文件即可。

## 安全提示

- Authkey 以明文写入包内 `程序/<平台>/keys/`，只发给可信目标；对方跑 clean 会清除。
- 建议在 Tailscale 后台用「限设备 / 可过期」的 key。
- 彻底下线还需到 <https://login.tailscale.com/admin/machines> 删除节点。
