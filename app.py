#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
工作台 —— 中文提示词 · 生图 · 批量 · 英语学习，一个窗口

左栏：中文描述 →（本地 Qwen3 翻译）→ 正向提示词 / 负向提示词 / 英文句子 / 逐词对译
右栏：单张 或 批量（源目录 + 输出目录）→ 送 ComfyUI 出图

· 全相对路径：规则、配置、工作流都在本目录；Ollama/ComfyUI 位置自动探测，可在 设置.json 改
· 不联网：只调用本机 Ollama(11434) 与 ComfyUI(8188)
"""
import json, os, queue, urllib.request, random, re, shutil, subprocess, threading, time, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(APP_DIR, "设置.json")
RULES_FILE = os.path.join(APP_DIR, "提示词规则.txt")
CREATE_NO_WINDOW = 0x08000000   # 子进程不弹黑窗

OLLAMA_API = "http://127.0.0.1:11434"
COMFY_API = "http://127.0.0.1:8188"

FIELDS = ["subject", "action", "camera", "clothing", "scene", "light", "style"]
DEFAULT_NEG = ["(worst quality, low quality:1.4)", "(child:2.0)", "(minor:2.0)",
               "(bad hands:1.4)", "watermark", "text"]
DEFAULT_STYLE = ["raw photo", "film grain", "35mm photograph", "shallow depth of field"]
POS_TAGS = {"n.", "v.", "adj.", "adv.", "prep.", "phrase", "pron.", "conj."}
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

SAMPLERS = ["dpmpp_2m_sde", "dpmpp_2m", "euler", "euler_ancestral", "dpmpp_sde", "uni_pc"]
SCHEDULERS = ["karras", "normal", "exponential", "sgm_uniform"]


# ================================================================ 外观
PALETTES = {
    "amber":   {"name": "琥珀", "bg": "#08090B", "card": "#12141A", "field": "#1D2128",
                "border": "#31363F", "fg": "#F6F7F9", "hint": "#9AA2AE",
                "accent": "#FFB020", "accent2": "#E09000", "on_accent": "#1A1200",
                "tab": "#FFC24D"},
    "violet":  {"name": "紫罗兰", "bg": "#08080D", "card": "#131320", "field": "#1E1E2E",
                "border": "#33334A", "fg": "#F4F2FF", "hint": "#9C99B8",
                "accent": "#A78BFA", "accent2": "#8B5CF6", "on_accent": "#12082B",
                "tab": "#E879F9"},
    "magenta": {"name": "洋红", "bg": "#0A070A", "card": "#160F16", "field": "#231A23",
                "border": "#3B2A3B", "fg": "#FBF2FA", "hint": "#B49AB0",
                "accent": "#FF3FA4", "accent2": "#DB2777", "on_accent": "#2A0016",
                "tab": "#FF8AC4"},
    "mono":    {"name": "极简", "bg": "#0A0A0A", "card": "#151515", "field": "#202020",
                "border": "#333333", "fg": "#F5F5F5", "hint": "#9A9A9A",
                "accent": "#E8E8E8", "accent2": "#BFBFBF", "on_accent": "#111111",
                "tab": "#FFFFFF"},
    "blue":    {"name": "钢蓝", "bg": "#070A10", "card": "#101724", "field": "#1A2434",
                "border": "#2C3B50", "fg": "#F2F6FC", "hint": "#93A3B8",
                "accent": "#4C8DFF", "accent2": "#2F6FE0", "on_accent": "#04122E",
                "tab": "#7FB2FF"},
}
THEME_ORDER = ["amber", "violet", "magenta", "mono", "blue"]


def enable_dpi_awareness():
    """开启 Per-Monitor DPI 感知：否则 Windows 会把窗口位图拉伸，字就发虚"""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)      # Win8.1+ 每显示器 v2
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()           # 退路
    except Exception:
        pass


class WrapTable:
    """两列可换行词表：表头固定不动，内容可滚，列宽按同一比例算"""

    def __init__(self, parent, palette, head1="单词 / 短语", head2="中文"):
        self.pal = palette
        bg, fg, hint = palette["card"], palette["fg"], palette["hint"]
        outer = tk.Frame(parent, bg=bg, bd=0, highlightthickness=0)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        # 表头：固定在第 0 行，和内容同列，宽度与画布一致
        self.head = tk.Frame(outer, bg=palette["field"])
        self.head.grid(row=0, column=0, sticky="ew")
        self.head.columnconfigure(0, weight=2)
        self.head.columnconfigure(1, weight=3)
        self.h1 = tk.Label(self.head, text=head1, anchor="w", justify="left",
                           bg=palette["field"], fg=hint,
                           font=("Microsoft YaHei UI", 9, "bold"))
        self.h2 = tk.Label(self.head, text=head2, anchor="w", justify="left",
                           bg=palette["field"], fg=hint,
                           font=("Microsoft YaHei UI", 9, "bold"))
        self.h1.grid(row=0, column=0, sticky="w", padx=(8, 4), pady=4)
        self.h2.grid(row=0, column=1, sticky="w", padx=(8, 8), pady=4)

        self.canvas = tk.Canvas(outer, bg=bg, highlightthickness=0, bd=0)
        self.vs = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vs.set)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.vs.grid(row=0, column=1, rowspan=2, sticky="ns")

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.columnconfigure(0, weight=2)
        self.inner.columnconfigure(1, weight=3)

        self.rows = []
        self._en, self._cn = [], []
        self._row = 0

        self.canvas.bind("<Configure>", self._on_resize)
        self.inner.bind("<Configure>", self._sync_scroll)
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")

    def _sync_scroll(self, e=None):
        try:
            self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_width(),
                                                max(1, self.inner.winfo_height())))
        except Exception:
            pass

    def _wheel(self, e):
        try:
            if self.canvas.winfo_ismapped():
                first, last = self.canvas.yview()
                if last - first >= 0.999:
                    return                      # 内容没溢出，不滚
                self.canvas.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    def _on_resize(self, e=None):
        w = self.canvas.winfo_width()
        if w < 30:
            return
        self.canvas.itemconfigure(self._win, width=w)
        w1 = max(50, int((w - 26) * 0.4))
        w2 = max(50, w - w1 - 26)
        self.h1.configure(wraplength=w1)
        self.h2.configure(wraplength=w2)
        for a, b in self.rows:
            a.configure(wraplength=w1)
            b.configure(wraplength=w2)
        self._sync_scroll()

    def clear(self):
        for a, b in self.rows:
            a.destroy(); b.destroy()
        self.rows = []
        self._en, self._cn = [], []
        self._row = 0
        self.canvas.yview_moveto(0)
        self._sync_scroll()

    def add(self, en, cn, masked=False):
        pal = self.pal
        a = tk.Label(self.inner, text=en, anchor="w", justify="left", bg=pal["card"],
                     fg=pal["fg"], font=("Consolas", 9))
        b = tk.Label(self.inner, text=cn, anchor="w", justify="left", bg=pal["card"],
                     fg=pal["fg"], font=("Microsoft YaHei UI", 9))
        a.grid(row=self._row, column=0, sticky="w", padx=(8, 4), pady=3)
        b.grid(row=self._row, column=1, sticky="w", padx=(4, 8), pady=3)
        self._row += 1
        sep = tk.Frame(self.inner, bg=pal["border"], height=1)
        sep.grid(row=self._row, column=0, columnspan=2, sticky="ew")
        self._row += 1
        self.rows.append((a, b))
        self._en.append(en)
        self._cn.append(cn)
        self._on_resize()

    def count(self):
        return len(self.rows)


def setup_style(root, key=None):
    """按主题配色构建 ttk 样式"""
    key = key if key in PALETTES else "amber"
    c = PALETTES[key]
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except Exception:
        pass
    BG, CARD, FIELD = c["bg"], c["card"], c["field"]
    BORDER, FG, HINT = c["border"], c["fg"], c["hint"]
    ACCENT, ACCENT2 = c["accent"], c["accent2"]
    ON, TAB = c["on_accent"], c["tab"]

    root.configure(bg=BG)
    s.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=CARD)
    s.configure("Bar.TFrame", background=CARD)
    s.configure("TabBody.TFrame", background=CARD)
    s.configure("Title.TLabel", background=CARD, foreground=FG,
                font=("Microsoft YaHei UI", 15, "bold"))
    s.configure("Subtitle.TLabel", background=CARD, foreground=HINT,
                font=("Microsoft YaHei UI", 10))
    s.configure("CardTitle.TLabel", background=CARD, foreground=c["tab"],
                font=("Microsoft YaHei UI", 11, "bold"))
    s.configure("BarHint.TLabel", background=CARD, foreground=HINT,
                font=("Microsoft YaHei UI", 10))
    s.configure("Hint.TLabel", background=CARD, foreground=HINT,
                font=("Microsoft YaHei UI", 10))
    s.configure("Status.TLabel", background=BG, foreground=HINT,
                font=("Microsoft YaHei UI", 10))
    s.configure("TLabel", background=CARD, foreground=FG)
    s.configure("DotOff.TLabel", background=CARD, foreground="#6B7283", font=("Segoe UI", 11))
    s.configure("DotOn.TLabel", background=CARD, foreground=ACCENT, font=("Segoe UI", 11))

    s.configure("TButton", padding=(10, 5), font=("Microsoft YaHei UI", 9),
                background=FIELD, foreground=FG, borderwidth=1, relief="flat",
                bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD)
    s.map("TButton", background=[("active", c["border"])], bordercolor=[("active", ACCENT)])
    s.configure("Accent.TButton", background=ACCENT, foreground=ON,
                borderwidth=0, padding=(14, 8), font=("Microsoft YaHei UI", 11, "bold"))
    s.map("Accent.TButton",
          background=[("active", ACCENT2), ("disabled", c["border"])],
          foreground=[("disabled", HINT)])
    s.configure("Ghost.TButton", background=CARD, foreground=HINT,
                borderwidth=0, padding=(9, 5), font=("Segoe UI", 10))
    s.map("Ghost.TButton", background=[("active", FIELD)], foreground=[("active", FG)])

    # 页签：统一宽度、去掉白色描边、选中态与未选中态大小一致
    s.configure("Card.TNotebook", background=CARD, borderwidth=0, tabmargins=(10, 8, 10, 0),
                lightcolor=CARD, darkcolor=CARD, bordercolor=CARD)
    s.configure("Card.TNotebook.Tab", background=FIELD, foreground=HINT,
                padding=(14, 5), borderwidth=0, width=6,
                lightcolor=FIELD, darkcolor=FIELD, bordercolor=FIELD,
                font=("Segoe UI", 10))
    s.map("Card.TNotebook.Tab",
          background=[("selected", FIELD), ("active", BORDER)],
          foreground=[("selected", TAB), ("!selected", HINT)],
          lightcolor=[("selected", FIELD), ("!selected", FIELD)],
          darkcolor=[("selected", FIELD), ("!selected", FIELD)],
          bordercolor=[("selected", FIELD), ("!selected", FIELD)],
          font=[("selected", ("Microsoft YaHei UI", 10, "bold")), ("!selected", ("Microsoft YaHei UI", 10))],
          expand=[])

    s.configure("Gloss.Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                rowheight=30, borderwidth=0, font=("Segoe UI", 10))
    s.layout("Gloss.Treeview", [("Gloss.Treeview.treearea", {"sticky": "nswe"})])
    s.configure("Gloss.Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                borderwidth=0, relief="flat", rowheight=26)
    s.map("Gloss.Treeview",
          background=[("selected", ACCENT2)],
          foreground=[("selected", ON)],
          bordercolor=[("focus", CARD), ("!focus", CARD)],
          lightcolor=[("focus", CARD), ("!focus", CARD)],
          darkcolor=[("focus", CARD), ("!focus", CARD)])
    s.configure("Rand.TCheckbutton", background=FIELD, foreground=HINT,
                font=("Microsoft YaHei UI", 9), padding=(10, 4), anchor="center",
                relief="flat", borderwidth=0, focusthickness=0, focuscolor=FIELD)
    s.map("Rand.TCheckbutton",
          background=[("selected", ACCENT), ("active", ACCENT2)],
          foreground=[("selected", ON), ("active", ON)],
          indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)])
    s.layout("Rand.TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})])
    s.configure("Gloss.Treeview.Heading", background=FIELD, foreground=HINT,
                relief="flat", font=("Segoe UI", 10))
    s.map("Gloss.Treeview", background=[("selected", c["border"])], foreground=[("selected", FG)])

    # 输入类：无亮边、无白色描边，箭头加大
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(name, fieldbackground=FIELD, background=FIELD, foreground=FG,
                    borderwidth=0, relief="flat", bordercolor=FIELD,
                    lightcolor=FIELD, darkcolor=FIELD,
                    insertcolor=FG, arrowcolor=c["tab"], arrowsize=18, padding=6,
                    font=("Microsoft YaHei UI", 10))
        s.map(name, fieldbackground=[("readonly", FIELD), ("focus", FIELD)],
              bordercolor=[("focus", ACCENT), ("!focus", FIELD)],
              lightcolor=[("focus", ACCENT), ("!focus", FIELD)],
              darkcolor=[("focus", ACCENT), ("!focus", FIELD)],
              foreground=[("disabled", HINT)])
    s.configure("Bar.TCombobox", fieldbackground=FIELD, background=FIELD, foreground=FG,
                borderwidth=0, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD,
                arrowcolor=c["tab"], arrowsize=18, padding=5, font=("Microsoft YaHei UI", 10))
    s.configure("Bar.TSpinbox", fieldbackground=FIELD, background=FIELD, foreground=FG,
                borderwidth=0, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD,
                arrowcolor=c["tab"], arrowsize=18, padding=5, font=("Microsoft YaHei UI", 10))
    s.map("Bar.TCombobox", fieldbackground=[("readonly", FIELD)],
          bordercolor=[("focus", ACCENT)], lightcolor=[("!focus", FIELD)])
    s.configure("TProgressbar", background=ACCENT, troughcolor=FIELD, borderwidth=0)
    try:
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    except Exception:
        pass
    return c


# ================================================================ 通用
def http_json(base, path, payload=None, timeout=180):
    import urllib.request, urllib.error
    req = urllib.request.Request(base + path,
                                 data=json.dumps(payload).encode() if payload else None,
                                 headers={"Content-Type": "application/json"} if payload else {})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode("utf-8", "replace")[:300])


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    k = 1
    while os.path.exists("%s_%d%s" % (base, k, ext)):
        k += 1
    return "%s_%d%s" % (base, k, ext)


def pids_on_port(port):
    """返回监听指定端口的进程 PID 集合（纯 API，不启动子进程、不弹窗）"""
    import ctypes
    from ctypes import wintypes
    pids = set()
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi")
        size = wintypes.DWORD(0)
        # TCP_TABLE_OWNER_PID_ALL = 5
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 5, 0)
        buf = ctypes.create_string_buffer(size.value)
        if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 5, 0) != 0:
            raise OSError("GetExtendedTcpTable failed")
        num = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
        base = ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD)
        # MIB_TCPROW_OWNER_PID: state, local_addr, local_port, remote_addr, remote_port, pid
        for i in range(num):
            row = ctypes.cast(base + i * 24, ctypes.POINTER(wintypes.DWORD))
            state, laddr, lport, raddr, rport, pid = [row[j] for j in range(6)]
            if state == 2 and ((lport & 0xFFFF) >> 8 | (lport & 0xFF) << 8) == int(port):
                pids.add(str(pid))
        return pids
    except Exception:
        pass
    # 兜底：老办法（静默执行）
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=15, creationflags=CREATE_NO_WINDOW).stdout
        key = ":%d" % int(port)
        for line in out.splitlines():
            up = line.upper()
            if key in line and "LISTENING" in up:
                pid = line.split()[-1].strip()
                if pid.isdigit():
                    pids.add(pid)
    except Exception:
        pass
    return pids


def kill_pid(pid):
    """用 TerminateProcess 结束进程（不启动 taskkill，不弹窗）"""
    import ctypes
    PROCESS_TERMINATE = 0x0001
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if not h:
            return False
        ok = k32.TerminateProcess(h, 1)
        k32.CloseHandle(h)
        return bool(ok)
    except Exception:
        return False


def kill_port(port):
    """结束监听该端口的进程，返回被结束的 PID 列表"""
    killed = []
    for pid in pids_on_port(port):
        if kill_pid(pid):
            killed.append(pid)
        else:
            # 兜底：权限不足时退回 taskkill（静默）
            try:
                r = subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                                   capture_output=True, timeout=15,
                                   creationflags=CREATE_NO_WINDOW)
                if r.returncode == 0:
                    killed.append(pid)
            except Exception:
                pass
    return killed


def kill_by_name(names):
    """按进程名结束残留进程（如 ollama 的模型 runner）"""
    killed = []
    for name in names:
        try:
            r = subprocess.run(["taskkill", "/F", "/T", "/IM", name],
                               capture_output=True, text=True, timeout=15,
                               creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0:
                killed.append(name)
        except Exception:
            pass
    return killed


def safe_makedirs(path):
    """建目录；失败返回 False 而不是抛异常"""
    try:
        if not path:
            return False
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False


# ================================================================ Ollama
def find_ollama(cfg):
    cands = []
    if cfg.get("ollama_exe"):
        cands.append(cfg["ollama_exe"])
    if os.environ.get("OLLAMA_EXE"):
        cands.append(os.environ["OLLAMA_EXE"])
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        cands.append(os.path.join(la, "Programs", "Ollama", "ollama.exe"))
    # 本身所在盘 + 所有存在的盘符下的 \ollama\ollama.exe
    drive = os.path.splitdrive(APP_DIR)[0] + os.sep
    cands.append(os.path.join(drive, "ollama", "ollama.exe"))
    for letter in "CDEFGH":
        cands.append("%s:\\ollama\\ollama.exe" % letter)
    cands.append(r"C:\Program Files\Ollama\ollama.exe")
    w = shutil.which("ollama")
    if w:
        cands.append(w)
    for c in cands:
        try:
            if c and os.path.isfile(c):
                return c
        except Exception:
            pass
    return ""


def ollama_alive():
    try:
        http_json(OLLAMA_API, "/api/tags", timeout=3)
        return True
    except Exception:
        return False


def start_ollama(exe):
    if not exe or not os.path.exists(exe):
        return False
    env = dict(os.environ)
    # 模型目录：优先用已设的环境变量，否则用 ollama.exe 同级 models 目录
    if not env.get("OLLAMA_MODELS"):
        guess = os.path.join(os.path.dirname(exe), "models")
        if os.path.isdir(guess):
            env["OLLAMA_MODELS"] = guess
    # 显存与速度优化（实测：生成速度 2.8 → 5.3 tok/s）
    env.setdefault("OLLAMA_FLASH_ATTENTION", "1")     # 省 KV 显存、加速注意力
    env.setdefault("OLLAMA_KV_CACHE_TYPE", "q8_0")    # KV 缓存量化，减半
    env.setdefault("OLLAMA_NUM_PARALLEL", "1")        # 单并发，不重复分配 KV
    env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
    try:
        subprocess.Popen([exe, "serve"], cwd=os.path.dirname(exe), env=env,
                         creationflags=0x08000000,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for _ in range(60):
        time.sleep(1)
        if ollama_alive():
            return True
    return False


_FACE_APP = None


def face_engine():
    """懒加载 insightface（只用 CPU，不占显存）"""
    global _FACE_APP
    if _FACE_APP is None:
        from insightface.app import FaceAnalysis
        fa = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        fa.prepare(ctx_id=-1, det_size=(640, 640))
        _FACE_APP = fa
    return _FACE_APP


def count_faces(path, max_side=1280, min_ratio=0.12):
    """返回图片里的主体数量（只算前景，忽略背景小脸）；失败返回 -1
    min_ratio：主体高度占画面高度的最低比例，低于它视为背景路人
    """
    if not path or not os.path.exists(path):
        return -1
    try:
        import numpy as np
        import cv2
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return -1
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            s = float(max_side) / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)))
        faces = face_engine().get(img)
        if not faces:
            return 0
        hh = img.shape[0]
        big = [f for f in faces if (f.bbox[3] - f.bbox[1]) >= hh * min_ratio]
        return len(big) if big else len(faces)
    except Exception:
        return -1


def face_embeddings(path, max_side=1280):
    """返回按左→右排序的 normed embedding 列表；失败返回 []"""
    if not path or not os.path.exists(path):
        return []
    try:
        import numpy as np
        import cv2
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return []
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            s = float(max_side) / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)))
        fs = face_engine().get(img)
        fs.sort(key=lambda f: f.bbox[0])
        out = []
        for f in fs:
            e = np.asarray(f.normed_embedding, dtype=np.float32)
            out.append(e / (np.linalg.norm(e) + 1e-9))
        return out
    except Exception:
        return []


def auto_pair(src_path, out_path):
    """每个目标主体各自配最像的源主体（源可重复），返回 (源索引串, 目标索引串)"""
    import numpy as np
    src = face_embeddings(src_path)
    out = face_embeddings(out_path)
    if not src or not out:
        return "", ""
    src_list, tgt_list = [], []
    for ti, te in enumerate(out):
        sims = [float(np.dot(te, se)) for se in src]
        si = int(np.argmax(sims))
        src_list.append(str(si))
        tgt_list.append(str(ti))
    return ",".join(src_list), ",".join(tgt_list)


def similarity_report(src_path, out_path):
    """逐张比对：每个输出主体最像源图的哪一个，返回 [(输出序号, 源序号, 相似度)] 与平均分"""
    import numpy as np
    src = face_embeddings(src_path)
    out = face_embeddings(out_path)
    if not src or not out:
        return [], 0.0
    rows = []
    for i, e in enumerate(out):
        sims = [float(np.dot(e, s)) for s in src]
        best = int(np.argmax(sims))
        rows.append((i, best, sims[best]))
    avg = sum(r[2] for r in rows) / len(rows)
    return rows, avg


def count_faces_detail(path, max_side=1280, min_ratio=0.12):
    """返回 (前景数量, 全部数量)"""
    if not path or not os.path.exists(path):
        return -1, -1
    try:
        import numpy as np
        import cv2
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return -1, -1
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            s = float(max_side) / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)))
        faces = face_engine().get(img)
        hh = img.shape[0]
        big = [f for f in faces if (f.bbox[3] - f.bbox[1]) >= hh * min_ratio]
        return (len(big) if big else len(faces)), len(faces)
    except Exception:
        return -1, -1


def master_image_path(wf_name, comfy_dir):
    """工作流里喂给 ReActor 的那张母版图，返回本地绝对路径"""
    try:
        wf = os.path.join(APP_DIR, "工作流", wf_name)
        data = json.load(open(wf, encoding="utf-8"))
        nodes = {str(n["id"]): n for n in data.get("nodes", [])} if "nodes" in data else data
        for nid, node in nodes.items():
            if node.get("class_type") == "ReActorFaceSwap":
                ref = node.get("inputs", {}).get("source_image")
                if isinstance(ref, list) and len(ref) == 2:
                    src = nodes.get(str(ref[0]))
                    if src and src.get("class_type") == "LoadImage":
                        name = src.get("inputs", {}).get("image")
                        main_dir, _ = resolve_comfy_paths(comfy_dir)
                        cand = os.path.join(main_dir, "input", name) if main_dir else ""
                        if cand and os.path.exists(cand):
                            return cand
            # UI 格式
            if node.get("type") == "ReActorFaceSwap":
                for inp in node.get("inputs", []):
                    if inp.get("name") == "source_image":
                        lid = inp.get("link")
                        for n2 in data.get("nodes", []):
                            if n2.get("type") == "LoadImage" and lid in n2.get("outputs", [{}])[0].get("links", []):
                                name = (n2.get("widgets_values") or [""])[0]
                                main_dir, _ = resolve_comfy_paths(comfy_dir)
                                cand = os.path.join(main_dir, "input", name) if main_dir else ""
                                if cand and os.path.exists(cand):
                                    return cand
    except Exception:
        pass
    return ""


def vram_free_mb():
    """当前可用显存（MB）；查询失败返回 -1"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8,
                             creationflags=CREATE_NO_WINDOW).stdout.strip()
        return int(out.splitlines()[0].strip())
    except Exception:
        return -1


