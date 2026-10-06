# P2P 文件传输（双向）

在已建好的 Tailscale 通道上**直接传文件，双方都能收也能发**。

```
控制端                                   被控端
 拖文件到 dragdrop-send.bat      ←→      receive-files.bat（双击启动）
 或 python p2p.py send <IP> 文件           或 bash 收文件.sh
                                        或 python p2p.py serve --gui
```

---

## 为什么不用 scp？

在已连通的机器间传文件，`scp` 看着够用，但实际常卡在：

| 场景 | scp 的麻烦 | 本工具 |
|---|---|---|
| 对方是精简 Linux，没装 ssh 客户端 | 传不了 | 只用 python3 标准库 |
| 路径带中文/空格 | 引号转义容易出错 | 自动处理 |
| 对方不会敲命令 | 得教他 | `receive-files.bat` 双击就行 |
| 只想丢个安装包 | 还得先开会话 | 一条命令 |
| 大文件传到一半断了 | 半个坏文件 | 临时文件+原子改名，不留坏文件 |

---

## 依赖（重要）

| 组件 | 需要什么 | 说明 |
|---|---|---|
| 被控端 `serve` | **python3 本体即可** | `socket` / `threading` / `zipfile` 全是标准库 |
| 控制端 `send` / `fetch` | **python3 本体即可** | 同上 |
| `--gui` 弹窗模式 | + tkinter | ⚠ **纯可选**。精简 Linux 常缺，缺了自动降级为命令行模式，**收文件功能完全不受影响** |

不需要 `scp` / `rsync` / `sftp` / `paramiko` / PyQt。

---

## 用法

### 一、被控端：启动接收

```bash
python p2p.py serve                    # 存到 ./收件箱
python p2p.py serve --gui              # 弹窗 + "打开收件目录"按钮（有 tkinter 时）
python p2p.py serve --dir D:\收件 --port 9000
```

启动后屏幕会显示：

```
  监听    : 0.0.0.0:8765
  收件目录: C:\...\inbox
  口令    : 482913
  本机 Tailscale IP: 100.101.102.103
  → 让对方用这个 IP 传文件给你:
       python p2p.py send 100.101.102.103 <文件>
```

**保持窗口开着**。Windows 用户可直接双击 `receive-files.bat`；Linux/macOS 用 `bash 收文件.sh`。

### 二、控制端：送文件给对方

```bash
python p2p.py send 100.101.102.103 报告.pdf
python p2p.py send 100.101.102.103 -a 整个目录     # 打包成 zip 再传(推荐)
python p2p.py send 100.101.102.103 目录 -a         # 目录自动 zip
```

**拖拽方式（最省事）**：把要发的文件/文件夹**直接拖到 `dragdrop-send.bat` 图标上**，按提示填对方 IP 和口令即可。

### 三、控制端：从对方取文件（反向拉取）

```bash
python p2p.py fetch 100.101.102.103 -l              # 先看对方有哪些文件
python p2p.py fetch 100.101.102.103 截图.png        # 取回
```

---

## 安全设计

- **默认要口令**：serve 端自动生成 6 位数字口令（`--auth` 可指定），防止 tailnet 内其他设备误连。
- **只走私网**：走 Tailscale 的 `100.x.x.x`，不经公网。
- **越界保护**：`fetch` 的路径经 `abspath` 校验，`../` 跳出收件目录一律拒绝。
- **文件名净化**：去掉路径分隔符与 `<>:"/\|?*` 等字符，防止写到任意位置。
- **原子写入**：先写 `.part` 临时文件，`os.replace` 改名 —— 中断不会留半个坏文件。
- **同名不覆盖**：自动加 `(1) (2)` 序号。
- `--no-auth` 只在网络完全可控时用。

---

## 全部命令

```bash
python p2p.py serve  [-p 端口] [--dir 收件目录] [--gui] [--auth 口令] [--no-auth]
python p2p.py send   <对方IP> <文件...> [-a] [-p 端口] [--auth 口令] [--no-auth]
python p2p.py fetch  <对方IP> [相对路径] [-l] [-p 端口] [--auth 口令] [--no-auth]
python p2p.py --help
```

---

## 排错

**连接被拒（WinError 10061）**
对方没开 `serve`，或 IP 写错。在对方机器上 `tailscale status` 确认 IP。

**提示口令错误**
serve 端屏幕上的 6 位数字，注意别把空格也复制进去。也可以双方都用 `--no-auth`（网络可控时）。

**传大文件很慢**
正常现象 —— 走的是 Tailscale 直连（若同地区走 DERP 中继会慢）。传整个目录建议加 `-a` 打成 zip，压缩后小很多。

**Linux 上 `--gui` 没反应**
说明没装 tkinter，**不影响收文件**，看命令行窗口的进度即可。要 GUI：`sudo apt install python3-tk`。

**对方收不到 / 防火墙**
首次运行 Windows 可能弹防火墙询问，选「允许访问」。
