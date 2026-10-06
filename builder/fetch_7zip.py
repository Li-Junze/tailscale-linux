#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_7zip.py —— 下载官方便携 7-Zip 二进制到各平台 assets/7zip/
=========================================================
为什么需要它:
    7-Zip 是闭源二进制, 体积不小(约 3MB), 因此 **不入 Git 仓库**。
    生成器里勾选「内置便携 7-Zip」时, 需要这两个文件就位:
        linux/assets/7zip/7zz        (静态, 无依赖, ~2.7MB)
        windows/assets/7zip/7za.exe  (独立单文件, ~0.6MB)
    本脚本从 7-zip.org 官方地址拉取并放好, 仅需联网一次。

用法:
    python builder/fetch_7zip.py            # 两个都下
    python builder/fetch_7zip.py linux      # 只下 Linux
    python builder/fetch_7zip.py windows    # 只下 Windows

说明:
    · 只依赖 Python 标准库(urllib / tarfile / zipfile), 不需要额外包。
    · 已存在且大小合理时默认跳过, 加 --force 可强制重新下载。
    · 下载的是 7-Zip 官方发布物, 版权归 Igor Pavlov, 遵循 LGPL/BSD 双许可。
"""
import os
import sys
import shutil
import zipfile
import tarfile
import tempfile
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 官方地址(静态版本号, 避免上游改文件名导致失效)
SRC = {
    "windows": "https://7-zip.org/a/7za920.zip",                 # 7-Zip 9.20 独立版
    "linux": "https://7-zip.org/a/7z2301-linux-x64.tar.xz",       # 7-Zip 23.01 linux x64 静态
}
DEST_REL = {
    "windows": os.path.join("windows", "assets", "7zip", "7za.exe"),
    "linux": os.path.join("linux", "assets", "7zip", "7zz"),
}
MIN_SIZE = 100 * 1024  # 小于 100KB 视为下载失败/损坏


def _download(url, timeout=180):
    print(f"  下载 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, tempfile.NamedTemporaryFile(
        delete=False, suffix=".dl"
    ) as tmp:
        shutil.copyfileobj(r, tmp)
        return tmp.name


def fetch(plat, force=False):
    dest = os.path.join(REPO_ROOT, DEST_REL[plat])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and os.path.getsize(dest) > MIN_SIZE and not force:
        print(f"[跳过] {DEST_REL[plat]} 已存在（{os.path.getsize(dest)//1024} KB）")
        return True

    tmp = None
    try:
        tmp = _download(SRC[plat])
        if os.path.getsize(tmp) < MIN_SIZE:
            print(f"[X] 下载过小（{os.path.getsize(tmp)} 字节），疑似失败")
            return False

        if plat == "windows":
            with zipfile.ZipFile(tmp) as z:
                if "7za.exe" not in z.namelist():
                    print("[X] 压缩包内未见 7za.exe")
                    return False
                with z.open("7za.exe") as fsrc, open(dest, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
        else:
            found = None
            with tarfile.open(tmp, "r:xz") as t:
                try:
                    t.extractall(dest + ".tmp", filter="data")
                except TypeError:      # Python < 3.12 无 filter 参数
                    t.extractall(dest + ".tmp")
            for root, _d, files in os.walk(dest + ".tmp"):
                for f in files:
                    if f == "7zz":
                        found = os.path.join(root, f)
            if not found:
                print("[X] 压缩包内未见 7zz")
                shutil.rmtree(dest + ".tmp", ignore_errors=True)
                return False
            shutil.copyfile(found, dest)
            shutil.rmtree(dest + ".tmp", ignore_errors=True)

        os.chmod(dest, 0o755)
        print(f"[OK]  {DEST_REL[plat]}  ({os.path.getsize(dest)//1024} KB)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[X]  {plat} 下载失败: {e}")
        return False
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def main():
    args = [a.lower() for a in sys.argv[1:]]
    force = "--force" in args
    plats = [a for a in args if a in ("linux", "windows")]
    if not plats:
        plats = ["linux", "windows"]

    print("=== 获取官方便携 7-Zip 二进制 ===")
    ok = True
    for p in plats:
        print(f"\n[{p}]")
        ok = fetch(p, force=force) and ok

    print("\n" + ("全部完成。可在生成器中勾选「内置便携 7-Zip」。" if ok else "部分失败，请检查网络后重试。"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())