def free_comfy_vram():
    """让 ComfyUI 卸载模型、释放显存，给翻译模型腾地方"""
    try:
        req = urllib.request.Request(
            COMFY_API + "/free",
            data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False


def diag_log(**kw):
    """把一次翻译的关键指标追加到 诊断.log（jsonl），便于事后定位"""
    try:
        kw["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        path = os.path.join(APP_DIR, "诊断.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    except Exception:
        pass


def gpu_snapshot():
    """GPU 已用/总量 + 占用显存的进程名（尽力而为）"""
    info = {"used": -1, "total": -1, "procs": []}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True,
                             creationflags=CREATE_NO_WINDOW).stdout.strip()
        u, tt = [int(x) for x in out.split(",")]
        info["used"], info["total"] = u, tt
    except Exception:
        pass
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name",
                              "--format=csv,noheader"],
                             capture_output=True, text=True,
                             creationflags=CREATE_NO_WINDOW).stdout.strip()
        info["procs"] = [l.split(",")[-1].strip().split("\\")[-1] for l in out.splitlines() if l]
    except Exception:
        pass
    return info


def ping_speed(model, num_ctx=4096):
    """跑 8 token 的探针，返回 tok/s（<0 表示失败）"""
    try:
        payload = {"model": model, "stream": False, "think": False, "keep_alive": "30m",
                   "messages": [{"role": "user", "content": "hi"}],
                   "options": {"num_predict": 8, "num_ctx": num_ctx, "temperature": 0}}
        r = http_json(OLLAMA_API, "/api/chat", payload, timeout=180)
        ec = r.get("eval_count", 0)
        ed = (r.get("eval_duration", 0) or 0) / 1e9
        return (ec / ed) if ed > 0 else -1
    except Exception:
        return -1


def preload_model(model, keep_alive="30m", num_ctx=4096):
    """让 ollama 先把模型整块拉进显存（空请求，不产生输出）"""
    try:
        payload = {"model": model, "messages": [{"role": "user", "content": "hi"}],
                   "stream": False, "keep_alive": keep_alive,
                   "options": {"num_predict": 1, "num_ctx": num_ctx}}
        http_json(OLLAMA_API, "/api/chat", payload, timeout=300)
        return True
    except Exception:
        return False


def fit_model_to_vram(model, keep_alive="30m"):
    """从大上下文往小试，返回能“完整”进显存的那个 num_ctx（全部失败则返回最小档）"""
    for ctx in (4096, 3072, 2048, 1024):
        preload_model(model, keep_alive, ctx)
        v, total = ollama_residency()
        if total > 0 and v >= total * 0.9:
            return ctx, True
        unload_ollama(model)
    return 1024, False


def ollama_residency():
    """返回 (显存占用字节, 模型总字节)，没有模型时 (0, 0)"""
    try:
        ps = http_json(OLLAMA_API, "/api/ps", timeout=5).get("models", [])
        vram = max([m.get("size_vram", 0) for m in ps] + [0])
        total = max([m.get("size", 0) for m in ps] + [0])
        return vram, total
    except Exception:
        return 0, 0


def gpu_util_sample(seconds):
    """采样 GPU 利用率，返回 (平均, 峰值)"""
    try:
        vals = []
        end = time.time() + seconds
        while time.time() < end:
            out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu",
                                  "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True,
                                 creationflags=CREATE_NO_WINDOW).stdout.strip()
            if out.isdigit():
                vals.append(int(out))
            time.sleep(1.0)
        return (sum(vals) // len(vals) if vals else -1,
                max(vals) if vals else -1)
    except Exception:
        return -1, -1


def comfy_busy():
    """ComfyUI 是否正在执行任务（此时 /free 不会释放显存）"""
    try:
        q = http_json(COMFY_API, "/queue", timeout=5)
        return bool(q.get("queue_running"))
    except Exception:
        return False


def wait_vram_free(need_mb, timeout=10):
    """等显存真正退回来（ComfyUI 的 /free 是异步的）"""
    t0 = time.time()
    last = -1
    stable = 0
    while time.time() - t0 < timeout:
        free = vram_free_mb()
        if free < 0:
            return free
        if free >= need_mb:
            return free
        if abs(free - last) < 64:
            stable += 1
            if stable >= 3:      # 连续三次不再变化，认为已稳定
                return free
        else:
            stable = 0
        last = free
        time.sleep(0.4)
    return vram_free_mb()


def unload_ollama(model=None):
    """把对话模型从显存里请出去（keep_alive=0），给 ComfyUI 腾地方"""
    import urllib.request
    names = []
    try:
        ps = http_json(OLLAMA_API, "/api/ps", timeout=5).get("models", [])
        names = [m.get("name") for m in ps if m.get("name")]
    except Exception:
        names = []
    if model and model not in names:
        names.append(model)
    freed = []
    for name in names:
        try:
            req = urllib.request.Request(
                OLLAMA_API + "/api/generate",
                data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20).read()
            freed.append(name)
        except Exception:
            pass
    return freed


def list_models():
    try:
        d = http_json(OLLAMA_API, "/api/tags", timeout=5)
        return [m["name"] for m in d.get("models", [])]
    except Exception:
        return []


# ================================================================ ComfyUI
def comfy_alive():
    try:
        http_json(COMFY_API, "/system_stats", timeout=3)
        return True
    except Exception:
        return False


def resolve_comfy_paths(comfy_dir):
    """兼容两种布局：
         A) 标准：<dir>/main.py + <dir>/python/pythonw.exe
         B) 绘世等整合包：<dir>/ComfyUI/main.py + <dir>/python/pythonw.exe
       返回 (main.py 所在目录, python 可执行文件)；找不到则 (None, None)
    """
    if not comfy_dir or not os.path.isdir(comfy_dir):
        return None, None
    main_dir = None
    for cand in (comfy_dir,
                 os.path.join(comfy_dir, "ComfyUI"),
                 os.path.join(comfy_dir, "ComfyUI-aki-v2"),
                 os.path.join(comfy_dir, "..", "ComfyUI")):
        cand = os.path.abspath(cand)
        if os.path.isfile(os.path.join(cand, "main.py")):
            main_dir = cand
            break
    if main_dir is None:
        try:
            for child in os.listdir(comfy_dir):
                cand = os.path.join(comfy_dir, child)
                if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "main.py")):
                    main_dir = cand
                    break
        except Exception:
            pass
    if main_dir is None:
        return None, None

    py = None
    for base in (comfy_dir, main_dir, os.path.dirname(main_dir)):
        for exe in ("pythonw.exe", "python.exe"):
            cand = os.path.join(os.path.abspath(base), "python", exe)
            if os.path.isfile(cand):
                py = cand
                break
        if py:
            break
    if py is None:
        py = shutil.which("python")
    return main_dir, py


def start_comfy(comfy_dir):
    main_dir, py = resolve_comfy_paths(comfy_dir)
    if not main_dir or not py:
        return False
    try:
        proc = subprocess.Popen([py, "main.py", "--preview-method", "none", "--disable-xformers"],
                                cwd=main_dir, creationflags=0x08000000,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for i in range(50):                      # 最多 100 秒
        time.sleep(2)
        if comfy_alive():
            return True
        if proc.poll() is not None and i > 3:   # 进程已退出，不再空等
            return False
    return False


def upload_image(path, name):
    import urllib.request
    data = open(path, "rb").read()
    b = "----up%d" % int(time.time() * 1000)
    ext = "png" if path.lower().endswith(".png") else "jpg"
    body = ("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
            "Content-Type: image/%s\r\n\r\n" % (b, name, ext)).encode() + data + \
           ("\r\n--%s\r\nContent-Disposition: form-data; name=\"image_type\"\r\n\r\ninput"
            "\r\n--%s\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
            "\r\n--%s--\r\n" % (b, b, b)).encode()
    req = urllib.request.Request(COMFY_API + "/upload/image", data=body,
                                 headers={"Content-Type": "multipart/form-data; boundary=" + b})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())["name"]


# ================================================================ 文本
def est_tokens(prompt):
    words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", prompt))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", prompt))
    punct = len(re.findall(r"[,()\[\]:]", prompt))
    return int(words * 1.3 + cjk * 1.5 + punct * 0.5)


def render(fields):
    parts = []
    for key in FIELDS:
        for item in fields.get(key, []) or []:
            t = re.sub(r"\s+", " ", str(item).strip().strip(",").lower())
            if t and t not in parts:
                parts.append(t)
    return ", ".join(parts)


# 弱化词 → 应改为（命中即视为翻译失真）
WEAK_WORDS = [
    ("making love", "sex"),
    ("intimate", "sex"),
    ("embrace", "原文动作（hug / 原动词）"),
    ("cuddle", "原文动作"),
    ("making out", "kissing"),
    ("chest", "breasts"),
    ("bust", "breasts"),
    ("curves", "breasts / hips"),
    ("undressed", "nude"),
    ("revealing", "nude / topless"),
    ("scantily clad", "topless / nude"),
    ("bare shoulders", "照原文（原文没提肩就别写）"),
    ("private area", "genitals"),
    ("private parts", "genitals"),
    ("lower body", "genitals"),
    ("down there", "genitals"),
    ("close together", "原文贴合动作（pressed / against）"),
    ("leaning", "pressed / lying（按原文）"),
    ("positioned", "原文动词"),
    ("resting on", "原文动词"),
]

FIELDS_CHECK = ("subject", "action", "camera", "clothing", "scene", "light", "style", "negative")


def check_weak(clean, sentence):
    """返回 [(弱化词, 建议, 出现位置), ...]
    male chest（男性的胸部）是正确英文，不判定为弱化。"""
    hits = []
    subj = " ".join(str(v) for v in (clean.get("subject") or [])).lower()
    for field in FIELDS_CHECK:
        val = clean.get(field)
        if not val:
            continue
        txt = val if isinstance(val, str) else " ".join(str(v) for v in val)
        low = txt.lower()
        for bad, good in WEAK_WORDS:
            if bad not in low:
                continue
            if bad == "chest" and _male_chest(low, subj):
                continue
            hits.append((bad, good, field))
    low_s = (sentence or "").lower()
    for bad, good in WEAK_WORDS:
        if bad in low_s:
            if bad == "chest" and _male_chest(low_s, subj):
                continue
            hits.append((bad, good, "sentence"))
    return hits


