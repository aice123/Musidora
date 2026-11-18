#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_shortcut.py —— 在本机生成一个带图标的启动快捷方式（Musidora.lnk）

为什么要用脚本生成，而不是直接把 .lnk 放进仓库：
    Windows 快捷方式里存的是**绝对路径**（pythonw.exe 的位置、工作目录），
    换一台电脑就失效。所以仓库放生成脚本，用户跑一次得到适配自己环境的快捷方式。

用法：
    python make_shortcut.py              # 在本程序目录生成 Musidora.lnk
    python make_shortcut.py --desktop    # 同时复制一份到桌面
    python make_shortcut.py --python-exe "C:\\Python311\\pythonw.exe"   # 指定解释器
"""
import argparse
import os
import shutil
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(APP_DIR, "assets", "icon.ico")
TARGET = os.path.join(APP_DIR, "app.py")
LNK_NAME = "Musidora.lnk"


def find_python_windowed(explicit=None):
    """优先找 pythonw.exe（无控制台窗口），其次 python.exe"""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    exe = sys.executable or ""
    d, name = os.path.split(exe)
    if name.lower() == "python.exe":
        cand = os.path.join(d, "pythonw.exe")
        if os.path.exists(cand):
            return cand
    for name in ("pythonw.exe", "python.exe"):
        w = shutil.which(name)
        if w:
            return w
    return exe or None


def make_with_win32com(lnk_path, exe, args, workdir, icon):
    from win32com.client import Dispatch
    sh = Dispatch("WScript.Shell")
    s = sh.CreateShortCut(lnk_path)
    s.TargetPath = exe
    s.Arguments = args
    s.WorkingDirectory = workdir
    if icon and os.path.exists(icon):
        s.IconLocation = icon + ",0"
    s.Description = "Musidora"
    s.Save()
    return True


def make_with_powershell(lnk_path, exe, args, workdir, icon):
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.Arguments='%s';$s.WorkingDirectory='%s';"
        "$s.IconLocation='%s,0';$s.Description='Musidora';$s.Save()"
    ) % (lnk_path, exe, args, workdir, icon if icon and os.path.exists(icon) else exe)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    return r.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 Musidora 启动快捷方式")
    ap.add_argument("--desktop", action="store_true", help="同时放一份到桌面")
    ap.add_argument("--python-exe", default="", help="指定 pythonw.exe 路径")
    ap.add_argument("--out-dir", default=APP_DIR, help="快捷方式输出目录")
    a = ap.parse_args(argv)

    if os.name != "nt":
        print("这个脚本只用于 Windows。Linux/macOS 请直接运行：python app.py")
        return 1
    if not os.path.exists(TARGET):
        print("找不到 app.py，请把本脚本放在项目根目录")
        return 1

    exe = find_python_windowed(a.python_exe)
    if not exe:
        print("找不到 Python 解释器，请用 --python-exe 指定，例如：")
        print('  python make_shortcut.py --python-exe "C:\\Python311\\pythonw.exe"')
        return 1

    out_dir = a.out_dir
    os.makedirs(out_dir, exist_ok=True)
    lnk = os.path.join(out_dir, LNK_NAME)

    ok = False
    try:
        ok = make_with_win32com(lnk, exe, '"%s"' % TARGET, APP_DIR, ICON)
    except Exception:
        ok = make_with_powershell(lnk, exe, '"%s"' % TARGET, APP_DIR, ICON)

    if not ok or not os.path.exists(lnk):
        print("创建失败。可以手动：右键 app.py → 发送到 → 桌面快捷方式，再改图标为 assets/icon.ico")
        return 1

    print("已创建: %s" % lnk)
    print("  目标: %s" % exe)
    print("  脚本: %s" % TARGET)
    print("  图标: %s" % (ICON if os.path.exists(ICON) else "(默认)"))

    if a.desktop:
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(desk):
            dst = os.path.join(desk, LNK_NAME)
            shutil.copy2(lnk, dst)
            print("已复制到桌面: %s" % dst)
        else:
            print("没找到桌面目录，跳过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