AUTO_FIX = [
    # (正则, 替换, 说明)
    (r"\bher chest\b", "her breasts", "chest→breasts"),
    (r"\bthe chest\b", "the breasts", "chest→breasts"),
    (r"\bchests\b", "breasts", "chests→breasts"),
    (r"\bintimate moment\b", "sex", "intimate→sex"),
    (r"\bmaking love\b", "having sex", "making love→sex"),
    (r"\bprivate area\b", "genitals", "private area→genitals"),
    (r"\bprivate parts\b", "genitals", "private parts→genitals"),
    (r"\bdown there\b", "genitals", "down there→genitals"),
    (r"\bclose together\b", "pressed against each other", "close together→贴合"),
    (r"\bscantily clad\b", "topless", "scantily clad→topless"),
    (r"\bmaking out\b", "kissing", "making out→kissing"),
]


def auto_fix_weak(clean, sentence):
    """按对照表做保守替换，返回 (clean, sentence, 修正过的词列表)"""
    import re as _re
    fixed = []

    def fix_text(txt):
        for pat, rep, note in AUTO_FIX:
            new = _re.sub(pat, rep, txt, flags=_re.IGNORECASE)
            if new != txt:
                txt = new
                if note not in fixed:
                    fixed.append(note)
        return txt

    for field in FIELDS_CHECK:
        val = clean.get(field)
        if not val:
            continue
        if isinstance(val, str):
            clean[field] = fix_text(val)
        else:
            clean[field] = [fix_text(str(v)) for v in val]
    if sentence:
        sentence = fix_text(sentence)
    return clean, sentence, fixed


def _male_chest(low, subj):
    """只有男性主体 + bare/his chest 这种写法才放行"""
    if not any(k in subj for k in ("1boy", "man", "male", "boy")):
        return False
    if any(k in subj for k in ("1girl", "woman", "female")):
        # 男女都有时，只有明确 "his chest" 才放行
        return bool("his chest" in low or "bare chest" in low)
    return bool("his chest" in low or "bare chest" in low or "man's chest" in low)


def parse_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        return json.loads(m.group(0))
    raise RuntimeError("模型没有输出 JSON")


def guard(fields, raw_text):
    """校验模型输出：剥中文、补默认、校验词表、按 75 token 裁剪"""
    warns = []
    if not isinstance(fields, dict):
        raise RuntimeError("输出不是 JSON 对象")

    def strip_cn(x):
        return re.sub(r"[\u4e00-\u9fff\u3000-\u303f]", "", str(x)).strip()

    clean = {}
    for k, v in fields.items():
        if k in ("glossary", "sentence"):
            continue
        if isinstance(v, list):
            clean[k] = [strip_cn(i) for i in v if strip_cn(i)]
        elif isinstance(v, str) and strip_cn(v):
            clean[k] = [strip_cn(v)]
    if re.search(r"[\u4e00-\u9fff]", raw_text):
        warns.append("模型输出混了中文，已剥离")

    if not clean.get("style"):
        clean["style"] = list(DEFAULT_STYLE)
        warns.append("缺 style，补默认质感词")
    if not clean.get("negative"):
        clean["negative"] = list(DEFAULT_NEG)
        warns.append("缺 negative，补默认负向提示词")

    # 句子 + 词表
    sentence = re.sub(r"\s+", " ", str(fields.get("sentence") or "")).strip()
    low_sentence = sentence.lower()
    FUNCTION_WORDS = {"a", "an", "the", "in", "on", "at", "of", "to", "and", "or", "is", "are",
                      "was", "were", "be", "been", "with", "by", "for", "from", "as", "that",
                      "this", "it", "its", "his", "her", "their", "he", "she", "they", "we", "you"}
    glossary, dropped, seen = [], 0, set()
    for g in (fields.get("glossary") or []):
        if not isinstance(g, dict):
            continue
        en = re.sub(r"\s+", " ", str(g.get("en") or "")).strip()
        cn = str(g.get("cn") or "").strip()
        if not en or not cn:
            continue
        if en.lower() in FUNCTION_WORDS:
            dropped += 1
            continue
        # 只过滤虚词，其余全部保留（词表越多越好，便于学习）
        if en.lower() in seen:
            continue
        seen.add(en.lower())
        glossary.append({"en": en, "cn": cn})
    if dropped:
        warns.append("词表里 %d 条虚词已跳过" % dropped)
    if not sentence:
        warns.append("模型没给英文句子")

    # 裁剪超长
    prompt = render(clean)
    cut = 0
    while est_tokens(prompt) > 75 and cut < 14:
        for key in ("style", "scene", "light", "clothing"):
            if len(clean.get(key, [])) > 1:
                clean[key].pop(); cut += 1
                break
        else:
            break
        prompt = render(clean)
    if cut:
        warns.append("超 75 token，裁掉 %d 条尾部标签" % cut)
    return clean, sentence, glossary, warns


# ================================================================ 图构建
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}


def is_link_type(t0):
    """ComfyUI 节点输入类型判定：非基本类型、且不是下拉列表 → 是连线"""
    if isinstance(t0, list):
        return False          # ["a","b",...] 是下拉控件
    if not isinstance(t0, str):
        return False
    return t0 not in WIDGET_TYPES


def object_info():
    return http_json(COMFY_API, "/object_info", timeout=180)


def ui_to_api(ui, info):
    """UI 格式工作流(nodes/links) → API 格式"""
    links = {l[0]: (str(l[1]), l[2]) for l in ui.get("links", [])}
    api = {}
    for node in ui.get("nodes", []):
        cls = node.get("type")
        if cls not in info or cls in ("Note", "Reroute", "PrimitiveNode"):
            continue
        nid = str(node["id"])
        spec = info[cls]["input"]
        order = []
        for name, s in list(spec.get("required", {}).items()) + list(spec.get("optional", {}).items()):
            t0 = s[0]
            if is_link_type(t0):
                order.append((name, "LINK"))
            else:
                order.append((name, "WIDGET"))
                if name in ("seed", "noise_seed"):
                    order.append(("__control__", "SKIP"))
        connected = {}
        for inp in node.get("inputs", []) or []:
            lid = inp.get("link")
            if lid is not None and lid in links:
                connected[inp["name"]] = [links[lid][0], links[lid][1]]
        wv = list(node.get("widgets_values") or [])
        inputs, wi = {}, 0
        for name, kind in order:
            if kind == "LINK":
                if name in connected:
                    inputs[name] = connected[name]
                continue
            if kind == "SKIP":
                wi += 1
                continue
            if name in connected:
                inputs[name] = connected[name]
                wi += 1
                continue
            if wi < len(wv):
                inputs[name] = wv[wi]
                wi += 1
        api[nid] = {"class_type": cls, "inputs": inputs,
                    "_meta": {"title": node.get("title", cls)}}
    return api


def load_workflow(path):
    data = json.load(open(path, encoding="utf-8"))
    if "nodes" in data:
        return ui_to_api(data, object_info()), "UI"
    return data, "API"


def inject(graph, img_name, prompt, neg, seed, width=None, height=None,
           lora_w=None, faceid_w=None, face_idx=None, disable_swap=False,
           face_count=1, restore=None, image_scale=None):
    """把输入图/提示词/种子写进工作流（与批量处理引擎同一套逻辑）"""
    first_done = False
    for nid in sorted(graph, key=lambda x: int(x) if x.isdigit() else 0):
        node = graph[nid]
        cls = node.get("class_type")
        ins = node.get("inputs", {})
        if cls == "LoadImage" and "image" in ins and not first_done:
            ins["image"] = img_name
            first_done = True
        if cls == "EmptyLatentImage":
            if width:
                ins["width"] = int(width)
            if height:
                ins["height"] = int(height)
    encs = [nid for nid in sorted(graph, key=lambda x: int(x) if x.isdigit() else 0)
            if graph[nid].get("class_type") in ("CLIPTextEncode", "CLIPTextEncodeSDXL")]
    if len(encs) >= 1:
        graph[encs[0]]["inputs"]["text"] = prompt
    if len(encs) >= 2:
        graph[encs[1]]["inputs"]["text"] = neg
    for nid, node in graph.items():
        ins = node.get("inputs", {})
        if isinstance(ins.get("seed"), int):
            ins["seed"] = seed
        if isinstance(ins.get("noise_seed"), int):
            ins["noise_seed"] = seed
        cls = node.get("class_type")
        # LoRA 权重（None = 保持工作流原值，不动）
        if cls == "LoraLoader" and lora_w is not None:
            if "strength_model" in ins:
                ins["strength_model"] = float(lora_w)
            if "strength_clip" in ins:
                ins["strength_clip"] = float(lora_w)
        # FaceID 权重
        if cls == "IPAdapterFaceID" and faceid_w is not None and "weight" in ins:
            ins["weight"] = float(faceid_w)
        # 放大节点：仅多耦合模式指定，单耦合保持工作流原值
        if cls == "ImageScale" and image_scale:
            ins["width"] = int(image_scale[0])
            ins["height"] = int(image_scale[1])
        # 耦合：序号 / 关闭 / 多数
        if cls == "ReActorFaceSwap":
            if disable_swap and "enabled" in ins:
                ins["enabled"] = False
            if restore is not None and "face_restore_visibility" in ins:
                ins["face_restore_visibility"] = float(restore)
            n = max(1, int(face_count or 1))
            if n > 1:
                idx = ",".join(str(i) for i in range(n))
                ins["input_faces_index"] = idx
                ins["source_faces_index"] = idx
            elif face_idx is not None and "input_faces_index" in ins:
                ins["input_faces_index"] = str(int(face_idx))
    return graph


def build_plain(prompt, neg, w, h, steps, cfg, sampler, scheduler, seed, ckpt, vae):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "3": {"class_type": "CLIPSetLastLayer", "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -2}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": neg}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": int(w), "height": int(h), "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler,
            "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["2", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "bench_tmp"}},
    }


def add_reactor(graph, source_image_name, swap_model="inswapper_128.onnx", restore=0.35):
    """在已有图上追加人物统一节点（9 号保存节点改从它取图）"""
    graph["20"] = {"class_type": "LoadImage", "inputs": {"image": source_image_name}}
    graph["21"] = {"class_type": "ReActorFaceSwap", "inputs": {
        "enabled": True, "input_image": ["8", 0], "source_image": ["20", 0],
        "swap_model": swap_model, "facedetection": "retinaface_resnet50",
        "face_restore_model": "GFPGANv1.4.pth", "face_restore_visibility": float(restore),
        "codeformer_weight": 0.5, "detect_gender_input": "no", "detect_gender_source": "no",
        "input_faces_index": "0", "source_faces_index": "0", "console_log_level": 0}}
    graph["9"]["inputs"]["images"] = ["21", 0]
    return graph


# ================================================================ 界面
class App:
    def __init__(self, root):
        self.root = root
        root.title("Musidora")
        root.geometry("1280x860")     # 先给个占位，建完界面后按真实需求重设
        ico = os.path.join(APP_DIR, "icon.ico")
        if os.path.exists(ico):
            try:
                root.iconbitmap(default=ico)
            except Exception:
                pass
        self.q = queue.Queue()
        self.busy = False
        self.stop_flag = False
        self.cfg = self.load_cfg()
        self.last = {"prompt": "", "neg": "", "sentence": "", "glossary": []}
        self._gloss_rows = []
        self._gloss_cn_hidden = False
        self._run_seq = 0
        self._mode_prev = "单耦合"

        self.theme_key = self.cfg.get("theme", "amber")
        if self.theme_key not in PALETTES:
            self.theme_key = "amber"
        self.palette = setup_style(root, self.theme_key)
        PAD = 14

        # ───── 顶栏 ─────
        bar = ttk.Frame(root, style="Bar.TFrame")
        bar.pack(fill="x")
        inner = ttk.Frame(bar, style="Bar.TFrame")
        inner.pack(fill="x", padx=PAD, pady=9)

        ttk.Label(inner, text="Musidora", style="Title.TLabel").pack(side="left")
        self.dot = ttk.Label(inner, text="●", style="DotOff.TLabel")
        self.dot.pack(side="left", padx=(10, 2))
        self.dot_txt = ttk.Label(inner, text="ComfyUI 检测中", style="BarHint.TLabel")
        self.dot_txt.pack(side="left")

        btn_close = ttk.Button(inner, text="关闭服务", style="Ghost.TButton",
                               command=self.shutdown_comfy)
        btn_close.pack(side="right")
        self.add_tip(btn_close,
            "关闭服务：\n"
            "· 同时结束 ComfyUI 与 Ollama 两个进程\n"
            "· 关掉后翻译、出图都会报服务不可用\n"
            "· 想恢复只能重新打开软件（启动时会自动拉起两个服务）\n"
            "· 只是想让出显存，用「卸载模式」即可，不用关服务")

        self.btn_theme = ttk.Button(inner, text="主题", style="Ghost.TButton",
                                    command=self.cycle_theme)
        self.btn_theme.pack(side="right", padx=6)
        btn_rules = ttk.Button(inner, text="规则", style="Ghost.TButton",
                               command=self.edit_rules)
        btn_rules.pack(side="right", padx=6)
        self.add_tip(btn_rules,
            "规则：打开 提示词规则.txt\n"
            "· 翻译模型按里面写的规则输出提示词与词表\n"
            "· 每次翻译都会重新读文件，改完立刻生效，不用重启\n"
            "· 想让它更懂你的说法，往末尾追加示例比加规则有效")
        self.temp = tk.DoubleVar(value=float(self.cfg.get("temp", 0.3)))
        ttk.Spinbox(inner, from_=0.0, to=1.2, increment=0.1, width=4,
                    textvariable=self.temp, style="Bar.TSpinbox").pack(side="right", padx=(2, 10))
        lbl_temp = ttk.Label(inner, text="温度", style="BarHint.TLabel")
        lbl_temp.pack(side="right")
        self.add_tip(lbl_temp,
            "温度：控制翻译模型的随机性\n"
            "· 0.1~0.3：稳定、按规则老实输出（推荐）\n"
            "· 0.6~0.9：更有创意，但可能不守格式\n"
            "· 「换个说法」按钮会固定用 0.9 重新翻一次")
        # 显存策略：下拉选择（与模型同样的样式）
        _vm = self.cfg.get("vram_mode", "按需")
        if _vm not in ("按需", "出图优先", "翻译优先"):     # 兼容旧配置
            _vm = "按需"
        self.vram_mode = tk.StringVar(value=_vm)
        self.vram_box = ttk.Combobox(inner, textvariable=self.vram_mode, width=9,
                                     values=["按需", "出图优先", "翻译优先"], state="readonly",
                                     style="Bar.TCombobox")
        self.vram_box.pack(side="right", padx=(0, 10))
        self.vram_box.configure(takefocus=0)      # 不要焦点白框
        lbl_vram = ttk.Label(inner, text="卸载模式", style="BarHint.TLabel")
        lbl_vram.pack(side="right", padx=(0, 4))
        self.add_tip(lbl_vram,
            "按需（默认）：\n"
            "· 显存够用时谁也不动，两个模型都不重载\n"
            "· 翻译显存不够 → 让 ComfyUI 让出\n"
            "· 出图显存不够 → 让翻译模型让出\n\n"
            "出图优先：\n"
            "· 出图前总是把翻译模型请出显存，出图始终最快\n"
            "· 翻译时不碰 ComfyUI\n"
            "· 代价：出图后再翻译要重新加载模型\n\n"
            "翻译优先：\n"
            "· 翻译前总是让 ComfyUI 让出显存，翻译始终全速\n"
            "· 出图前不动翻译模型\n"
            "· 代价：出图时显存紧张，出图会变慢")
        self.model = tk.StringVar(value=self.cfg.get("model", "qwen3-abliterated"))
        self.model_box = ttk.Combobox(inner, textvariable=self.model, width=22,
                                      values=[], style="Bar.TCombobox")
        self.model_box.pack(side="right", padx=(2, 6))
        lbl_model = ttk.Label(inner, text="模型", style="BarHint.TLabel")
        lbl_model.pack(side="right", padx=(0, 4))
        self.add_tip(lbl_model,
            "模型：负责把中文翻译成英文提示词的本地模型\n"
            "· 由 Ollama 提供，这里列出已安装的模型\n"
            "· 换模型后点右上角刷新可更新列表\n"
            "· 8B 级模型在 8 GB 显存上约 40~50 字/秒")

        # ───── 主体：左右两栏 ─────
        # 用一个容器统一用 grid 分配高度：上半 3 份、英语学习 4 份
        main = ttk.Frame(root)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        body = ttk.Frame(main)
        body.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=(10, 4))
        # 用经典 PanedWindow：支持双向拖动 + 每栏最小宽度
        pw = tk.PanedWindow(body, orient="horizontal", sashwidth=6,
                            sashrelief="flat", bd=0, bg=self.palette["bg"])
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, minsize=360)
        pw.add(right, minsize=320)
        self.pw = pw
        self._sash_busy = False
        def _sash_down(_e=None):
            self._sash_busy = True
            for k in ("1","2","3"):
                lw = getattr(self, "log%s" % k, None)
                if lw is not None:
                    try: lw.configure(wrap="none")   # 拖动时不重排文本，减少闪烁
                    except Exception: pass
        def _sash_up(_e=None):
            def finish():
                self._sash_busy = False
                for k in ("1", "2", "3"):
                    if getattr(self, "_pv_open", {}).get(k):
                        try:
                            getattr(self, "_pv_last_%s" % k, None)
                            self._fit_preview(k)
                        except Exception:
                            pass
                for k in ("1","2","3"):
                    lw = getattr(self, "log%s" % k, None)
                    if lw is not None:
                        try:
                            lw.configure(wrap="word")
                            lw.see("end")
                        except Exception:
                            pass
            try: self.root.after(220, finish)
            except Exception: finish()
        try:
            pw.bind("<ButtonPress-1>", _sash_down, add="+")
            pw.bind("<ButtonRelease-1>", _sash_up, add="+")
        except Exception:
            pass

        # ── 左：输入与提示词 ──
        card1 = ttk.Frame(left, style="Card.TFrame")
        card1.pack(fill="x")
        ttk.Label(card1, text="描述", style="CardTitle.TLabel").pack(anchor="w", padx=12, pady=(10, 0))
        self.cn = tk.Text(card1, height=3, wrap="word", relief="flat", bd=0,
                          bg=self.palette["field"], fg=self.palette["fg"], insertbackground=self.palette["accent"],
                          font=("Microsoft YaHei UI", 10), padx=10, pady=8,
                          highlightthickness=1, highlightbackground=self.palette["border"],
                          highlightcolor=self.palette["accent"])
        self.cn.pack(fill="x", padx=12, pady=(6, 8))
        self.cn.insert("1.0", self.cfg.get("last_cn", ""))

        rowb = ttk.Frame(card1)
        rowb.pack(fill="x", padx=12, pady=(0, 12))
        self.btn_tr = ttk.Button(rowb, text="翻译成提示词", style="Accent.TButton",
                                 command=self.translate)
        self.btn_tr.pack(side="left")
        ttk.Button(rowb, text="换个说法", style="Ghost.TButton",
                   command=lambda: self.translate(hot=True)).pack(side="left", padx=6)
        self.btn_stop_tr = ttk.Button(rowb, text="停止翻译", style="Ghost.TButton",
                                      width=8, command=self.stop_translate, state="disabled")
        self.btn_stop_tr.pack(side="left", padx=(6, 0))
        self.btn_blur = ttk.Button(rowb, text="隐藏", style="Ghost.TButton",
                                   command=self.toggle_blur)
        self.btn_blur.pack(side="left", padx=(6, 0))
        self.tok_lbl = ttk.Label(rowb, text="0 / 75", style="Hint.TLabel")
        self.tok_lbl.pack(side="right")
        self.time_lbl = ttk.Label(rowb, text="用时 —", style="Hint.TLabel")
        self.time_lbl.pack(side="right", padx=(0, 14))

        card2 = ttk.Frame(left, style="Card.TFrame")
        card2.pack(fill="both", expand=True, pady=(10, 0))
        head2 = ttk.Frame(card2)
        head2.pack(fill="x", padx=12, pady=(10, 0))
        ttk.Label(head2, text="正向提示词", style="CardTitle.TLabel").pack(side="left")
        ttk.Button(head2, text="复制", style="Ghost.TButton", width=5,
                   command=lambda: self.copy_box(self.pos)).pack(side="right")
        self.pos = tk.Text(card2, height=4, wrap="word", relief="flat", bd=0,
                           bg=self.palette["field"], fg=self.palette["fg"], insertbackground=self.palette["accent"],
                           font=("Consolas", 10), padx=10, pady=8,
                           highlightthickness=1, highlightbackground=self.palette["border"],
                           highlightcolor=self.palette["accent"])
        self.pos.pack(fill="both", expand=True, padx=12, pady=(6, 8))
        self.pos.insert("1.0", self.cfg.get("last_prompt", ""))
        self.pos.bind("<KeyRelease>", lambda e: self.update_token())

        head3 = ttk.Frame(card2)
        head3.pack(fill="x", padx=12)
        ttk.Label(head3, text="负向提示词", style="CardTitle.TLabel").pack(side="left")
        ttk.Button(head3, text="复制", style="Ghost.TButton", width=5,
                   command=lambda: self.copy_box(self.neg)).pack(side="right")
        self.neg = tk.Text(card2, height=2, wrap="word", relief="flat", bd=0,
                           bg=self.palette["field"], fg=self.palette["fg"], insertbackground=self.palette["accent"],
                           font=("Consolas", 10), padx=10, pady=8,
                           highlightthickness=1, highlightbackground=self.palette["border"],
                           highlightcolor=self.palette["accent"])
        self.neg.pack(fill="x", padx=12, pady=(6, 12))
        self.neg.insert("1.0", self.cfg.get("last_neg", ", ".join(DEFAULT_NEG)))

        # ── 右：参数卡 ──
        nb = ttk.Notebook(right, style="Card.TNotebook")
        nb.pack(fill="both", expand=True)
        self.tab_single = ttk.Frame(nb, style="TabBody.TFrame")
        self.tab_batch = ttk.Frame(nb, style="TabBody.TFrame")
        self.tab_map = ttk.Frame(nb, style="TabBody.TFrame")
        nb.add(self.tab_single, text="生成")
        nb.add(self.tab_batch, text="耦合")
        nb.add(self.tab_map, text="映射")
        self.build_single_tab(self.tab_single)
        self.build_batch_tab(self.tab_batch)
        self.build_map_tab(self.tab_map)

        # ───── 英语学习：放进左栏，和描述/提示词共享左半空间 ─────
        lf = ttk.Frame(left, style="Card.TFrame")
        lf.pack(fill="both", expand=True, pady=(10, 0))
        lh = ttk.Frame(lf)
        lh.pack(fill="x", padx=12, pady=(10, 0))
        ttk.Label(lh, text="英语学习", style="CardTitle.TLabel").pack(side="left")
        ttk.Button(lh, text="导出", style="Ghost.TButton", width=5,
                   command=self.export_card).pack(side="right")
        ttk.Button(lh, text="朗读", style="Ghost.TButton", width=5,
                   command=self.speak).pack(side="right", padx=6)
        ttk.Button(lh, text="复制词表", style="Ghost.TButton",
                   command=self.copy_gloss).pack(side="right")
        ttk.Button(lh, text="复制句子", style="Ghost.TButton",
                   command=lambda: self.copy_box(self.sentence)).pack(side="right", padx=6)
        self.btn_gloss = ttk.Button(lh, text="隐藏词义", style="Ghost.TButton",
                                    command=self.toggle_gloss_cn)
        self.btn_gloss.pack(side="right", padx=(0, 6))
        # 左：句子   右：词表
        split = ttk.Frame(lf)
        split.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        split.columnconfigure(0, weight=2, minsize=300)
        split.columnconfigure(1, weight=3, minsize=380)
        split.rowconfigure(0, weight=1)

        left_l = ttk.Frame(split)
        left_l.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.sentence = tk.Text(left_l, height=4, wrap="word", relief="flat", bd=0,
                                bg=self.palette["card"], fg=self.palette["fg"],
                                insertbackground=self.palette["accent"],
                                font=("Georgia", 11), padx=8, pady=6,
                                highlightthickness=0)
        self.sentence.pack(fill="both", expand=True)
        self.sentence.insert("1.0", self.cfg.get("last_sentence", ""))

        right_l = ttk.Frame(split)
        right_l.grid(row=0, column=1, sticky="nsew")
        self.gloss = WrapTable(right_l, self.palette)   # 可换行

        bar2 = ttk.Frame(root)
        bar2.pack(fill="x", padx=PAD, pady=(0, 8))
        self.dot2 = ttk.Label(bar2, text="●", style="DotOff.TLabel", background=self.palette["card"])
        self.dot2.pack(side="left")
        self.svc_lbl = ttk.Label(bar2, text="Ollama 检测中", style="Status.TLabel")
        self.svc_lbl.pack(side="left", padx=(4, 0))
        self.status = ttk.Label(bar2, text="", style="Status.TLabel", anchor="e")
        self.status.pack(side="right")

        try:
            self.btn_theme.config(text="主题：" + self.palette["name"])
        except Exception:
            pass
        self.fit_window()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_token()
        # 启动时默认隐藏中文描述（内容仍在，点「显示」可看）
        try:
            if self.cn.get("1.0", "end").strip():
                self.toggle_blur()
        except Exception:
            pass
        threading.Thread(target=self.boot, daemon=True).start()
        self.root.after(8000, self.periodic_check)
        self.pump()

    # ---------------- 浮层提示
    def add_tip(self, widget, text):
        """鼠标悬停显示说明"""
        tip = {"win": None}
        def show(_e=None):
            if tip["win"] is not None:
                return
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry("+%d+%d" % (x, y))
            lbl = tk.Label(win, text=text, justify="left", bg="#1B1F27", fg="#E8EAED",
                           font=("Microsoft YaHei UI", 9), padx=10, pady=8,
                           relief="solid", bd=1)
            lbl.pack()
            tip["win"] = win
        def hide(_e=None):
            if tip["win"] is not None:
                tip["win"].destroy(); tip["win"] = None
        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def tip_label(self, parent, text):
        """一个小问号，悬停出说明"""
        lbl = ttk.Label(parent, text=" ? ", style="Hint.TLabel", cursor="question_arrow")
        self.add_tip(lbl, text)
        return lbl

    def _sync_seed_ui(self, which):
        """勾了随机 → 输入框只读（里面显示最近实际用过的种子）"""
        try:
            if which == "1":
                st = "readonly" if self.seed_rand.get() else "normal"
                self.seed_entry.configure(state=st)
            else:
                st = "readonly" if self.seed2_rand.get() else "normal"
                self.seed2_entry.configure(state=st)
        except Exception:
            pass

    def set_seed_display(self, which, seed):
        """把实际用过的种子写回界面"""
        try:
            if which == "1":
                self.seed.set(int(seed))
            else:
                self.seed2.set(int(seed))
        except Exception:
            pass

    # ---------------- 出图预览
    def make_preview(self, box, key):
        """在日志右侧建一个默认隐藏的预览面板（横向占位，不额外占高度）"""
        logw = getattr(self, "log%s" % key, None)
        panel = tk.Frame(box, bg=self.palette["card"], highlightthickness=1,
                         highlightbackground=self.palette["border"])
        img_lbl = tk.Label(panel, bg=self.palette["card"], text="出图后这里显示预览",
                           fg=self.palette["hint"], font=("Microsoft YaHei UI", 9))
        img_lbl.pack(fill="both", expand=True, padx=6, pady=6)
        info = tk.Label(panel, bg=self.palette["card"], text="", fg=self.palette["hint"],
                        font=("Microsoft YaHei UI", 9), wraplength=280)
        info.pack(fill="x", padx=6, pady=(0, 6))
        panel.pack_forget()
        if logw is not None:
            logw.pack_configure(side="left", fill="both", expand=True)
        setattr(self, "pv_panel_%s" % key, panel)
        setattr(self, "pv_img_%s" % key, img_lbl)
        setattr(self, "pv_info_%s" % key, info)
        setattr(self, "pv_path_%s" % key, "")
        img_lbl.bind("<Double-Button-1>", lambda e, k=key: self.open_preview(k))
        img_lbl.bind("<Configure>", lambda e, k=key: self._schedule_fit(k))
        info.bind("<Double-Button-1>", lambda e, k=key: self.open_preview(k))
        return panel

    def toggle_preview(self, key):
        """预览覆盖日志：同一块区域切换显示"""
        panel = getattr(self, "pv_panel_%s" % key, None)
        btn = getattr(self, "pv_btn_%s" % key, None)
        logw = getattr(self, "log%s" % key, None)
        if panel is None:
            return
        opened = getattr(self, "_pv_open", None)
        if opened is None:
            opened = self._pv_open = {}
        info = getattr(self, "pv_packinfo_%s" % key, None)
        if opened.get(key):
            panel.pack_forget()
            prog = getattr(self, "prog%s" % key, None)
            if prog is not None:
                prog.pack(fill="x", pady=(4, 8))
            if logw is not None:
                logw.pack(**(info or {"fill": "both", "expand": True}))
            opened[key] = False
            if btn is not None:
                btn.config(text="预览")
        else:
            if logw is not None:
                if info is None:
                    info = logw.pack_info()
                    info.pop("in", None)
                    setattr(self, "pv_packinfo_%s" % key, info)
                logw.pack_forget()
            prog = getattr(self, "prog%s" % key, None)
            if prog is not None:
                prog.pack_forget()
            panel.pack(fill="both", expand=True)
            opened[key] = True
            if btn is not None:
                btn.config(text="收起")
            self._fit_preview(key, force=True)
        # 状态不写配置，下次启动仍为隐藏

    def show_preview(self, key, path, idx=0, total=0):
        img_lbl = getattr(self, "pv_img_%s" % key, None)
        info = getattr(self, "pv_info_%s" % key, None)
        if img_lbl is None or not path or not os.path.exists(path):
            return
        setattr(self, "pv_path_%s" % key, path)
        setattr(self, "pv_src_%s" % key, path)
        self.__dict__["_pv_last_%s" % key] = None      # 换图了，尺寸缓存作废
        self._fit_preview(key)
        if info is not None:
            tag = ("%d/%d ｜ " % (idx, total)) if total else ""
            info.config(text=tag.rstrip(" ｜"))

    def _schedule_fit(self, key):
        """尺寸变化时延迟重绘，避免拖动分隔条时抖动"""
        job = getattr(self, "_pv_job", None)
        if job is None:
            job = self._pv_job = {}
        old = job.get(key)
        if old is not None:
            try:
                self.root.after_cancel(old)
            except Exception:
                pass
        if getattr(self, "_sash_busy", False):
            job[key] = None
            return
        job[key] = self.root.after(200, lambda k=key: self._fit_preview(k, force=True))

    def _fit_preview(self, key, force=False):
        """按面板当前尺寸等比缩放，保证完整显示且不溢出"""
        img_lbl = getattr(self, "pv_img_%s" % key, None)
        path = getattr(self, "pv_src_%s" % key, "")
        if img_lbl is None or not path or not os.path.exists(path) or not HAS_PIL:
            return
        try:
            w = max(80, img_lbl.winfo_width() - 12)
            h = max(80, img_lbl.winfo_height() - 12)
            if w < 80 or h < 80:
                return
            last = getattr(self, "_pv_last_%s" % key, None)
            if not force and last and abs(last[0] - w) <= 14 and abs(last[1] - h) <= 14:
                return
            self.__dict__["_pv_last_%s" % key] = (w, h)
            im = Image.open(path)
            im.thumbnail((w, h))
            photo = ImageTk.PhotoImage(im)
            img_lbl.config(image=photo, text="")
            img_lbl.image = photo
        except Exception:
            pass

    def open_preview(self, key):
        path = getattr(self, "pv_path_%s" % key, "")
        if path and os.path.exists(path):
            try:
                os.startfile(path)
            except Exception:
                pass

    def build_single_tab(self, f):
        box = ttk.Frame(f, style="TabBody.TFrame")
        box.pack(fill="both", expand=True, padx=16, pady=12)

        # 输出目录
        ttk.Label(box, text="输出到", style="CardTitle.TLabel").pack(anchor="w")
        r = ttk.Frame(box, style="TabBody.TFrame"); r.pack(fill="x", pady=(4, 10))
        self.out1 = tk.StringVar(value=self.cfg.get("out_single", os.path.join(APP_DIR, "出图")))
        ttk.Entry(r, textvariable=self.out1).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="选择", style="Ghost.TButton", width=4,
                   command=lambda: self.pick_dir(self.out1)).pack(side="left", padx=(4, 0))
        ttk.Button(r, text="打开", style="Ghost.TButton", width=4,
                   command=lambda: self.open_dir(self.out1)).pack(side="left", padx=(4, 0))

        # 尺寸
        ttk.Label(box, text="尺寸", style="CardTitle.TLabel").pack(anchor="w")
        r2 = ttk.Frame(box, style="TabBody.TFrame"); r2.pack(fill="x", pady=(4, 10))
        self.w = tk.IntVar(value=int(self.cfg.get("w", 512)))
        self.h = tk.IntVar(value=int(self.cfg.get("h", 768)))
        ttk.Combobox(r2, textvariable=self.w, values=[512, 576, 640, 768, 832, 1024],
                     width=6, state="readonly").pack(side="left")
        ttk.Label(r2, text="×", style="CardTitle.TLabel").pack(side="left", padx=6)
        ttk.Combobox(r2, textvariable=self.h, values=[512, 640, 768, 1024, 1152, 1216],
                     width=6, state="readonly").pack(side="left")

        # 采样
        ttk.Label(box, text="采样", style="CardTitle.TLabel").pack(anchor="w", pady=(2, 0))
        r3 = ttk.Frame(box, style="TabBody.TFrame"); r3.pack(fill="x", pady=(4, 10))
        lbl_steps = ttk.Label(r3, text="步数", style="Hint.TLabel")
        lbl_steps.pack(side="left")
        self.add_tip(lbl_steps, "步数：采样迭代次数\n· 越高细节越多，耗时线性增加\n· SD1.5 常用 25~40；低于 20 容易糊，高于 50 收益很小")
        self.steps = tk.IntVar(value=int(self.cfg.get("steps", 32)))
        ttk.Spinbox(r3, from_=8, to=80, width=4, textvariable=self.steps).pack(side="left", padx=(4, 10))
        lbl_cfg = ttk.Label(r3, text="CFG", style="Hint.TLabel")
        lbl_cfg.pack(side="left")
        self.add_tip(lbl_cfg, "CFG：提示词的影响强度\n· 3~5 写实自然\n· 5.5~8 更听话，但颜色和对比容易过\n· 高于 9 容易糊、噪点明显")
        self.cfgv = tk.DoubleVar(value=float(self.cfg.get("cfg", 5.5)))
        ttk.Spinbox(r3, from_=1.0, to=15.0, increment=0.5, width=4,
                    textvariable=self.cfgv).pack(side="left", padx=(4, 10))

        r4 = ttk.Frame(box, style="TabBody.TFrame"); r4.pack(fill="x", pady=(0, 10))
        self.sampler = tk.StringVar(value=self.cfg.get("sampler", SAMPLERS[0]))
        cb_sampler = ttk.Combobox(r4, textvariable=self.sampler, values=SAMPLERS, width=14,
                                  state="readonly")
        cb_sampler.pack(side="left")
        self.add_tip(cb_sampler, "采样器：每一步怎么去噪\n· dpmpp_2m_sde 质感稳定、细节好（推荐）\n· dpmpp_2m 稍快\n· euler_ancestral 更随机、风格化\n· uni_pc 少步数也能出效果")
        self.scheduler = tk.StringVar(value=self.cfg.get("scheduler", SCHEDULERS[0]))
        cb_sched = ttk.Combobox(r4, textvariable=self.scheduler, values=SCHEDULERS, width=10,
                                state="readonly")
        cb_sched.pack(side="left", padx=6)
        self.add_tip(cb_sched,
            "调度器：每步噪声下降的曲线\n"
            "· karras：末段降得快，细节利落（推荐）\n"
            "· normal：默认曲线，平稳\n"
            "· exponential：前段变化大，出片偏柔\n"
            "· sgm_uniform：SDXL 风格，SD1.5 也可用")

        # 种子与张数
        r5 = ttk.Frame(box, style="TabBody.TFrame"); r5.pack(fill="x", pady=(0, 12))
        lbl_seed1 = ttk.Label(r5, text="种子", style="Hint.TLabel")
        lbl_seed1.pack(side="left")
        self.seed = tk.IntVar(value=int(self.cfg.get("seed", 123456)))
        self.seed_rand = tk.BooleanVar(value=True)          # 默认随机
        self.seed_entry = ttk.Entry(r5, textvariable=self.seed, width=11)
        self.seed_entry.pack(side="left", padx=(4, 2))
        self.seed_chk = ttk.Checkbutton(r5, text="随机", variable=self.seed_rand,
                                        style="Rand.TCheckbutton",
                                        command=lambda: self._sync_seed_ui("1"))
        self.seed_chk.pack(side="left", padx=(0, 12))
        self.seed_entry.configure(state="readonly")
        self.add_tip(lbl_seed1,
            "种子：固定数字可复现同一张图\n"
            "· 勾「随机」= 每次都用新种子（默认）\n"
            "· 取消勾选后可自己填数字，框里显示的就是下次要用的种子\n"
            "· 每次出图后这里会更新成实际用过的种子")
        ttk.Label(r5, text="张数", style="Hint.TLabel").pack(side="left")
        self.times1 = tk.IntVar(value=int(self.cfg.get("times1", 1)))
        ttk.Spinbox(r5, from_=1, to=50, width=4, textvariable=self.times1).pack(side="left", padx=4)

        # 运行
        r6 = ttk.Frame(box, style="TabBody.TFrame"); r6.pack(fill="x", pady=(4, 8))
        self.pv_btn_1 = ttk.Button(r6, text="预览", style="Ghost.TButton", width=6,
                                   command=lambda: self.toggle_preview("1"))
        self.btn_gen1 = ttk.Button(r6, text="开始生成", style="Accent.TButton", command=self.gen_single)
        self.btn_gen1.pack(side="left", fill="x", expand=True)
        self.btn_stop1 = ttk.Button(r6, text="停止", style="Ghost.TButton", width=6,
                                    command=self.stop, state="disabled")
        self.btn_stop1.pack(side="left", padx=(6, 0))
        self.pv_btn_1.pack(side="left", padx=(6, 0))

        self.prog1 = ttk.Progressbar(box, mode="determinate")
        self.prog1.pack(fill="x", pady=(4, 8))
        self.log1 = tk.Text(box, height=3, wrap="word", state="disabled", relief="flat", bd=0,
                            font=("Consolas", 9), bg=self.palette["card"], fg=self.palette["hint"],
                            padx=8, pady=6, highlightthickness=1, highlightbackground=self.palette["border"])
        self.log1.pack(fill="both", expand=True)
        self.make_preview(box, "1")

    def on_mode_change(self, *_):
        """切换模式时，把当前尺寸存回旧模式，再载入新模式的值"""
        old_key = "w2" if getattr(self, "_mode_prev", "单耦合") == "单耦合" else "mw2"
        old_key_h = "h2" if getattr(self, "_mode_prev", "单耦合") == "单耦合" else "mh2"
        try:
            self.cfg[old_key] = int(self.w2.get())
            self.cfg[old_key.replace("w", "h", 1) if old_key.startswith("w") else old_key_h] = int(self.h2.get())
        except Exception:
            pass
        cur = self.couple_mode.get()
        k = "w2" if cur == "单耦合" else "mw2"
        kh = "h2" if cur == "单耦合" else "mh2"
        try:
            self.w2.set(int(self.cfg.get(k, 512)))
            self.h2.set(int(self.cfg.get(kh, 768)))
        except Exception:
            pass
        try:
            self._restore_touched = False
            self.restore_w.set(0.75 if cur == "单耦合" else 0.5)
            self._restore_touched = False
        except Exception:
            pass
        # 目录也跟着换成该模式自己的
        try:
            prev_multi = getattr(self, "_mode_prev", "单耦合") == "多耦合"
            self.cfg["msrc_dir" if prev_multi else "src_dir"] = self.src_dir.get()
            self.cfg["mout_batch" if prev_multi else "out_batch"] = self.out2.get()
            s_def = os.path.join(APP_DIR, "多耦合源" if cur == "多耦合" else "耦合源")
            o_def = os.path.join(APP_DIR, "多耦合输出" if cur == "多耦合" else "耦合输出")
            self.src_dir.set(self.cfg.get("msrc_dir" if cur == "多耦合" else "src_dir", "") or s_def)
            self.out2.set(self.cfg.get("mout_batch" if cur == "多耦合" else "out_batch", "") or o_def)
        except Exception:
            pass
        self._mode_prev = cur
        try:
            self.save_cfg()
        except Exception:
            pass

    def build_batch_tab(self, f):
        box = ttk.Frame(f, style="TabBody.TFrame")
        box.pack(fill="both", expand=True, padx=16, pady=12)

        ttk.Label(box, text="模式", style="CardTitle.TLabel").pack(anchor="w")
        rm0 = ttk.Frame(box, style="TabBody.TFrame"); rm0.pack(fill="x", pady=(4, 10))
        self.couple_mode = tk.StringVar(value="单耦合")   # 启动固定单耦合，不记忆
        self.mode_box = ttk.Combobox(rm0, textvariable=self.couple_mode, width=8,
                                     state="readonly", values=["单耦合", "多耦合"])
        self.mode_box.pack(side="left")
        self.mode_box.bind("<<ComboboxSelected>>", self.on_mode_change)



        _multi = self.couple_mode.get() == "多耦合"
        for label, var_name, cfg_key, extra in (
                ("源目录", "src_dir", "msrc_dir" if _multi else "src_dir", False),
                ("输出目录", "out2", "mout_batch" if _multi else "out_batch", True)):
            ttk.Label(box, text=label, style="CardTitle.TLabel").pack(anchor="w")
            r = ttk.Frame(box, style="TabBody.TFrame"); r.pack(fill="x", pady=(4, 10))
            var = tk.StringVar(value=self.cfg.get(cfg_key, ""))
            setattr(self, var_name, var)
            e = ttk.Entry(r, textvariable=var, width=18)
            e.pack(side="left", fill="x", expand=True)
            ttk.Button(r, text="选择", style="Ghost.TButton", width=4,
                       command=lambda v=var: self.pick_dir(v)).pack(side="left", padx=(4, 0))
            ttk.Button(r, text="打开", style="Ghost.TButton", width=4,
                       command=lambda v=var: self.open_dir(v)).pack(side="left", padx=(4, 0))

        ttk.Label(box, text="数量与尺寸", style="CardTitle.TLabel").pack(anchor="w")

        # 第一行：每张次数 · 尺寸 · 倍率
        r3 = ttk.Frame(box, style="TabBody.TFrame"); r3.pack(fill="x", pady=(4, 4))
        lbl_times = ttk.Label(r3, text="每张", style="Hint.TLabel")
        lbl_times.pack(side="left")
        self.add_tip(lbl_times,
            "每张次数：每张源图生成几张\n"
            "· 种子随机，所以每张都不一样\n"
            "· 张数越多耗时按比例增加")
        self.times2 = tk.IntVar(value=int(self.cfg.get("times2", 1)))
        ttk.Spinbox(r3, from_=1, to=20, width=3, textvariable=self.times2).pack(side="left", padx=(4, 14))

        lbl_size = ttk.Label(r3, text="尺寸", style="Hint.TLabel")
        lbl_size.pack(side="left")
        self.add_tip(lbl_size, "尺寸：第一遍出图的草图大小，成品 = 尺寸 × 倍率")
        _w_key = "w2" if self.couple_mode.get() == "单耦合" else "mw2"
        _h_key = "h2" if self.couple_mode.get() == "单耦合" else "mh2"
        self.w2 = tk.IntVar(value=int(self.cfg.get(_w_key, 512)))
        self.h2 = tk.IntVar(value=int(self.cfg.get(_h_key, 768)))
        ttk.Combobox(r3, textvariable=self.w2, values=[384, 448, 512, 576, 640, 768],
                     width=5, state="readonly").pack(side="left", padx=(4, 2))
        ttk.Label(r3, text="×", style="CardTitle.TLabel").pack(side="left")
        ttk.Combobox(r3, textvariable=self.h2, values=[512, 640, 768, 896, 1024],
                     width=5, state="readonly").pack(side="left", padx=(2, 14))

        lbl_scale = ttk.Label(r3, text="倍率", style="Hint.TLabel")
        lbl_scale.pack(side="left")
        self.add_tip(lbl_scale,
            "成品倍率：第二遍重绘的放大倍数\n"
            "· 成品 = 尺寸 × 倍率\n"
            "· 1.5 = 原配方，512×768 → 768×1152\n"
            "· 1.0 等于放弃重绘，观感偏软\n"
            "· 2.0 更大更清晰，耗时约翻倍")
        self.scale_x = tk.DoubleVar(value=float(self.cfg.get("scale_x", 1.5)))
        ttk.Combobox(r3, textvariable=self.scale_x, values=[1.0, 1.5, 2.0],
                     width=4, state="readonly").pack(side="left", padx=(4, 0))

        # 第二行：种子 · 随机
        r3b = ttk.Frame(box, style="TabBody.TFrame"); r3b.pack(fill="x", pady=(0, 10))
        lbl_seed2 = ttk.Label(r3b, text="种子", style="Hint.TLabel")
        lbl_seed2.pack(side="left")
        self.seed2 = tk.IntVar(value=int(self.cfg.get("seed2", 123456)))
        self.seed2_rand = tk.BooleanVar(value=True)
        self.seed2_entry = ttk.Entry(r3b, textvariable=self.seed2, width=12)
        self.seed2_entry.pack(side="left", padx=(4, 2))
        self.seed2_chk = ttk.Checkbutton(r3b, text="随机", variable=self.seed2_rand,
                                         style="Rand.TCheckbutton",
                                         command=lambda: self._sync_seed_ui("2"))
        self.seed2_chk.pack(side="left")
        self.seed2_entry.configure(state="readonly")
        self.add_tip(lbl_seed2,
            "种子：固定数字可复现同一张图\n"
            "· 勾「随机」= 每次都用新种子（默认）\n"
            "· 取消勾选后可自己填，同参数重复跑会命中缓存（几秒出图）\n"
            "· 和「生成」页的种子各自独立")

        ttk.Label(box, text="工作流", style="CardTitle.TLabel").pack(anchor="w")
        r4 = ttk.Frame(box, style="TabBody.TFrame"); r4.pack(fill="x", pady=(4, 10))
        self.wf_name = tk.StringVar(value=self.cfg.get("wf_name", "change_v3_reactor.json"))
        self.wf_box = ttk.Combobox(r4, textvariable=self.wf_name, width=26,
                                   values=self.list_workflows(), state="readonly")
        self.wf_box.pack(side="left")
        ttk.Button(r4, text="刷新", style="Ghost.TButton", width=5,
                   command=self.refresh_workflows).pack(side="left", padx=6)

        ttk.Label(box, text="耦合参数", style="CardTitle.TLabel").pack(anchor="w")
        r5 = ttk.Frame(box, style="TabBody.TFrame"); r5.pack(fill="x", pady=(4, 12))
        lbl_lora = ttk.Label(r5, text="LoRA", style="Hint.TLabel")
        lbl_lora.pack(side="left")
        self.add_tip(lbl_lora,
            "LoRA：面部特征的权重\n"
            "· 0.5~0.7 常用；越高越像参考，但姿势容易僵\n"
            "· 0 等于不用 LoRA")
        self.lora_w = tk.DoubleVar(value=float(self.cfg.get("lora_w", 0.6)))
        ttk.Spinbox(r5, from_=0.0, to=1.5, increment=0.05, width=4,
                    textvariable=self.lora_w).pack(side="left", padx=(4, 10))
        lbl_fid = ttk.Label(r5, text="FaceID", style="Hint.TLabel")
        lbl_fid.pack(side="left")
        self.add_tip(lbl_fid,
            "FaceID：参考图特征的注入强度\n"
            "· 0.6~0.9 常用；越高越锁死这张脸\n"
            "· 太高（>1.1）会让所有图都长一样")
        self.faceid_w = tk.DoubleVar(value=float(self.cfg.get("faceid_w", 0.8)))
        ttk.Spinbox(r5, from_=0.0, to=1.5, increment=0.05, width=4,
                    textvariable=self.faceid_w).pack(side="left", padx=(4, 10))
        lbl_res = ttk.Label(r5, text="修复强度", style="Hint.TLabel")
        lbl_res.pack(side="left")
        self.add_tip(lbl_res,
            "修复强度：特征迁移后修复模型的混合比例\n"
            "· 0.75 是工作流默认值\n"
            "· 0.35~0.5 更贴近原图，皮肤质感更真\n"
            "· 高于 0.8 会明显磨皮、发假")
        _init_restore = 0.75 if self.couple_mode.get() == "单耦合" else float(self.cfg.get("restore", 0.5))
        self.restore_w = tk.DoubleVar(value=float(_init_restore))
        self._restore_touched = False
        ttk.Spinbox(r5, from_=0.0, to=1.0, increment=0.05, width=4,
                    textvariable=self.restore_w).pack(side="left", padx=(4, 10))
        self.restore_w.trace_add("write", lambda *a: setattr(self, "_restore_touched", True))
        ttk.Button(r5, text="默认", style="Ghost.TButton", width=4,
                   command=self.reset_people).pack(side="left")

        r6 = ttk.Frame(box, style="TabBody.TFrame"); r6.pack(fill="x", pady=(4, 8))
        self.pv_btn_2 = ttk.Button(r6, text="预览", style="Ghost.TButton", width=6,
                                   command=lambda: self.toggle_preview("2"))
        self.btn_gen2 = ttk.Button(r6, text="开始耦合", style="Accent.TButton", command=self.gen_batch)
        self.btn_gen2.pack(side="left", fill="x", expand=True)
        self.btn_stop2 = ttk.Button(r6, text="停止", style="Ghost.TButton", width=6,
                                    command=self.stop, state="disabled")
        self.btn_stop2.pack(side="left", padx=(6, 0))
        self.pv_btn_2.pack(side="left", padx=(6, 0))

        self.prog2 = ttk.Progressbar(box, mode="determinate")
        self.prog2.pack(fill="x", pady=(4, 8))
        self.log2 = tk.Text(box, height=3, wrap="word", state="disabled", relief="flat", bd=0,
                            font=("Consolas", 9), bg=self.palette["card"], fg=self.palette["hint"],
                            padx=8, pady=6, highlightthickness=1, highlightbackground=self.palette["border"])
        self.log2.pack(fill="both", expand=True)
        self.make_preview(box, "2")

    def build_map_tab(self, f):
        box = ttk.Frame(f, style="TabBody.TFrame")
        box.pack(fill="both", expand=True, padx=16, pady=12)

        # 母版（要被映射出去的那张图）
        ttk.Label(box, text="母版", style="CardTitle.TLabel").pack(anchor="w")
        r0 = ttk.Frame(box, style="TabBody.TFrame"); r0.pack(fill="x", pady=(4, 10))
        self.map_ref = tk.StringVar(value=self.cfg.get("map_ref", ""))
        ttk.Entry(r0, textvariable=self.map_ref).pack(side="left", fill="x", expand=True)
        ttk.Button(r0, text="选择", style="Ghost.TButton", width=4,
                   command=lambda: self.pick_file(self.map_ref)).pack(side="left", padx=(4, 0))

        # 目标目录（要处理的图）
        ttk.Label(box, text="目标目录", style="CardTitle.TLabel").pack(anchor="w")
        r1 = ttk.Frame(box, style="TabBody.TFrame"); r1.pack(fill="x", pady=(4, 10))
        self.map_src = tk.StringVar(value=self.cfg.get("map_src", ""))
        ttk.Entry(r1, textvariable=self.map_src).pack(side="left", fill="x", expand=True)
        ttk.Button(r1, text="选择", style="Ghost.TButton", width=4,
                   command=lambda: self.pick_dir(self.map_src)).pack(side="left", padx=(4, 0))
        ttk.Button(r1, text="打开", style="Ghost.TButton", width=4,
                   command=lambda: self.open_dir(self.map_src)).pack(side="left", padx=(4, 0))

        # 输出目录
        ttk.Label(box, text="输出目录", style="CardTitle.TLabel").pack(anchor="w")
        r2 = ttk.Frame(box, style="TabBody.TFrame"); r2.pack(fill="x", pady=(4, 10))
        self.map_out = tk.StringVar(value=self.cfg.get("map_out", ""))
        ttk.Entry(r2, textvariable=self.map_out).pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="选择", style="Ghost.TButton", width=4,
                   command=lambda: self.pick_dir(self.map_out)).pack(side="left", padx=(4, 0))
        ttk.Button(r2, text="打开", style="Ghost.TButton", width=4,
                   command=lambda: self.open_dir(self.map_out)).pack(side="left", padx=(4, 0))

        # 映射强度 / 修复模型
        r4 = ttk.Frame(box, style="TabBody.TFrame"); r4.pack(fill="x", pady=(2, 12))
        lbl_mstr = ttk.Label(r4, text="映射强度", style="CardTitle.TLabel")
        lbl_mstr.pack(side="left")
        self.add_tip(lbl_mstr,
            "映射强度：母版特征压到目标图上的力度（0~1）\n"
            "· 0.30~0.40 自然，皮肤纹理保留最好\n"
            "· 0.50~0.65 更像母版，细节会被磨平一些\n"
            "· 高于 0.7 容易发假、边缘生硬")
        self.map_strength = tk.DoubleVar(value=float(self.cfg.get("map_strength", 0.35)))
        ttk.Spinbox(r4, from_=0.0, to=1.0, increment=0.05, width=4,
                    textvariable=self.map_strength).pack(side="left", padx=(6, 14))
        ttk.Label(r4, text="修复模型", style="Hint.TLabel").pack(side="left")
        _mr = self.cfg.get("map_restore", "GFPGANv1.4.pth")
        if _mr not in ("GFPGANv1.4.pth", "codeformer-v0.1.0.pth", "none"):
            _mr = "GFPGANv1.4.pth"
        self.map_restore = tk.StringVar(value=_mr)
        ttk.Combobox(r4, textvariable=self.map_restore, width=15, state="readonly",
                     values=["GFPGANv1.4.pth", "codeformer-v0.1.0.pth", "none"]).pack(side="left", padx=6)

        # 运行
        r5 = ttk.Frame(box, style="TabBody.TFrame"); r5.pack(fill="x", pady=(4, 8))
        self.pv_btn_3 = ttk.Button(r5, text="预览", style="Ghost.TButton", width=6,
                                   command=lambda: self.toggle_preview("3"))
        self.btn_map = ttk.Button(r5, text="开始映射", style="Accent.TButton", command=self.gen_map)
        self.btn_map.pack(side="left", fill="x", expand=True)
        self.btn_stop3 = ttk.Button(r5, text="停止", style="Ghost.TButton", width=6,
                                    command=self.stop, state="disabled")
        self.btn_stop3.pack(side="left", padx=(6, 0))
        self.pv_btn_3.pack(side="left", padx=(6, 0))

        self.prog3 = ttk.Progressbar(box, mode="determinate")
        self.prog3.pack(fill="x", pady=(4, 8))
        self.log3 = tk.Text(box, height=8, wrap="word", state="disabled", relief="flat", bd=0,
                            font=("Consolas", 9), bg=self.palette["card"], fg=self.palette["hint"],
                            padx=8, pady=6, highlightthickness=1,
                            highlightbackground=self.palette["border"])
        self.log3.pack(fill="both", expand=True)
        self.make_preview(box, "3")

    # ---------------- 配置
    def load_cfg(self):
        try:
            return json.load(open(CFG_FILE, encoding="utf-8"))
        except Exception:
            return {}

    def save_cfg(self):
        if getattr(self, "_no_save_cfg", False):
            return
        cfg = dict(self.cfg)
        cfg.pop("couple_mode", None)     # 不记忆模式
        cfg.update({
            "model": self.model.get(), "temp": self.temp.get(),
            "last_cn": self.cn_text().strip(),
            "last_prompt": self.pos.get("1.0", "end").strip(),
            "last_neg": self.neg.get("1.0", "end").strip(),
            "last_sentence": self.sentence.get("1.0", "end").strip(),
            "out_single": self.out1.get(),
            ("mout_batch" if self.couple_mode.get() == "多耦合" else "out_batch"): self.out2.get(),
            ("msrc_dir" if self.couple_mode.get() == "多耦合" else "src_dir"): self.src_dir.get(),
            "w": self.w.get(), "h": self.h.get(), "steps": self.steps.get(), "cfg": self.cfgv.get(),
            "sampler": self.sampler.get(), "scheduler": self.scheduler.get(), "seed": self.seed.get(),
            "seed_rand": self.seed_rand.get(),
            "times1": self.times1.get(), "times2": self.times2.get(),
            "w2": self.w2.get(), "h2": self.h2.get(),
            "wf_name": self.wf_name.get(), "lora_w": self.lora_w.get(), "faceid_w": self.faceid_w.get(),
               "theme": self.theme_key,
               "map_src": self.map_src.get(), "map_out": self.map_out.get(),
               "map_ref": self.map_ref.get(), "map_strength": self.map_strength.get(),
               "map_restore": self.map_restore.get(),
            "lora_w": self.lora_w.get(), "faceid_w": self.faceid_w.get(),
            "vram_mode": self.vram_mode.get(),
            "seed": int(self.seed.get()), "seed2": int(self.seed2.get()),
            "scale_x": float(self.scale_x.get()),
            "restore": round(float(self.restore_w.get()), 2),
            "mw2": int(self.cfg.get("mw2", 768)), "mh2": int(self.cfg.get("mh2", 1152)),
        })
        try:
            json.dump(cfg, open(CFG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass

    def on_close(self):
        self._closing = True
        self.save_cfg()
        self.stop_flag = True
        self.root.destroy()

    # ---------------- 小工具
    def set_status(self, t):
        self.q.put(("status", t))

    def log(self, where, text):
        self.q.put(("log", (where, text)))

    def copy_box(self, box):
        txt = box.get("1.0", "end").strip()
        if txt:
            self.root.clipboard_clear(); self.root.clipboard_append(txt)
            self.set_status("已复制 %d 字符" % len(txt))

    def copy_gloss(self):
        rows = getattr(self, "_gloss_rows", [])
        txt = "\n".join("%s\t%s" % (r[0], r[1]) for r in rows)
        self.root.clipboard_clear(); self.root.clipboard_append(txt)
        self.set_status("已复制词表 %d 条" % len(rows))

    def speak(self):
        txt = self.sentence.get("1.0", "end").strip()
        if not txt:
            return
        try:
            def run():
                try:
                    import win32com.client
                    sp = win32com.client.Dispatch("SAPI.SpVoice")
                    sp.Rate = 0
                    sp.Speak(txt)
                finally:
                    self.set_status("朗读完成")
                    def clear():
                        try:
                            if self.status.cget("text") == "朗读完成":
                                self.status.config(text="")
                        except Exception:
                            pass
                    try:
                        self.root.after(1200, clear)
                    except Exception:
                        pass
            threading.Thread(target=run, daemon=True).start()
            self.set_status("朗读中…")
        except Exception as e:
            self.set_status("[!] 朗读失败: %s" % e)

    def export_card(self):
        dirs = filedialog.askdirectory(title="学习卡片存到哪")
        if not dirs:
            return
        rows = getattr(self, "_gloss_rows", [])
        name = "学习卡片_%s.md" % time.strftime("%Y%m%d_%H%M")
        path = unique_path(os.path.join(dirs, name))
        body = ["# 学习卡片 %s" % time.strftime("%Y-%m-%d %H:%M"), "",
                "## 中文原文", self.cn.get("1.0", "end").strip(), "",
                "## 英文句子", self.sentence.get("1.0", "end").strip(), "",
                "## 提示词", self.pos.get("1.0", "end").strip(), "",
                "## 逐词对译", "", "| 单词 / 短语 | 中文 |", "|---|---|"]
        body += ["| %s | %s |" % (r[0], r[1]) for r in rows]
        open(path, "w", encoding="utf-8").write("\n".join(body))
        self.set_status("已导出: %s" % os.path.basename(path))

    def pick_dir(self, var):
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    def pick_file(self, var):
        f = filedialog.askopenfilename(filetypes=[("图片", "*.png *.jpg *.jpeg *.webp")])
        if f:
            var.set(f)

    def open_dir(self, var):
        p = var.get().strip()
        try:
            if p and os.path.isdir(p):
                os.startfile(p)
            elif p:
                os.startfile(os.path.dirname(p))
        except Exception:
            pass

    def edit_rules(self):
        try:
            os.startfile(RULES_FILE)
        except Exception as e:
            self.set_status("[!] %s" % e)

    def stop_translate(self):
        """强行停止翻译：丢弃结果，立刻放行界面"""
        self._tr_log_to = self._log_target()
        self._tr_seq = getattr(self, "_tr_seq", 0) + 1
        self.busy = False
        try:
            self.btn_tr.config(state="normal")
        except Exception:
            pass
        if hasattr(self, "btn_stop_tr"):
            try:
                self.btn_stop_tr.config(state="disabled")
            except Exception:
                pass
        self.set_status("已停止翻译")

    def shutdown_comfy(self):
        """同时关闭 ComfyUI(8188) 与 Ollama(11434)"""
        try:
            c = kill_port(8188)
            o = kill_port(11434)
            time.sleep(0.5)
            # 端口没清干净的，再按进程名补一刀
            extra = kill_by_name(["ollama.exe", "ollama_llama_server.exe", "ollama app.exe",
                                  "pythonw.exe"] if False else
                                 ["ollama.exe", "ollama_llama_server.exe", "ollama app.exe"])
            o2 = list(o) + [x for x in extra if "ollama" in x.lower()]
            parts = []
            parts.append("ComfyUI " + ("已关闭" if c else "未运行"))
            parts.append("Ollama " + ("已关闭" if (o or o2) else "未运行"))
            self.set_status("｜".join(parts))
            self.check_services()
        except Exception as e:
            self.set_status("[!] 关闭失败: %s" % e)

    def update_token(self):
        n = est_tokens(self.pos.get("1.0", "end").strip())
        self.tok_lbl.config(text="token %d/75 %s" % (n, "✅" if n <= 75 else "⚠️"))

    # ---------------- 启动
    def boot(self):
        """启动时把两个后端都拉起来"""
        # 1) Ollama
        if not ollama_alive():
            exe = find_ollama(self.cfg)
            self.q.put(("log", ("auto", "[i] Ollama 未运行，正在启动…")))
            if start_ollama(exe):
                self.q.put(("log", ("auto", "[i] Ollama 已就绪")))
            else:
                self.q.put(("log", ("auto", "[!] Ollama 启动失败，请手动运行 ollama serve")))
        # 2) ComfyUI
        if not comfy_alive():
            cdir = self.cfg.get("comfy_dir", "")
            self.q.put(("log", ("auto", "[i] ComfyUI 未运行，正在启动…（首次约 20~40 秒）")))
            if start_comfy(cdir):
                self.q.put(("log", ("auto", "[i] ComfyUI 已就绪")))
            else:
                hint = "请在 设置.json 里填 comfy_dir" if not cdir else cdir
                self.q.put(("log", ("auto", "[!] ComfyUI 启动失败：%s" % hint)))
        try:
            self._sync_seed_ui("1"); self._sync_seed_ui("2")
        except Exception:
            pass
        self.q.put(("refresh", None))

    # ---------- 服务状态
    def check_services(self):
        """异步检测两个服务，结果通过队列回主线程"""
        def run():
            comfy_ok = comfy_alive()
            models = list_models()
            self.q.put(("svc", (comfy_ok, models)))
        threading.Thread(target=run, daemon=True).start()

    def periodic_check(self):
        if not getattr(self, "_closing", False):
            self.check_services()
            self.root.after(8000, self.periodic_check)

    def apply_svc(self, comfy_ok, models):
        # 左上：ComfyUI
        self.dot.config(style="DotOn.TLabel" if comfy_ok else "DotOff.TLabel")
        self.dot_txt.config(text="ComfyUI " + ("已连接" if comfy_ok else "未连接"))
        # 左下：Ollama + 当前模型
        ollama_ok = bool(models)
        self.dot2.config(style="DotOn.TLabel" if ollama_ok else "DotOff.TLabel")
        if ollama_ok:
            cur = self.model.get()
            self.svc_lbl.config(text="Ollama 已连接 · %s · 模型 %d 个" % (cur, len(models)))
        else:
            self.svc_lbl.config(text="Ollama 未连接（翻译不可用）")
        if models:
            self.model_box["values"] = models
            if self.model.get() not in models:
                self.model.set(models[0])

    def refresh_models(self):
        ms = list_models()
        self.model_box["values"] = ms
        if ms and self.model.get() not in ms:
            self.model.set(ms[0])
        self.check_services()

    # ---------------- 翻译
    # ---------- 窗口尺寸
    def fit_window(self):
        """宽屏优先：宽度尽量铺开，高度刚好放下内容（不裁切）"""
        try:
            self.root.update_idletasks()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            need_h = self.root.winfo_reqheight()
            need_w = self.root.winfo_reqwidth()
            # 3:2 比例：先定高度（够放内容），再按 3:2 反推宽度
            h = min(sh - 60, max(need_h + 24, int(sh * 0.80)))
            w = min(sw - 30, max(1400, int(h * 3 / 2)))
            x = max(0, (sw - w) // 2)
            y = max(0, min(36, (sh - h) // 2))
            self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
            self.root.minsize(min(w, max(need_w, 1200)), min(h, max(need_h, 720)))
            # 分隔条初始位置：左 54%（窗口真正显示后再摆）
            def _place_sash(_e=None):
                try:
                    total = self.pw.winfo_width()
                    if total > 400:
                        self.pw.sash_place(0, int(total * 0.54), 0)
                except Exception:
                    pass
            try:
                self.root.bind("<Map>", lambda e: (self.root.after(120, _place_sash), None)[1], add="+")
                self.root.after(200, _place_sash)
            except Exception:
                pass
        except Exception:
            pass

    # ---------- 主题
    def cycle_theme(self):
        """依次切换配色方案（自动保存）"""
        i = THEME_ORDER.index(self.theme_key) if self.theme_key in THEME_ORDER else 0
        self.theme_key = THEME_ORDER[(i + 1) % len(THEME_ORDER)]
        self.apply_theme(self.theme_key)

    def apply_theme(self, key):
        self.palette = setup_style(self.root, key)
        c = self.palette
        # 普通 tk.Text 控件要单独改色
        for box, fg in ((self.cn, c["fg"]), (self.pos, c["fg"]), (self.neg, c["fg"]),
                        (self.sentence, c["fg"]), (self.log1, c["hint"]), (self.log2, c["hint"])):
            try:
                box.configure(bg=c["field"] if box in (self.cn, self.pos, self.neg) else c["card"],
                              fg=fg, insertbackground=c["accent"],
                              highlightbackground=c["border"], highlightcolor=c["accent"])
            except Exception:
                pass
        try:
            self.model_box.configure(style="Bar.TCombobox")
        except Exception:
            pass
        self.btn_theme.config(text="主题：" + c["name"])
        self.cfg["theme"] = key
        self.save_cfg()      # 走统一出口，测试时也能被 _no_save_cfg 拦住
        self.set_status("配色已切换为 " + c["name"])

    # ---------- 中文遮挡
    # ---------- 词表隐藏中文
    def toggle_gloss_cn(self):
        """把词表里的中文释义遮住 / 恢复"""
        if getattr(self, "_gloss_cn_hidden", False):
            self._gloss_cn_hidden = False
            self.btn_gloss.config(text="隐藏词义")
        else:
            self._gloss_cn_hidden = True
            self.btn_gloss.config(text="显示词义")
        self.render_gloss()

    def render_gloss(self):
        rows = getattr(self, "_gloss_rows", [])
        hidden = getattr(self, "_gloss_cn_hidden", False)
        self.gloss.clear()
        for en, cn in rows:
            self.gloss.add(en, "••••••" if hidden else cn, masked=hidden)

    def toggle_blur(self):
        """把中文描述换成看不清的方块，内容仍保留在内存里"""
        if getattr(self, "_blurred", False):
            self.cn.config(state="normal")          # 必须先解锁，否则 insert 无效
            self.cn.delete("1.0", "end")
            self.cn.insert("1.0", getattr(self, "_cn_real", ""))
            self._blurred = False
            self.btn_blur.config(text="隐藏")
            return
        raw = self.cn.get("1.0", "end").rstrip("\n")
        self._cn_real = raw
        # 中文按字替换成方块，英文按词替换成圆点，保留换行和空格
        masked = []
        for ch in raw:
            if "\u4e00" <= ch <= "\u9fff":
                masked.append("█")
            elif ch.isalnum() and ord(ch) < 128:
                masked.append("•")
            else:
                masked.append(ch)
        self.cn.delete("1.0", "end")
        self.cn.insert("1.0", "".join(masked))
        self.cn.config(state="disabled")
        self._blurred = True
        self.btn_blur.config(text="显示")

    def cn_text(self):
        """取中文原文（遮挡时返回缓存）"""
        if getattr(self, "_blurred", False):
            return getattr(self, "_cn_real", "")
        return self.cn.get("1.0", "end").strip()

    def translate(self, hot=False):
        if self.busy:
            return
        cn = self.cn_text().strip()
        if not cn:
            self.set_status("先写一句中文")
            return
        if not os.path.exists(RULES_FILE):
            self.set_status("[!] 找不到 提示词规则.txt")
            return
        self.busy = True
        self.btn_tr.config(state="disabled")
        try:
            self.btn_stop_tr.config(state="normal")
        except Exception:
            pass
        self.set_status("翻译中…")
        params = {"model": self.model.get(), "temp": 0.9 if hot else float(self.temp.get()),
                  "rules": open(RULES_FILE, encoding="utf-8").read(),
                  "vram_mode": self.vram_mode.get(),
            "seed": int(self.seed.get()), "seed2": int(self.seed2.get()), "log_to": self._log_target()}
        self._tr_log_to = self._log_target()
        self._tr_seq = getattr(self, "_tr_seq", 0) + 1
        self._tr_seq_at = self._tr_seq
        threading.Thread(target=self.tr_worker, args=(cn, params), daemon=True).start()

    def tr_worker(self, cn, params):
        try:
            note = params.get("log_to", "auto")
            t_all = time.time()

            # ── 翻译前准备：模型必须是“完整”在显存，否则就是在跑 CPU（那种 116 秒）
            vm = params.get("vram_mode", "按需")
            vram_of, total_of = ollama_residency()
            fully = total_of > 0 and vram_of >= total_of * 0.9
            t_prep = time.time()
            acted = []
            need_mb = max(5600, int(total_of // 1048576) + 512) if total_of else 5600
            free_mb = vram_free_mb()
            if not fully:
                if vm in ("翻译优先", "按需") and comfy_alive():
                    if comfy_busy():
                        self.q.put(("log", (note, "[!] ComfyUI 正在出图，暂时让不出显存，本次翻译会慢")))
                    if free_comfy_vram():
                        acted.append("让 ComfyUI 让出显存")
                        free_mb = wait_vram_free(need_mb, timeout=15)
                if vram_of > 0:
                    unload_ollama(params["model"])              # 半驻留不会自己搬家
                    acted.append("卸掉半驻留的模型")
                if acted:
                    self.q.put(("log", (note, "[i] 显存不足（可用 %d MB），已%s"
                                        % (free_mb, "，并".join(acted)))))
                # 预热：自适应上下文，直到模型“完整”进显存
                ctx_used, ok = fit_model_to_vram(params["model"])
                v2, t2_ = ollama_residency()
                if not ok:
                    # 再挤一次 ComfyUI，重试一轮
                    if vm in ("翻译优先", "按需") and comfy_alive():
                        free_comfy_vram(); wait_vram_free(need_mb, timeout=15)
                    ctx_used, ok = fit_model_to_vram(params["model"])
                    v2, t2_ = ollama_residency()
                params["num_ctx"] = ctx_used
                # 探针测速：ollama 有时会把模型“半速”摆放，重载一次通常能恢复
                sp = ping_speed(params["model"], ctx_used)
                if 0 <= sp < 12:
                    unload_ollama(params["model"])
                    if comfy_alive():
                        free_comfy_vram(); wait_vram_free(need_mb, timeout=10)
                    fit_model_to_vram(params["model"])
                    sp2 = ping_speed(params["model"], ctx_used)
                    self.q.put(("log", (note,
                        "[i] 探针测速 %.1f tok/s，重载后 %.1f tok/s" % (sp, sp2))))
                    if sp2 > sp:
                        sp = sp2
                if sp >= 0:
                    self.q.put(("log", (note, "[i] 当前推理速度 %.0f tok/s" % sp)))
                if ok:
                    if ctx_used < 4096:
                        self.q.put(("log", (note,
                            "[i] 显存偏紧，已把翻译上下文降到 %d 才完整放进显存" % ctx_used)))
                else:
                    self.q.put(("log", (note,
                        "[!] 翻译模型只有 %d/%d MB 进显存，本次会明显变慢"
                        "（关掉占显存的其他程序，或换成「翻译优先」）"
                        % (v2 // 1048576, t2_ // 1048576))))
            t_prep = time.time() - t_prep

            payload = {"model": params["model"],
                       "messages": [{"role": "system", "content": params["rules"]},
                                    {"role": "user", "content": cn}],
                       "stream": False, "think": False, "format": "json",
                       "keep_alive": "30m",
                       "options": {"temperature": params["temp"],
                                   "num_ctx": params.get("num_ctx", 4096), "num_predict": 520}}
            t0 = time.time()
            r = http_json(OLLAMA_API, "/api/chat", payload, timeout=600)
            d1 = time.time() - t0

            # 首轮异常慢 → 诊断并重试一次（强制腾出显存 + 重载模型）
            if d1 > 45:
                avg, peak = gpu_util_sample(2)
                v3, t3 = ollama_residency()
                self.q.put(("log", (note,
                    "[!] 首轮 %.0f 秒异常慢：GPU 利用率 %d%%（峰 %d%%，应为 90%% 以上）"
                    "｜模型 %d/%d MB 在显存"
                    % (d1, avg, peak, v3 // 1048576, t3 // 1048576))))
                try:
                    if comfy_alive():
                        free_comfy_vram(); wait_vram_free(need_mb, timeout=15)
                    unload_ollama(params["model"])
                    fit_model_to_vram(params["model"])
                    self.q.put(("log", (note, "[i] 已强制清显存并重载模型，重试一次")))
                    t1 = time.time()
                    r2 = http_json(OLLAMA_API, "/api/chat", payload, timeout=600)
                    d1b = time.time() - t1
                    if d1b < d1:
                        r = r2
                        d1 = d1b
                        self.q.put(("log", (note, "[i] 重试成功，%.1f 秒" % d1)))
                except Exception:
                    pass
            raw = (r.get("message") or {}).get("content", "")
            fields = parse_json(raw)
            clean, sentence, glossary, warns = guard(fields, raw)
            # 二次补全已关闭（用户要求：宁可词表少几条，也不要多等）
            d2 = 0.0
            # ---- 弱化词校验：命中就带纠正提示重译一次 ----
            hits = check_weak(clean, sentence)
            if hits:
                detail = "\n".join("- “%s” 应改为 %s（出现在 %s）" % (b, g, w)
                                   for b, g, w in dict.fromkeys(hits))
                fix_msg = ("你上一条输出把原文说轻了，违反「如实直译，禁止弱化」。\n"
                           + detail +
                           "\n请按原意重新输出完整 JSON：该用的词直接写，不要用任何委婉词；"
                           "sentence 也要如实叙述同一内容。不要解释，只输出 JSON。")
                try:
                    _t3 = time.time()
                    payload_fix = {"model": params["model"],
                                   "messages": payload["messages"] + [
                                       {"role": "assistant", "content": raw},
                                       {"role": "user", "content": fix_msg}],
                                   "stream": False, "think": False, "format": "json",
                                   "options": {"temperature": 0.35, "num_ctx": 4096,
                                               "num_predict": 900}}
                    rf = http_json(OLLAMA_API, "/api/chat", payload_fix, timeout=600)
                    rawf = (rf.get("message") or {}).get("content", "")
                    ff = parse_json(rawf)
                    cf, sf, gf, wf = guard(ff, rawf)
                    hits_f = check_weak(cf, sf)
                    if len(hits_f) < len(hits) and (sf or sentence):
                        clean, sentence, glossary = cf, sf or sentence, gf or glossary
                        warns = wf + warns + ["已按『禁止弱化』重译一次"]
                        hits = hits_f
                except Exception:
                    warns.append("弱化重译请求失败，保留首轮结果")
                if hits:
                    # 最后一道：按对照表自动替换能确定的部分
                    clean, sentence, fixed = auto_fix_weak(clean, sentence)
                    if fixed:
                        warns.append("已按词表自动修正: " + "、".join(fixed))
                    hits = check_weak(clean, sentence)
                if hits:
                    warns.append("仍有弱化词: " + ", ".join(sorted({b for b, _, _ in hits})))

            total = time.time() - t_all
            v2, t2_ = ollama_residency()
            if t2_ > 0 and v2 < t2_ * 0.5:
                warns.append("翻译模型只有 %d/%d MB 进了显存，其余在内存，所以很慢"
                             % (v2 // 1048576, t2_ // 1048576))
            _d2 = locals().get("d2", 0.0)
            self.q.put(("log", (note, "[i] 翻译完成 %.1f 秒（准备 %.1f ＋ 推理 %.1f）"
                                % (total, t_prep, d1))))
            try:
                v4, t4 = ollama_residency()
                diag_log(kind="translate", mode=vm, prep=round(t_prep, 1),
                         first=round(d1, 1), second=round(_d2, 1), total=round(total, 1),
                         ctx=params.get("num_ctx", 4096),
                         vram_in=round(v4 / 1048576), vram_total=round(t4 / 1048576),
                         comfy_busy=comfy_busy(), gpu=gpu_snapshot())
            except Exception:
                pass
            self.q.put(("translated", (clean, sentence, glossary, warns, total)))
        except Exception as e:
            self.q.put(("status", "[!] 翻译失败: %s" % str(e)[:200]))
        finally:
            self.q.put(("tr_done", None))

    # ---------------- 生图公共部分
    def ensure_comfy(self):
        if comfy_alive():
            return True
        cdir = self.cfg.get("comfy_dir", "")
        self.q.put(("log", (self._log_target(), "[i] ComfyUI 未运行，尝试启动…")))
        return start_comfy(cdir)

    def _log_target(self):
        """卸载/显存日志跟着当前页签走"""
        try:
            if self.tab_map.winfo_ismapped():
                return "map"
            if self.tab_batch.winfo_ismapped():
                return "batch"
        except Exception:
            pass
        return "single"

    def submit_and_wait(self, graph, dst, where, timeout=2400):
        import urllib.request, urllib.parse
        pid = http_json(COMFY_API, "/prompt", {"prompt": graph}, timeout=60)["prompt_id"]
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.stop_flag:
                return None
            try:
                h = http_json(COMFY_API, "/history/" + pid, timeout=30)
            except Exception:
                time.sleep(1.5); continue
            if pid in h:
                st = h[pid].get("status", {})
                if st.get("status_str") == "error":
                    self.log(where, "   节点出错: " + json.dumps(st, ensure_ascii=False)[:160])
                    return None
                for o in h[pid].get("outputs", {}).values():
                    for im in o.get("images", []):
                        qs = urllib.parse.urlencode({"filename": im["filename"],
                                                     "subfolder": im.get("subfolder", ""),
                                                     "type": im.get("type", "output")})
                        blob = urllib.request.urlopen(COMFY_API + "/view?" + qs, timeout=180).read()
                        real = unique_path(dst)
                        with open(real, "wb") as fh:
                            fh.write(blob)
                        return real
                return None
            time.sleep(1.5)
        return None

    def common_params(self):
        return {"w": self.w.get(), "h": self.h.get(), "steps": self.steps.get(),
                "cfg": self.cfgv.get(), "sampler": self.sampler.get(),
                "scheduler": self.scheduler.get(),
                "ckpt": self.cfg.get("ckpt", "epicphotogasm_v1.safetensors"),
                "vae": self.cfg.get("vae", "vae-ft-mse-840000-ema-pruned.safetensors")}

    # ---------------- 单张
    def gen_single(self):
        if self.busy:
            return
        prompt = self.pos.get("1.0", "end").strip()
        neg = self.neg.get("1.0", "end").strip()
        if not prompt:
            self.set_status("提示词是空的")
            return
        out = self.out1.get().strip() or os.path.join(APP_DIR, "出图")
        if not safe_makedirs(out):
            self.set_status("[!] 输出目录无法创建: %s" % out)
            return
        self.save_cfg()
        self.busy = True; self.stop_flag = False
        self.btn_gen1.config(state="disabled"); self.btn_stop1.config(state="normal")
        self.prog1["value"] = 0
        args = {"model": self.model.get(), "vram_mode": self.vram_mode.get(),
                "log_to": self._log_target(),
                "prompt": prompt, "neg": neg, "out": out,
                "times": max(1, self.times1.get()),
                "seed": max(0, min(int(self.seed.get()), 2 ** 31 - 1)),
                "seed_rand": self.seed_rand.get(),
                "cn": self.cn_text().strip(),
                "params": self.common_params()}
        threading.Thread(target=self.single_worker, args=(args,), daemon=True).start()

    def single_worker(self, a):
        # 生成前先释放显存（翻译模型常驻会挤占 ComfyUI）
        # 注意：这里在线程里，不能读 Tk 变量，模型名由主线程塞进 args
        try:
            vm2 = a.get("vram_mode", "按需")
            if vm2 == "按需":
                free_mb = vram_free_mb()
                if free_mb < 0 or free_mb < 4600:
                    freed = unload_ollama(a.get("model"))
                    if freed:
                        self.q.put(("log", (a.get("log_to", "single"),
                            "[i] 显存不足（可用 %s MB），已让翻译模型让出" % free_mb)))
            elif vm2 == "出图优先":
                freed = unload_ollama(a.get("model"))
                if freed:
                    self.q.put(("log", (a.get("log_to", "single"), "[i] 出图优先：已卸载翻译模型，释放显存")))
            # 翻译优先：出图前不动翻译模型
        except Exception:
            pass
        try:
            if not self.ensure_comfy():
                self.log("single", "[!] ComfyUI 不可用"); return
            self.q.put(("prog1_max", a["times"]))
            ok = 0
            for i in range(1, a["times"] + 1):
                if self.stop_flag:
                    break
                seed = (random.randint(1, 2 ** 31 - 1) if a.get("seed_rand", True)
                        else max(0, a["seed"] + i - 1))
                self.q.put(("seed_used", ("1", seed)))
                p = a["params"]
                g = build_plain(a["prompt"], a["neg"], p["w"], p["h"], p["steps"], p["cfg"],
                                p["sampler"], p["scheduler"], seed, p["ckpt"], p["vae"])
                self.log("single", "[%d/%d] seed=%d" % (i, a["times"], seed))
                t0 = time.time()
                dst = os.path.join(a["out"], "img_s%d.png" % seed)
                saved = self.submit_and_wait(g, dst, "single")
                if saved:
                    self.log("single", "   完成 %.0f 秒 -> %s" % (time.time() - t0, os.path.basename(saved)))
                    self.q.put(("preview", ("1", saved, i, a["times"])))
                    ok += 1
                else:
                    self.log("single", "   失败")
                self.q.put(("prog1", i))
            self.log("single", "结束：成功 %d / %d" % (ok, a["times"]))
        except Exception:
            self.log("single", "[!] " + traceback.format_exc()[-400:])
        finally:
            self.q.put(("done1", None))

    # ---------------- 批量
    def reset_people(self):
        self.lora_w.set(0.6)
        self.faceid_w.set(0.8)
        try:
            self._restore_touched = False
            self.restore_w.set(0.75 if self.couple_mode.get() == "单耦合" else 0.5)
            self._restore_touched = False
        except Exception:
            pass
        self.set_status("已恢复默认：LoRA 0.6 / FaceID 0.8")

    def list_workflows(self):
        d = os.path.join(APP_DIR, "工作流")
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d) if f.lower().endswith(".json"))

    def refresh_workflows(self):
        vals = self.list_workflows()
        self.wf_box["values"] = vals
        if vals and self.wf_name.get() not in vals:
            self.wf_name.set(vals[0])
        self.set_status("工作流: %d 个" % len(vals))

    def gen_batch(self):
        if self.busy:
            return
        prompt = self.pos.get("1.0", "end").strip()
        neg = self.neg.get("1.0", "end").strip()
        src = self.src_dir.get().strip()
        out = self.out2.get().strip()
        if not prompt:
            self.set_status("提示词是空的"); return
        if not src or not os.path.isdir(src):
            self.set_status("[!] 源目录（母版图片）不存在: %s" % (src or "(空)")); return
        wf = os.path.join(APP_DIR, "工作流", self.wf_name.get())
        if not os.path.exists(wf):
            self.set_status("[!] 找不到工作流: %s" % self.wf_name.get()); return
        if not out:
            out = os.path.join(src, "出图"); self.out2.set(out)
        files = sorted(os.path.join(src, f) for f in os.listdir(src)
                       if os.path.splitext(f)[1].lower() in EXTS)
        if not files:
            self.set_status("[!] 源目录里没有图片"); return
        os.makedirs(out, exist_ok=True)
        self.save_cfg()
        self.busy = True; self.stop_flag = False
        self.btn_gen2.config(state="disabled"); self.btn_stop2.config(state="normal")
        self.prog2["value"] = 0
        self._run_seq += 1
        args = {"model": self.model.get(), "vram_mode": self.vram_mode.get(),
                "log_to": self._log_target(),
                "prompt": prompt, "neg": neg, "src": src, "out": out,
                "files": files, "run_seq": self._run_seq,
                "times": max(1, self.times2.get()),
                "seed": max(0, min(int(self.seed2.get()), 2 ** 31 - 1)),
                "seed_rand": self.seed2_rand.get(),
                "cn": self.cn_text().strip(), "wf": wf,
                "w": self.w2.get(), "h": self.h2.get(),
                "lora_w": round(float(self.lora_w.get()), 2),
                "faceid_w": round(float(self.faceid_w.get()), 2),
                "face_count": 0,
                "restore": (round(float(self.restore_w.get()), 2)
                            if (self.couple_mode.get() != "单耦合" or
                                getattr(self, "_restore_touched", False)) else None),
                "mode": self.couple_mode.get(),
                "scale_x": float(self.scale_x.get()),
                "face_idx": 0,
                "largest_only": False}
        threading.Thread(target=self.batch_worker, args=(args,), daemon=True).start()

    def batch_worker(self, a):
        # 生成前先释放显存（翻译模型常驻会挤占 ComfyUI）
        # 注意：这里在线程里，不能读 Tk 变量，模型名由主线程塞进 args
        try:
            freed = unload_ollama(a.get("model"))
            if freed:
                self.q.put(("log", (a.get("log_to", "single"), "[i] 已卸载翻译模型，释放显存")))
        except Exception:
            pass
        try:
            if not self.ensure_comfy():
                self.log("batch", "[!] ComfyUI 不可用"); return
            tpl, fmt = load_workflow(a["wf"])
            if not tpl:
                self.log("batch", "[!] 工作流为空"); return
            # 源目录里的每张图 = 母版（特征来源），ReActor 也用它做耦合来源
            mode = a.get("mode", "单耦合")
            fixed_n = 1 if mode == "单耦合" else 0
            self.log("batch", "[i] 模式：%s" % mode)

            self.log("batch", "[i] 工作流 %s（%s 格式）节点 %d 个" % (
                os.path.basename(a["wf"]), fmt, len(tpl)))
            total = len(a["files"]) * a["times"]
            self.q.put(("prog2_max", total))
            done = ok = 0
            for f in a["files"]:
                if self.stop_flag:
                    break
                base = os.path.splitext(os.path.basename(f))[0]
                try:
                    img_name = upload_image(f, "bench_%s%s" % (base, os.path.splitext(f)[1].lower()))
                except Exception as e:
                    self.log("batch", "   上传失败 %s: %s" % (base, str(e)[:80]))
                    done += a["times"]
                    self.q.put(("prog2", done))
                    continue
                # 每张源图各自判断有几个主体（仅多耦合）
                n_face = fixed_n
                if n_face <= 0:
                    cnt, total_f = count_faces_detail(f)
                    n_face = max(1, min(6, cnt)) if cnt > 0 else 1
                    self.log("batch", "   源图主体 %s 个（画面共 %s 个）→ 耦合数 %d"
                              % (cnt if cnt > 0 else "未知",
                                 total_f if total_f > 0 else "未知", n_face))
                for i in range(1, a["times"] + 1):
                    if self.stop_flag:
                        break
                    done += 1
                    # 单耦合保持原种子算法；多耦合才加盐（防止缓存）
                    salt = 0 if a.get("mode") == "单耦合" else int(a.get("run_seq", 0)) * 7919
                    seed = (random.randint(1, 2 ** 31 - 1) if a.get("seed_rand", True)
                            else max(0, (a["seed"] + done + salt) % (2 ** 31)))
                    self.q.put(("seed_used", ("2", seed)))
                    if a["seed"] >= 0 and done == 1 and salt == 0:
                        self.log("batch", "[i] 种子固定 %d：同参数重复跑会直接返回缓存（同图、几秒完成）"
                                  % a["seed"])
                    g = json.loads(json.dumps(tpl))
                    prompt_use = a["prompt"]
                    if mode != "单耦合" and n_face >= 2:
                        if not re.search(r"\b\d+\s*(girl|boy)s?\b|\bsolo\b|\bcouple\b|\bman\b|\bwoman\b",
                                         prompt_use, re.I):
                            prompt_use = "%d%s, " % (n_face, "girls") + prompt_use
                            self.log("batch", "[i] 提示词没写人数，已按源图补 %dgirls" % n_face)
                    inject(g, img_name, prompt_use, a["neg"], seed, a["w"], a["h"],
                           lora_w=a["lora_w"], faceid_w=a["faceid_w"],
                           face_idx=a["face_idx"], disable_swap=False,
                           face_count=n_face,
                           restore=(a.get("restore") if mode != "单耦合"
                                    else (a.get("restore") if a.get("restore") is not None
                                          and abs(float(a["restore"]) - 0.75) > 0.001 else None)),
                           image_scale=(int(a["w"] * float(a.get("scale_x", 1.5))),
                                        int(a["h"] * float(a.get("scale_x", 1.5)))))
                    self.log("batch", "[%d/%d] %s #%d seed=%d" % (done, total, base, i, seed))
                    t0 = time.time()
                    dst = os.path.join(a["out"], "%s_%d.png" % (base, i))
                    saved = self.submit_and_wait(g, dst, "batch", timeout=3600)
                    if saved and mode != "单耦合":
                        try:
                            tf = count_faces(saved)
                            if tf >= 0:
                                self.log("batch", "   目标图 %d 个主体 ｜ 耦合数 %d" % (tf, n_face))
                            if tf > n_face:
                                self.log("batch", "   [!] 源图 %d 个主体，输出 %d 个 —— 多出来的没有来源，会保持原样"
                                          % (n_face, tf))
                            elif 0 <= tf < n_face:
                                self.log("batch", "   [!] 源图 %d 个主体，输出只有 %d 个 —— 少了 %d 个"
                                          % (n_face, tf, n_face - tf))
                                if re.search(r"\b1\s*(girl|boy)\b|\bsolo\b", a["prompt"], re.I):
                                    self.log("batch", "       提示词写的是单人，改成 %dgirls 才会生成 %d 个人"
                                              % (n_face, n_face))
                            rows, avg = similarity_report(f, saved)
                            if rows:
                                detail = "  ".join("%d↔%d %.2f" % (i, s, sim)
                                                   for i, s, sim in rows)
                                self.log("batch", "   相似度 %s ｜ 平均 %.2f" % (detail, avg))
                                worst_row = min(rows, key=lambda r: r[2])
                                if worst_row[2] < 0.55:
                                    self.log("batch",
                                             "   [!] 第 %d 个主体只有 %.2f：这张图里它被挡住或太小"
                                             % (worst_row[0], worst_row[2]))
                                    self.log("batch",
                                             "       办法：① 画布加到 1024×1536 ② 构图拉开距离、别前后遮挡"
                                             " ③ 提示词人数与源图一致")
                        except Exception:
                            pass
                    if saved and mode == "单耦合":
                        try:
                            # 单耦合只耦合 1 张脸，用“最像的那张”代表结果，别被路人脸拉低平均
                            rows, avg = similarity_report(f, saved)
                            if rows:
                                best = max(r[2] for r in rows)
                                extra = ("（画面另有 %d 张脸未参与耦合）" % (len(rows) - 1)
                                         if len(rows) > 1 else "")
                                warn = "  [!] 偏低，参考图差异较大" if best < 0.6 else ""
                                self.log("batch", "   相似度 %.2f%s%s" % (best, extra, warn))
                        except Exception:
                            pass
                    if saved:
                        self.log("batch", "   完成 %.0f 秒 -> %s" % (time.time() - t0, os.path.basename(saved)))
                        self.q.put(("preview", ("2", saved, done, total)))
                        ok += 1
                    self.q.put(("prog2", done))
            self.log("batch", "结束：成功 %d / %d" % (ok, total))
        except Exception:
            self.log("batch", "[!] " + traceback.format_exc()[-500:])
        finally:
            self.q.put(("done2", None))

    # ---------------- 映射（参考人物 → 素材图）
    def gen_map(self):
        if self.busy:
            return
        src = self.map_src.get().strip()
        out = self.map_out.get().strip()
        ref = self.map_ref.get().strip()
        if not src or not os.path.isdir(src):
            self.set_status("[!] 目标目录不存在")
            return
        if not ref or not os.path.exists(ref):
            self.set_status("[!] 母版图片不存在")
            return
        if not out:
            out = os.path.join(src, "映射结果")
            self.map_out.set(out)
        files = sorted(os.path.join(src, f) for f in os.listdir(src)
                       if os.path.splitext(f)[1].lower() in EXTS)
        if not files:
            self.set_status("[!] 目标目录里没有图片")
            return
        if not safe_makedirs(out):
            self.set_status("[!] 输出目录无法创建: %s" % out)
            return
        self.save_cfg()
        self.busy = True
        self.stop_flag = False
        self.btn_map.config(state="disabled")
        self.btn_stop3.config(state="normal")
        self.prog3["value"] = 0
        args = {"files": files, "out": out, "ref": ref,
                "model": self.model.get(), "vram_mode": self.vram_mode.get(),
                "log_to": self._log_target(),
                "strength": round(float(self.map_strength.get()), 2),
                "restore": self.map_restore.get()}
        threading.Thread(target=self.map_worker, args=(args,), daemon=True).start()

    def map_worker(self, a):
        # 生成前先释放显存（翻译模型常驻会挤占 ComfyUI）
        # 注意：这里在线程里，不能读 Tk 变量，模型名由主线程塞进 args
        try:
            freed = unload_ollama(a.get("model"))
            if freed:
                self.q.put(("log", (a.get("log_to", "single"), "[i] 已卸载翻译模型，释放显存")))
        except Exception:
            pass
        try:
            if not comfy_alive() and not start_comfy(self.cfg.get("comfy_dir", "")):
                self.log_map("[!] ComfyUI 不可用")
                return
            ref_name = upload_image(a["ref"], "map_ref" + os.path.splitext(a["ref"])[1].lower())
            total = len(a["files"])
            self.q.put(("prog3_max", total))
            self.log_map("[i] 母版已载入｜映射强度 %.2f｜修复 %s" % (a["strength"], a["restore"]))
            ok = 0
            for i, f in enumerate(a["files"], 1):
                if self.stop_flag:
                    break
                base = os.path.splitext(os.path.basename(f))[0]
                try:
                    img_name = upload_image(f, "map_t_%d%s" % (i, os.path.splitext(f)[1].lower()))
                except Exception as e:
                    self.log_map("   %s 上传失败: %s" % (base, str(e)[:60]))
                    self.q.put(("prog3", i))
                    continue
                g = {
                    "1": {"class_type": "LoadImage", "inputs": {"image": img_name}},
                    "2": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
                    "3": {"class_type": "ReActorFaceSwap", "inputs": {
                        "enabled": True, "input_image": ["1", 0], "source_image": ["2", 0],
                        "swap_model": "inswapper_128.onnx",
                        "facedetection": "retinaface_resnet50",
                        "face_restore_model": a["restore"],
                        "face_restore_visibility": float(a["strength"]),
                        "codeformer_weight": 0.5,
                        "detect_gender_input": "no", "detect_gender_source": "no",
                        "input_faces_index": "0", "source_faces_index": "0",
                        "console_log_level": 0}},
                    "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0],
                                                                "filename_prefix": "map_tmp"}},
                }
                t0 = time.time()
                dst = os.path.join(a["out"], base + "_映射.png")
                saved = self.submit_and_wait(g, dst, "single", timeout=900)
                if saved:
                    self.log_map("[%d/%d] %s 完成 %.0f 秒 -> %s"
                                 % (i, total, base, time.time() - t0, os.path.basename(saved)))
                    self.q.put(("preview", ("3", dst, i, total)))
                    ok += 1
                else:
                    self.log_map("[%d/%d] %s 失败" % (i, total, base))
                self.q.put(("prog3", i))
            self.log_map("结束：母版已映射到 %d / %d 张" % (ok, total))
        except Exception:
            self.log_map("[!] " + traceback.format_exc()[-400:])
        finally:
            self.q.put(("done3", None))

    def log_map(self, text):
        self.q.put(("log", ("map", text)))

    def stop(self):
        self.stop_flag = True
        self.set_status("已请求停止（当前这张跑完后停）")

    # ---------------- 事件循环
    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status":
                    self.status.config(text=str(val))
                elif kind == "log":
                    where, text = val
                    if where == "auto":
                        where = self._log_target()
                    box = {"single": self.log1, "batch": self.log2,
                           "map": self.log3}.get(where, self.log1)
                    box.config(state="normal")
                    box.insert("end", text + "\n")
                    if not getattr(self, "_sash_busy", False):
                        box.see("end")      # 拖动分隔条时不自动滚动，避免抖动
                    box.config(state="disabled")
                elif kind == "refresh":
                    self.refresh_models()
                elif kind == "svc":
                    self.apply_svc(*val)
                elif kind == "translated" and getattr(self, "_tr_seq", 0) == getattr(self, "_tr_seq_at", 0):
                    clean, sentence, glossary, warns, dt = val
                    prompt = render(clean)
                    neg = ", ".join(clean.get("negative", DEFAULT_NEG))
                    self.pos.delete("1.0", "end"); self.pos.insert("1.0", prompt)
                    self.neg.delete("1.0", "end"); self.neg.insert("1.0", neg)
                    self.sentence.delete("1.0", "end"); self.sentence.insert("1.0", sentence)
                    self._gloss_rows = [(g["en"], g["cn"]) for g in glossary]
                    self.render_gloss()
                    self.last = {"prompt": prompt, "neg": neg, "sentence": sentence, "glossary": glossary}
                    self.update_token()
                    try:
                        self.time_lbl.config(text="用时 %.1f 秒" % dt)
                    except Exception:
                        pass
                    msg = "翻译完成｜标签 %d｜词表 %d 条" % (est_tokens(prompt), len(glossary))
                    # 只保留一个最要紧的标记，其余细节写进日志
                    if any("仍有弱化词" in w for w in warns):
                        msg += "｜含弱化词"
                    elif any(("自动修正" in w) or ("重译" in w) for w in warns):
                        msg += "｜已修正"
                    self.status.config(text=msg)
                    if warns:
                        seen = []
                        for w in warns:
                            if w not in seen:
                                seen.append(w)
                        self.q.put(("log", (getattr(self, "_tr_log_to", "auto"),
                                            "[i] " + "；".join(seen))))
                elif kind == "tr_done":
                    self.busy = False
                    self.btn_tr.config(state="normal")
                    try:
                        self.btn_stop_tr.config(state="disabled")
                    except Exception:
                        pass
                elif kind == "prog1_max":
                    self.prog1["maximum"] = val
                elif kind == "prog1":
                    self.prog1["value"] = val
                elif kind == "prog2_max":
                    self.prog2["maximum"] = val
                elif kind == "prog2":
                    self.prog2["value"] = val
                elif kind == "prog3_max":
                    self.prog3["maximum"] = val
                elif kind == "prog3":
                    self.prog3["value"] = val
                elif kind == "seed_used":
                    self.set_seed_display(val[0], val[1])
                elif kind == "preview":
                    self.show_preview(*val)
                elif kind == "done1":
                    self.busy = False
                    self.btn_gen1.config(state="normal"); self.btn_stop1.config(state="disabled")
                elif kind == "done2":
                    self.busy = False
                    self.btn_gen2.config(state="normal"); self.btn_stop2.config(state="disabled")
                elif kind == "done3":
                    self.busy = False
                    self.btn_map.config(state="normal"); self.btn_stop3.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(90, self.pump)


if __name__ == "__main__":
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    App(root)
    root.mainloop()
