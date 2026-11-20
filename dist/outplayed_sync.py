import ctypes
import ctypes.wintypes as wintypes
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from xml.dom import minidom

try:
    import winreg
except ImportError:
    winreg = None


APP_VERSION = "2.0"
CONFIG_PATH = Path.home() / ".outplayed_premiere_sync.json"
DEFAULT_PREMIERE_EXE = Path(r"D:\Apps\Adobe\Adobe Premiere Pro 2026\Adobe Premiere Pro.exe")
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}

THEMES = {
    "dark": {
        "bg": "#16181d",
        "panel": "#1e2128",
        "card": "#252932",
        "input": "#2c313c",
        "border": "#363b47",
        "fg": "#eef0f4",
        "muted": "#9aa2b1",
        "accent": "#5b5ef4",
        "accent_hover": "#6f72ff",
        "accent_pressed": "#484bd4",
        "select_bg": "#4448c8",
        "tooltip_bg": "#101216",
        "tooltip_fg": "#f3f4f6",
        "preview_bg": "#111317",
        "warn": "#f59e0b",
        "ok": "#34d399",
    },
    "light": {
        "bg": "#f3f4f8",
        "panel": "#ffffff",
        "card": "#ffffff",
        "input": "#ffffff",
        "border": "#d4d8e2",
        "fg": "#181a20",
        "muted": "#5c6475",
        "accent": "#4f46e5",
        "accent_hover": "#6366f1",
        "accent_pressed": "#4338ca",
        "select_bg": "#4f46e5",
        "tooltip_bg": "#1f232c",
        "tooltip_fg": "#ffffff",
        "preview_bg": "#1e2028",
        "warn": "#d97706",
        "ok": "#059669",
    },
}

PREMIERE_COLORS = {
    "Зелёный": (62, 168, 79),
    "Красный": (220, 56, 56),
    "Оранжевый": (235, 130, 40),
    "Жёлтый": (230, 200, 50),
    "Голубой": (60, 190, 220),
    "Синий": (65, 120, 225),
    "Фиолетовый": (165, 85, 215),
    "Белый": (240, 240, 240),
}


def find_app_icon() -> Path | None:
    search_dirs: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        search_dirs.append(Path(sys._MEIPASS))
    search_dirs.append(Path(__file__).resolve().parent)
    search_dirs.append(Path.cwd())
    search_dirs.append(Path.cwd() / "dist")

    names = ("circular-app-icon-logo_cropped.ico", "app.ico", "icon.ico")
    for base in search_dirs:
        for name in names:
            p = base / name
            if p.exists():
                return p
    return None


def set_windows_dark_titlebar(window: tk.Misc, dark: bool):
    if os.name != "nt":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        val = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi
        # 20 = DWMWA_USE_IMMUTABLE_DARK_MODE (Win11 / Win10 20H1+), 19 = старый Win10
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def default_color_for_event(ev_type: str) -> str:
    t = ev_type.upper()
    if any(w in t for w in ("KILL", "ELIM", "HEADSHOT", "ACE", "CLUTCH")):
        return "Зелёный"
    if any(w in t for w in ("DEATH", "DIED", "KNOCK")):
        return "Красный"
    if any(w in t for w in ("ASSIST",)):
        return "Жёлтый"
    if any(w in t for w in ("WIN", "VICTORY")):
        return "Голубой"
    if any(w in t for w in ("BOOKMARK", "HIGHLIGHT", "MANUAL")):
        return "Оранжевый"
    return "Фиолетовый"


# ==========================================
# Всплывающие подсказки (Tooltips)
# ==========================================

class TooltipManager:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.theme = THEMES["dark"]
        self.tip_win: tk.Toplevel | None = None
        self.after_id: str | None = None
        self.active_widget: tk.Widget | None = None

    def set_theme(self, theme: dict):
        self.theme = theme

    def attach(self, widget: tk.Widget, text: str):
        widget.bind("<Enter>", lambda e, w=widget, t=text: self._schedule(w, t), add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")
        widget.bind("<ButtonPress>", lambda e: self.hide(), add="+")

    def _schedule(self, widget: tk.Widget, text: str):
        self.hide()
        self.active_widget = widget
        self.after_id = self.root.after(350, lambda: self._show(widget, text))

    def _show(self, widget: tk.Widget, text: str):
        if self.active_widget != widget:
            return
        try:
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 6
        except tk.TclError:
            return

        self.tip_win = tk.Toplevel(self.root)
        self.tip_win.wm_overrideredirect(True)
        self.tip_win.attributes("-topmost", True)

        lbl = tk.Label(
            self.tip_win,
            text=text,
            justify="left",
            wraplength=340,
            bg=self.theme["tooltip_bg"],
            fg=self.theme["tooltip_fg"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=self.theme["border"],
            padx=9,
            pady=6,
            font=("Segoe UI", 9),
        )
        lbl.pack()

        self.tip_win.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        tw = self.tip_win.winfo_reqwidth()
        th = self.tip_win.winfo_reqheight()
        if x + tw > sw - 10:
            x = max(10, sw - tw - 10)
        if y + th > sh - 40:
            y = max(10, widget.winfo_rooty() - th - 6)
        self.tip_win.wm_geometry(f"+{x}+{y}")

    def hide(self):
        self.active_widget = None
        if self.after_id:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.tip_win:
            try:
                self.tip_win.destroy()
            except Exception:
                pass
            self.tip_win = None


# ==========================================
# Windows Shell Thumbnail (превью как в Проводнике)
# ==========================================

def get_windows_thumbnail_ppm(file_path: Path, width: int = 304, height: int = 171) -> bytes | None:
    if os.name != "nt" or not file_path.exists():
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", wintypes.LONG),
            ("bmWidth", wintypes.LONG),
            ("bmHeight", wintypes.LONG),
            ("bmWidthBytes", wintypes.LONG),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", ctypes.c_void_p),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    IID_IShellItemImageFactory = GUID(
        0xBCC18B79,
        0xBA16,
        0x442F,
        (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B),
    )

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32

    ole32.CoInitializeEx(None, 0x2)
    factory = ctypes.c_void_p()
    hbm = wintypes.HBITMAP()
    hdc = None

    try:
        shell32.SHCreateItemFromParsingName.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHCreateItemFromParsingName.restype = ctypes.HRESULT

        hr = shell32.SHCreateItemFromParsingName(
            str(file_path.resolve()),
            None,
            ctypes.byref(IID_IShellItemImageFactory),
            ctypes.byref(factory),
        )
        if hr != 0 or not factory.value:
            return None

        vtable_arr = ctypes.cast(
            ctypes.cast(factory.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents,
            ctypes.POINTER(ctypes.c_void_p),
        )

        if struct.calcsize("P") == 8:
            GetImageProto = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p,
                ctypes.c_uint64,
                ctypes.c_int,
                ctypes.POINTER(wintypes.HBITMAP),
            )
            size_arg = (width & 0xFFFFFFFF) | ((height & 0xFFFFFFFF) << 32)
        else:
            GetImageProto = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p,
                SIZE,
                ctypes.c_int,
                ctypes.POINTER(wintypes.HBITMAP),
            )
            size_arg = SIZE(width, height)

        get_image = GetImageProto(vtable_arr[3])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable_arr[2])

        hr = get_image(factory.value, size_arg, 0x08, ctypes.byref(hbm))
        if hr != 0 or not hbm.value:
            hr = get_image(factory.value, size_arg, 0x00, ctypes.byref(hbm))

        release(factory.value)
        factory.value = None

        if hr != 0 or not hbm.value:
            return None

        bmp = BITMAP()
        if gdi32.GetObjectW(hbm, ctypes.sizeof(BITMAP), ctypes.byref(bmp)) == 0:
            return None

        w, h = bmp.bmWidth, bmp.bmHeight
        if w <= 0 or h <= 0:
            return None

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = (ctypes.c_ubyte * (w * h * 4))()
        hdc = user32.GetDC(None)
        gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(BITMAPINFOHEADER),
            wintypes.UINT,
        ]
        if gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bmi), 0) == 0:
            return None

        raw = bytes(buf)
        rgb = bytearray(w * h * 3)
        rgb[0::3] = raw[2::4]
        rgb[1::3] = raw[1::4]
        rgb[2::3] = raw[0::4]

        return f"P6\n{w} {h}\n255\n".encode("ascii") + bytes(rgb)
    except Exception:
        return None
    finally:
        if hdc:
            user32.ReleaseDC(None, hdc)
        if hbm and hbm.value:
            gdi32.DeleteObject(hbm)
        ole32.CoUninitialize()


# ==========================================
# Pure-Python Snappy, LevelDB (.ldb + .log) и V8 IndexedDB парсер
# ==========================================

def _read_file_shared(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except PermissionError:
        if os.name != "nt":
            return None
        try:
            k32 = ctypes.windll.kernel32
            GENERIC_READ = 0x80000000
            SHARE_ALL = 0x01 | 0x02 | 0x04
            OPEN_EXISTING = 3
            INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
            handle = k32.CreateFileW(str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, 0, None)
            if handle == INVALID_HANDLE_VALUE or not handle:
                return None
            try:
                size = path.stat().st_size
                if size <= 0:
                    return b""
                buf = (ctypes.c_ubyte * size)()
                read_bytes = wintypes.DWORD(0)
                ok = k32.ReadFile(handle, buf, size, ctypes.byref(read_bytes), None)
                if not ok:
                    return None
                return bytes(buf[: read_bytes.value])
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return None
    except Exception:
        return None


def _snappy_decompress(src: bytes) -> bytes | None:
    try:
        i = 0
        n = len(src)
        uncomp_len = 0
        shift = 0
        while i < n:
            b = src[i]
            i += 1
            uncomp_len |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift > 35:
                return None
        if uncomp_len > 32 * 1024 * 1024:
            return None

        out = bytearray()
        while i < n:
            tag = src[i]
            i += 1
            kind = tag & 0x03
            if kind == 0:
                l = tag >> 2
                if l < 60:
                    lit_len = l + 1
                else:
                    extra = l - 59
                    if i + extra > n:
                        return None
                    lit_len = int.from_bytes(src[i : i + extra], "little") + 1
                    i += extra
                if i + lit_len > n:
                    return None
                out.extend(src[i : i + lit_len])
                i += lit_len
            else:
                if kind == 1:
                    if i >= n:
                        return None
                    length = ((tag >> 2) & 0x07) + 4
                    offset = ((tag & 0xE0) << 3) | src[i]
                    i += 1
                elif kind == 2:
                    if i + 2 > n:
                        return None
                    length = (tag >> 2) + 1
                    offset = int.from_bytes(src[i : i + 2], "little")
                    i += 2
                else:
                    if i + 4 > n:
                        return None
                    length = (tag >> 2) + 1
                    offset = int.from_bytes(src[i : i + 4], "little")
                    i += 4

                if offset == 0 or offset > len(out):
                    return None
                if offset >= length:
                    start = len(out) - offset
                    out.extend(out[start : start + length])
                else:
                    for _ in range(length):
                        out.append(out[-offset])
        return bytes(out)
    except Exception:
        return None


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift > 63:
            break
    return val, pos


def _decode_all_encodings(raw: bytes) -> list[str]:
    res = [raw.decode("utf-8", errors="ignore")]
    for shift in (0, 1):
        chunk = raw[shift:]
        if len(chunk) % 2 == 1:
            chunk = chunk[:-1]
        if chunk:
            res.append(chunk.decode("utf-16-le", errors="ignore"))
    return res


def _parse_ldb_kv_records(data: bytes) -> list[tuple[int, int, bytes, bytes]]:
    records: list[tuple[int, int, bytes, bytes]] = []
    if len(data) < 48 or data[-8:] != b"\x57\xfb\x80\x8b\x24\x75\x47\xdb":
        return records

    footer = data[-48:]
    p = 0
    _, p = _read_varint(footer, p)
    _, p = _read_varint(footer, p)
    idx_off, p = _read_varint(footer, p)
    idx_sz, p = _read_varint(footer, p)
    if idx_off + idx_sz + 1 > len(data):
        return records

    raw_idx = data[idx_off : idx_off + idx_sz]
    if data[idx_off + idx_sz] == 1:
        raw_idx = _snappy_decompress(raw_idx) or b""
    if len(raw_idx) < 4:
        return records

    num_restarts = int.from_bytes(raw_idx[-4:], "little")
    limit = len(raw_idx) - 4 - num_restarts * 4
    ip = 0
    prev_idx_key = b""

    block_handles: list[tuple[int, int]] = []
    while ip < limit:
        shared, ip = _read_varint(raw_idx, ip)
        non_shared, ip = _read_varint(raw_idx, ip)
        val_len, ip = _read_varint(raw_idx, ip)
        if ip + non_shared + val_len > limit:
            break
        key = prev_idx_key[:shared] + raw_idx[ip : ip + non_shared]
        prev_idx_key = key
        ip += non_shared
        val_bytes = raw_idx[ip : ip + val_len]
        ip += val_len
        vp = 0
        boff, vp = _read_varint(val_bytes, vp)
        bsz, vp = _read_varint(val_bytes, vp)
        block_handles.append((boff, bsz))

    for boff, bsz in block_handles:
        if boff + bsz + 1 > len(data):
            continue
        blk = data[boff : boff + bsz]
        if data[boff + bsz] == 1:
            blk = _snappy_decompress(blk) or b""
        if len(blk) < 4:
            continue
        blk_restarts = int.from_bytes(blk[-4:], "little")
        blk_limit = len(blk) - 4 - blk_restarts * 4
        if blk_limit <= 0:
            continue
        bp = 0
        prev_key = b""
        while bp < blk_limit:
            shared, bp = _read_varint(blk, bp)
            non_shared, bp = _read_varint(blk, bp)
            val_len, bp = _read_varint(blk, bp)
            if bp + non_shared + val_len > blk_limit:
                break
            ikey = prev_key[:shared] + blk[bp : bp + non_shared]
            prev_key = ikey
            bp += non_shared
            val = blk[bp : bp + val_len]
            bp += val_len
            if len(ikey) >= 8:
                trailer = int.from_bytes(ikey[-8:], "little")
                val_type = trailer & 0xFF
                seq = trailer >> 8
                records.append((seq, val_type, ikey[:-8], val))
    return records


def _parse_write_batch(batch: bytes, out: list[tuple[int, int, bytes, bytes]]):
    if len(batch) < 12:
        return
    seq = int.from_bytes(batch[0:8], "little")
    count = int.from_bytes(batch[8:12], "little")
    p = 12
    n = len(batch)
    for idx in range(count):
        if p >= n:
            break
        tag = batch[p]
        p += 1
        klen, p = _read_varint(batch, p)
        if p + klen > n:
            break
        key = batch[p : p + klen]
        p += klen
        if tag == 1:
            vlen, p = _read_varint(batch, p)
            if p + vlen > n:
                break
            val = batch[p : p + vlen]
            p += vlen
            out.append((seq + idx, 1, key, val))
        elif tag == 0:
            out.append((seq + idx, 0, key, b""))
        else:
            break


def _parse_log_kv_records(data: bytes) -> list[tuple[int, int, bytes, bytes]]:
    entries: list[tuple[int, int, bytes, bytes]] = []
    block_size = 32768
    n = len(data)
    pos = 0
    frag = bytearray()

    while pos < n:
        block_end = min(n, ((pos // block_size) + 1) * block_size)
        if block_end - pos < 7:
            pos = block_end
            continue
        rec_len = int.from_bytes(data[pos + 4 : pos + 6], "little")
        rec_type = data[pos + 6]
        if rec_type == 0 or pos + 7 + rec_len > block_end:
            pos = block_end
            continue
        payload = data[pos + 7 : pos + 7 + rec_len]
        pos += 7 + rec_len

        if rec_type == 1:
            _parse_write_batch(payload, entries)
            frag.clear()
        elif rec_type == 2:
            frag = bytearray(payload)
        elif rec_type == 3:
            frag.extend(payload)
        elif rec_type == 4:
            frag.extend(payload)
            _parse_write_batch(bytes(frag), entries)
            frag.clear()

    return entries


def _read_leveldb_latest_map(db_dir: Path) -> dict[bytes, bytes]:
    raw_records: list[tuple[int, int, bytes, bytes]] = []
    try:
        files = sorted(db_dir.iterdir(), key=lambda f: f.name)
    except Exception:
        return {}

    for f in files:
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in (".ldb", ".log"):
            continue
        if f.stat().st_size > 64 * 1024 * 1024:
            continue
        data = _read_file_shared(f)
        if not data:
            continue
        if ext == ".ldb":
            raw_records.extend(_parse_ldb_kv_records(data))
        else:
            raw_records.extend(_parse_log_kv_records(data))

    latest: dict[bytes, tuple[int, int, bytes]] = {}
    for seq, val_type, ukey, val in raw_records:
        prev = latest.get(ukey)
        if prev is None or seq >= prev[0]:
            latest[ukey] = (seq, val_type, val)

    return {k: v for k, (_, vt, v) in latest.items() if vt == 1 and v}


_V8_HOLE = object()


class _V8Decoder:
    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos
        self.n = len(data)
        self.refs: list = []

    def _u8(self) -> int:
        if self.pos >= self.n:
            raise ValueError("EOF")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def _peek(self) -> int:
        while self.pos < self.n and self.data[self.pos] == 0:
            self.pos += 1
        if self.pos >= self.n:
            raise ValueError("EOF")
        return self.data[self.pos]

    def _varint(self) -> int:
        val = 0
        shift = 0
        while self.pos < self.n:
            b = self.data[self.pos]
            self.pos += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                return val
            shift += 7
            if shift > 63:
                raise ValueError("varint overflow")
        raise ValueError("EOF varint")

    def _zigzag(self) -> int:
        u = self._varint()
        return (u >> 1) ^ -(u & 1)

    def _f64(self) -> float:
        if self.pos + 8 > self.n:
            raise ValueError("EOF f64")
        val = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return val

    def _bytes(self, length: int) -> bytes:
        if length < 0 or self.pos + length > self.n:
            raise ValueError("EOF bytes")
        b = self.data[self.pos : self.pos + length]
        self.pos += length
        return b

    def read_value(self, depth: int = 0):
        if depth > 40:
            raise ValueError("too deep")
        tag = self._u8()
        while tag == 0:
            tag = self._u8()

        if tag == 0xFF:
            self._varint()
            return self.read_value(depth + 1)
        if tag in (0x5F, 0x30):
            return None
        if tag == 0x54:
            return True
        if tag == 0x46:
            return False
        if tag == 0x49:
            return self._zigzag()
        if tag == 0x55:
            return self._varint()
        if tag == 0x4E:
            return self._f64()
        if tag == 0x44:
            val = self._f64()
            self.refs.append(val)
            return val
        if tag == 0x22:
            ln = self._varint()
            return self._bytes(ln).decode("latin-1", errors="ignore")
        if tag == 0x63:
            ln = self._varint()
            return self._bytes(ln).decode("utf-16-le", errors="ignore")
        if tag == 0x53:
            ln = self._varint()
            return self._bytes(ln).decode("utf-8", errors="ignore")
        if tag == 0x5A:
            bf = self._varint()
            ln = bf >> 1
            val = int.from_bytes(self._bytes(ln), "little")
            return -val if (bf & 1) else val
        if tag == 0x2D:
            return _V8_HOLE
        if tag == 0x5E:
            idx = self._varint()
            return self.refs[idx] if 0 <= idx < len(self.refs) else None
        if tag == 0x79:
            self.refs.append(True)
            return True
        if tag == 0x78:
            self.refs.append(False)
            return False
        if tag == 0x6E:
            v = self._f64()
            self.refs.append(v)
            return v
        if tag == 0x73:
            v = self.read_value(depth + 1)
            self.refs.append(v)
            return v
        if tag == 0x42:
            ln = self._varint()
            self._bytes(ln)
            self.refs.append(b"")
            return b""
        if tag == 0x56:
            self._varint()
            self._varint()
            self._varint()
            self.refs.append(b"")
            return b""
        if tag == 0x52:
            pat = self.read_value(depth + 1)
            self._varint()
            self.refs.append(pat)
            return pat
        if tag == 0x6F:
            obj: dict = {}
            self.refs.append(obj)
            while True:
                if self._peek() == 0x7B:
                    self._u8()
                    self._varint()
                    break
                k = self.read_value(depth + 1)
                v = self.read_value(depth + 1)
                if k is not None and k is not _V8_HOLE and v is not _V8_HOLE:
                    obj[str(k)] = v
            return obj
        if tag == 0x41:
            length = self._varint()
            if length > 200000:
                raise ValueError("array too big")
            arr: list = []
            self.refs.append(arr)
            for _ in range(length):
                el = self.read_value(depth + 1)
                arr.append(None if el is _V8_HOLE else el)
            while True:
                if self._peek() == 0x24:
                    self._u8()
                    self._varint()
                    self._varint()
                    break
                k = self.read_value(depth + 1)
                v = self.read_value(depth + 1)
                if isinstance(k, int) and 0 <= k < len(arr) and v is not _V8_HOLE:
                    arr[k] = v
            return arr
        if tag == 0x61:
            self._varint()
            arr_map: dict = {}
            self.refs.append(arr_map)
            while True:
                if self._peek() == 0x40:
                    self._u8()
                    self._varint()
                    self._varint()
                    break
                k = self.read_value(depth + 1)
                v = self.read_value(depth + 1)
                if v is not _V8_HOLE:
                    arr_map[k] = v
            int_items = [(k, v) for k, v in arr_map.items() if isinstance(k, int) and k >= 0]
            if int_items and len(int_items) == len(arr_map):
                int_items.sort(key=lambda kv: kv[0])
                return [v for _, v in int_items]
            return arr_map
        if tag == 0x3B:
            mp: dict = {}
            self.refs.append(mp)
            while True:
                if self._peek() == 0x3A:
                    self._u8()
                    self._varint()
                    break
                k = self.read_value(depth + 1)
                v = self.read_value(depth + 1)
                if k is not None:
                    mp[str(k)] = v
            return mp
        if tag == 0x27:
            st: list = []
            self.refs.append(st)
            while True:
                if self._peek() == 0x2C:
                    self._u8()
                    self._varint()
                    break
                st.append(self.read_value(depth + 1))
            return st

        raise ValueError(f"Unknown V8 tag 0x{tag:02x}")


def _decode_idb_v8_value(raw: bytes):
    if not raw:
        return None

    candidates = [raw]
    for skip in (0, 1, 2):
        if skip < len(raw):
            dec = _snappy_decompress(raw[skip:])
            if dec:
                candidates.append(dec)

    valid_root_tags = {0x6F, 0x41, 0x61, 0x22, 0x63, 0x53, 0x3B, 0x27}
    for buf in candidates:
        limit = min(len(buf) - 2, 48)
        for i in range(max(0, limit)):
            if buf[i] == 0xFF and 9 <= buf[i + 1] <= 25:
                nxt = buf[i + 2] if i + 2 < len(buf) else 0
                if nxt == 0xFF and i + 4 < len(buf) and 9 <= buf[i + 3] <= 25:
                    start_pos = i + 2
                elif nxt in valid_root_tags:
                    start_pos = i
                else:
                    continue
                try:
                    val = _V8Decoder(buf, start_pos).read_value()
                    if isinstance(val, (dict, list, str)):
                        return val
                except Exception:
                    continue
    return None


def _extract_leveldb_texts(ldb_path: Path) -> list[str]:
    texts: list[str] = []
    data = _read_file_shared(ldb_path)
    if not data:
        return texts

    texts.extend(_decode_all_encodings(data))
    if ldb_path.suffix.lower() == ".ldb":
        for _, val_type, _, val in _parse_ldb_kv_records(data):
            if val_type == 1 and val:
                texts.extend(_decode_all_encodings(val))
    elif ldb_path.suffix.lower() == ".log":
        for _, val_type, _, val in _parse_log_kv_records(data):
            if val_type == 1 and val:
                texts.extend(_decode_all_encodings(val))
    return texts


def load_outplayed_indexeddb_medias() -> tuple[list[dict], int]:
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Overwolf",
        Path(os.environ.get("APPDATA", "")) / "Overwolf",
    ]

    db_dirs: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.indexeddb.leveldb"):
            if p.is_dir():
                db_dirs.append(p)

    all_medias: list[dict] = []
    used_dbs = 0

    for db_dir in db_dirs:
        latest = _read_leveldb_latest_map(db_dir)
        if not latest:
            continue

        found_in_this_db = 0
        for val in latest.values():
            if len(val) < 16:
                continue
            decoded_obj = _decode_idb_v8_value(val)
            if decoded_obj is not None:
                parsed = deep_json_loads(decoded_obj)
                medias = collect_medias_with_events(parsed)
                if medias:
                    all_medias.extend(medias)
                    found_in_this_db += len(medias)
                    continue

            for txt in _decode_all_encodings(val):
                if "events" in txt and "path" in txt and "{" in txt:
                    start = txt.find("{")
                    end = txt.rfind("}")
                    if start != -1 and end > start:
                        try:
                            obj = deep_json_loads(json.loads(txt[start : end + 1]))
                            medias = collect_medias_with_events(obj)
                            if medias:
                                all_medias.extend(medias)
                                found_in_this_db += len(medias)
                                break
                        except Exception:
                            pass

        if found_in_this_db > 0:
            used_dbs += 1

    return all_medias, used_dbs


def _scan_obj_for_gaps(
    obj,
    known_event_types: set[str],
    per_event: dict[str, dict],
    global_candidates: list[tuple[float, float]],
    context_key: str = "",
):
    before_keys = (
        "preeventduration", "preevent", "beforeevent", "clipbefore",
        "highlightbefore", "beforeoffset", "preroll", "bookmarkbefore",
        "leadtime", "startoffset", "paddingbefore", "beforetime",
        "durationbefore", "capturebefore", "secondsbefore", "before", "pre",
    )
    after_keys = (
        "posteventduration", "postevent", "afterevent", "clipafter",
        "highlightafter", "afteroffset", "postroll", "bookmarkafter",
        "tailtime", "endoffset", "paddingafter", "aftertime",
        "durationafter", "captureafter", "secondsafter", "after", "post",
    )
    event_markers = (
        "KILL", "DEATH", "ASSIST", "HEADSHOT", "VICTORY", "WIN", "CLUTCH",
        "ACE", "KNOCK", "SPIKE", "PLANT", "DEFUSE", "ULTIMATE", "DOUBLE",
        "TRIPLE", "QUADRA", "PENTA", "BOOKMARK", "MANUAL", "HIGHLIGHT",
        "GOAL", "SAVE", "ROUND", "DOWN", "ELIM", "MVPS", "BOMB",
    )

    def norm_sec(v) -> float | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        fv = float(v)
        if fv < 0:
            return None
        if fv > 120:
            fv /= 1000.0
        if 0 <= fv <= 120:
            return round(fv, 1)
        return None

    if isinstance(obj, dict):
        low_map = {str(k).lower(): v for k, v in obj.items()}
        b_val = None
        a_val = None
        for bk in before_keys:
            if bk in low_map:
                b_val = norm_sec(low_map[bk])
                if b_val is not None:
                    break
        for ak in after_keys:
            if ak in low_map:
                a_val = norm_sec(low_map[ak])
                if a_val is not None:
                    break

        if b_val is not None and a_val is not None:
            raw_name = (
                obj.get("type")
                or obj.get("event")
                or obj.get("eventType")
                or obj.get("name")
                or obj.get("id")
                or context_key
            )
            ev_name = str(raw_name or "").strip().upper()
            is_ev = (
                ev_name in known_event_types
                or any(m in ev_name for m in event_markers)
            )
            if is_ev and 2 <= len(ev_name) <= 32:
                per_event[ev_name] = {"before": b_val, "after": a_val}
            else:
                global_candidates.append((b_val, a_val))

        for k, v in obj.items():
            _scan_obj_for_gaps(v, known_event_types, per_event, global_candidates, str(k))
    elif isinstance(obj, list):
        for item in obj:
            _scan_obj_for_gaps(item, known_event_types, per_event, global_candidates, context_key)


def detect_outplayed_gap_settings(known_event_types: list[str] | None = None) -> dict | None:
    """
    Ищет глобальные и индивидуальные гапы Outplayed:
    1) В декодированной Chromium IndexedDB (V8 объекты),
    2) В Chromium LocalStorage LevelDB,
    3) В JSON и логах Overwolf/Outplayed.
    """
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Overwolf",
        Path(os.environ.get("APPDATA", "")) / "Overwolf",
    ]
    known_set = {e.upper() for e in (known_event_types or [])}

    # 1. Сканируем IndexedDB (V8) и LocalStorage (LevelDB)
    for root in roots:
        if not root.exists():
            continue
        for db_dir in root.rglob("*.leveldb"):
            if not db_dir.is_dir():
                continue
            latest = _read_leveldb_latest_map(db_dir)
            if not latest:
                continue

            per_event: dict[str, dict] = {}
            global_cands: list[tuple[float, float]] = []

            for k_bytes, v_bytes in latest.items():
                k_txt = k_bytes.decode("utf-8", errors="ignore")
                decoded = _decode_idb_v8_value(v_bytes)
                if decoded is not None:
                    parsed = deep_json_loads(decoded)
                    _scan_obj_for_gaps(parsed, known_set, per_event, global_cands, k_txt)

                # LocalStorage / сырой JSON
                raw_variants = [v_bytes]
                if len(v_bytes) > 1 and v_bytes[0] in (0, 1):
                    raw_variants.append(v_bytes[1:])
                for rv in raw_variants:
                    for txt in _decode_all_encodings(rv):
                        if "{" in txt and any(w in txt.lower() for w in ("before", "preevent", "after", "postevent", "preroll")):
                            s_idx = txt.find("{")
                            e_idx = txt.rfind("}")
                            if s_idx != -1 and e_idx > s_idx:
                                try:
                                    jobj = deep_json_loads(json.loads(txt[s_idx : e_idx + 1]))
                                    _scan_obj_for_gaps(jobj, known_set, per_event, global_cands, k_txt)
                                except Exception:
                                    pass

            if per_event or global_cands:
                g = global_cands[0] if global_cands else (
                    next(iter(per_event.values()))["before"],
                    next(iter(per_event.values()))["after"],
                )
                return {
                    "global": g,
                    "per_event": per_event,
                    "source": f"{db_dir.parent.name}/{db_dir.name}",
                }

    # 2. Резервный текстовый/JSON поиск по файлам Overwolf
    before_keys = r"(?:preEventDuration|preEvent|beforeEvent|clipBefore|highlightBefore|beforeOffset|preRoll|bookmarkBefore|leadTime|startOffset|paddingBefore|before)"
    after_keys = r"(?:postEventDuration|postEvent|afterEvent|clipAfter|highlightAfter|afterOffset|postRoll|bookmarkAfter|tailTime|endOffset|paddingAfter|after)"

    pair_re = re.compile(
        rf'"?{before_keys}"?\s*:\s*(\d+(?:\.\d+)?).*?"?{after_keys}"?\s*:\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE | re.DOTALL,
    )
    event_block_re = re.compile(
        r'"?([a-zA-Z0-9_ -]{3,24})"?\s*:\s*\{([^{}]{5,240})\}',
        re.IGNORECASE,
    )

    def norm(v: float) -> float:
        return v / 1000.0 if v > 120 else v

    known_event_markers = ("kill", "death", "assist", "headshot", "victory", "clutch", "bookmark")

    for root in roots:
        if not root.exists():
            continue
        for fpath in root.rglob("*"):
            if not fpath.is_file():
                continue
            ext = fpath.suffix.lower()
            if ext not in (".json", ".log", ".ldb"):
                continue
            low = str(fpath).lower()
            if "outplayed" not in low and "leveldb" not in low and "indexeddb" not in low:
                continue
            if fpath.stat().st_size > 20 * 1024 * 1024:
                continue

            if ext in (".ldb", ".log"):
                chunks = _extract_leveldb_texts(fpath)
            else:
                try:
                    chunks = [fpath.read_text(encoding="utf-8", errors="ignore")]
                except Exception:
                    continue

            for text in chunks:
                t_low = text.lower()
                if sum(1 for k in known_event_markers if k in t_low) < 2 and "preevent" not in t_low:
                    continue

                per_event = {}
                for em in event_block_re.finditer(text):
                    ev_name = em.group(1).strip().upper()
                    body = em.group(2)
                    pm = pair_re.search(body)
                    if pm and len(ev_name) >= 3:
                        b = norm(float(pm.group(1)))
                        a = norm(float(pm.group(2)))
                        if 0 <= b <= 120 and 0 <= a <= 120:
                            per_event[ev_name] = {"before": round(b, 1), "after": round(a, 1)}

                if per_event:
                    first = next(iter(per_event.values()))
                    return {
                        "global": (first["before"], first["after"]),
                        "per_event": per_event,
                        "source": fpath.name,
                    }

                gm = pair_re.search(text)
                if gm:
                    b = norm(float(gm.group(1)))
                    a = norm(float(gm.group(2)))
                    if 0 <= b <= 120 and 0 <= a <= 120:
                        return {
                            "global": (round(b, 1), round(a, 1)),
                            "per_event": {},
                            "source": fpath.name,
                        }
    return None


# ==========================================
# Работа с JSON и базой Outplayed
# ==========================================

def deep_json_loads(obj):
    if isinstance(obj, str):
        s = obj.strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                return deep_json_loads(json.loads(s))
            except Exception:
                return obj
        return obj
    if isinstance(obj, list):
        return [deep_json_loads(item) for item in obj]
    if isinstance(obj, dict):
        return {k: deep_json_loads(v) for k, v in obj.items()}
    return obj


def find_latest_library_json() -> Path | None:
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Overwolf",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Overwolf",
    ]
    candidates = []
    for root in roots:
        if root.exists():
            candidates.extend(root.rglob("media-db-backup/library-*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def collect_medias_with_events(obj, found=None):
    if found is None:
        found = []
    if isinstance(obj, dict):
        events = obj.get("events")
        path = obj.get("path")
        if isinstance(events, list) and len(events) > 0 and isinstance(path, str) and path:
            found.append(obj)
        for v in obj.values():
            collect_medias_with_events(v, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_medias_with_events(item, found)
    return found


def get_media_duration_sec(media_dict: dict) -> float:
    for key in ("duration", "videoDuration"):
        val = media_dict.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val) / 1000.0 if val > 86400 else float(val)

    start = float(media_dict.get("startTime", 0) or 0)
    end = float(media_dict.get("endTime", 0) or 0)
    dur = max(0.0, end - start)
    if dur > 86400:
        dur /= 1000.0
    return dur


# ==========================================
# Быстрый pure-Python парсер MP4-заголовков
# ==========================================

def _iter_mp4_boxes(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            break
        size, box_type = struct.unpack(">I4s", header)
        hdr_len = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            size = struct.unpack(">Q", ext)[0]
            hdr_len = 16
        elif size == 0:
            size = end - pos
        if size < hdr_len or pos + size > end:
            break
        yield box_type, pos + hdr_len, pos + size
        pos += size


def parse_mp4_metadata(path: Path) -> dict | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        file_size = path.stat().st_size
        with path.open("rb") as f:
            moov_range = None
            for btype, bstart, bend in _iter_mp4_boxes(f, 0, file_size):
                if btype == b"moov":
                    moov_range = (bstart, bend)
                    break
            if not moov_range:
                return None

            duration_sec = 0.0
            width = 1920
            height = 1080
            fps = 0.0
            audio_streams: list[dict] = []

            for btype, bstart, bend in _iter_mp4_boxes(f, moov_range[0], moov_range[1]):
                if btype == b"mvhd":
                    f.seek(bstart)
                    vf = f.read(4)
                    if len(vf) == 4:
                        ver = vf[0]
                        if ver == 0:
                            data = f.read(16)
                            if len(data) == 16:
                                _, _, timescale, dur = struct.unpack(">IIII", data)
                                if timescale > 0:
                                    duration_sec = dur / timescale
                        elif ver == 1:
                            data = f.read(28)
                            if len(data) == 28:
                                _, _, timescale, dur = struct.unpack(">QQIQ", data)
                                if timescale > 0:
                                    duration_sec = dur / timescale

                elif btype == b"trak":
                    tk_w, tk_h = 0, 0
                    handler = None
                    md_timescale = 0
                    md_duration = 0
                    total_samples = 0
                    aud_channels = 2
                    aud_rate = 48000

                    for tbtype, tbstart, tbend in _iter_mp4_boxes(f, bstart, bend):
                        if tbtype == b"tkhd":
                            f.seek(tbstart)
                            vf = f.read(4)
                            if len(vf) == 4:
                                ver = vf[0]
                                off = 72 if ver == 0 else 84
                                if tbstart + 4 + off + 8 <= tbend:
                                    f.seek(tbstart + 4 + off)
                                    wh = f.read(8)
                                    if len(wh) == 8:
                                        w_fixed, h_fixed = struct.unpack(">II", wh)
                                        tk_w = w_fixed >> 16
                                        tk_h = h_fixed >> 16

                        elif tbtype == b"mdia":
                            for mbtype, mbstart, mbend in _iter_mp4_boxes(f, tbstart, tbend):
                                if mbtype == b"hdlr":
                                    if mbstart + 12 <= mbend:
                                        f.seek(mbstart + 8)
                                        handler = f.read(4)
                                elif mbtype == b"mdhd":
                                    f.seek(mbstart)
                                    vf = f.read(4)
                                    if len(vf) == 4:
                                        ver = vf[0]
                                        if ver == 0:
                                            data = f.read(16)
                                            if len(data) == 16:
                                                _, _, md_timescale, md_duration = struct.unpack(">IIII", data)
                                        elif ver == 1:
                                            data = f.read(28)
                                            if len(data) == 28:
                                                _, _, md_timescale, md_duration = struct.unpack(">QQIQ", data)
                                elif mbtype == b"minf":
                                    for ibtype, ibstart, ibend in _iter_mp4_boxes(f, mbstart, mbend):
                                        if ibtype == b"stbl":
                                            for sbtype, sbstart, sbend in _iter_mp4_boxes(f, ibstart, ibend):
                                                if sbtype == b"stts" and sbstart + 8 <= sbend:
                                                    f.seek(sbstart + 4)
                                                    cnt_bytes = f.read(4)
                                                    if len(cnt_bytes) == 4:
                                                        entry_count = struct.unpack(">I", cnt_bytes)[0]
                                                        max_entries = min(entry_count, (sbend - sbstart - 8) // 8)
                                                        for _ in range(max_entries):
                                                            pair = f.read(8)
                                                            if len(pair) < 8:
                                                                break
                                                            scount, _ = struct.unpack(">II", pair)
                                                            total_samples += scount
                                                elif sbtype == b"stsd" and sbstart + 16 <= sbend:
                                                    for _, abstart, abend in _iter_mp4_boxes(f, sbstart + 8, sbend):
                                                        if abstart + 28 <= abend:
                                                            f.seek(abstart + 16)
                                                            ainfo = f.read(12)
                                                            if len(ainfo) == 12:
                                                                ch, _, _, sr_hi, _ = struct.unpack(">HHHHH", ainfo[:10])
                                                                if 1 <= ch <= 8:
                                                                    aud_channels = ch
                                                                if sr_hi in (44100, 48000, 32000, 22050, 96000):
                                                                    aud_rate = sr_hi

                    if handler == b"vide":
                        if tk_w > 0 and tk_h > 0:
                            width, height = tk_w, tk_h
                        if md_timescale > 0 and md_duration > 0 and total_samples > 0:
                            fps = total_samples / (md_duration / md_timescale)
                    elif handler == b"soun":
                        audio_streams.append({
                            "channels": aud_channels,
                            "samplerate": aud_rate,
                        })

            return {
                "duration": duration_sec,
                "width": width,
                "height": height,
                "fps": fps,
                "audio_tracks": len(audio_streams),
                "audio_streams": audio_streams,
            }
    except Exception:
        return None


# ==========================================
# Поиск, проверка окна и быстрый запуск Premiere Pro
# ==========================================

def find_premiere_exe(preferred: str = "") -> Path | None:
    if preferred:
        p = Path(preferred)
        if p.exists():
            return p
    if DEFAULT_PREMIERE_EXE.exists():
        return DEFAULT_PREMIERE_EXE
    if winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Adobe Premiere Pro.exe",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "")
                p = Path(val.strip('"'))
                if p.exists():
                    return p
        except OSError:
            pass

    adobe_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Adobe"
    if adobe_dir.exists():
        found = sorted(adobe_dir.glob("Adobe Premiere Pro*/Adobe Premiere Pro.exe"), reverse=True)
        if found:
            return found[0]
    return None


def get_premiere_pids() -> set[int]:
    if os.name != "nt":
        return set()
    pids: set[int] = set()
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Adobe Premiere Pro.exe", "/FO", "CSV", "/NH"],
            creationflags=0x08000000,
            text=True,
            errors="ignore",
        )
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and "adobe premiere pro.exe" in parts[0].lower():
                if parts[1].isdigit():
                    pids.add(int(parts[1]))
    except Exception:
        pass
    return pids


def find_premiere_main_hwnd(pids: set[int]) -> int | None:
    if os.name != "nt" or not pids:
        return None

    user32 = ctypes.windll.user32
    found_hwnd: int | None = None

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, lparam):
        nonlocal found_hwnd
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w >= 450 and h >= 300:
                found_hwnd = int(hwnd)
                return False
        return True

    user32.EnumWindows(enum_cb, 0)
    return found_hwnd


def focus_window(hwnd: int | None):
    if os.name != "nt" or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def auto_locate_renamed_videos(
    missing_items: list[dict],
    known_existing_paths: set[Path],
    extra_search_dirs: list[Path] | None = None,
) -> dict[str, Path]:
    if not missing_items:
        return {}

    search_dirs: set[Path] = set()
    for item in missing_items:
        orig = Path(item["path"])
        if orig.parent.exists():
            search_dirs.add(orig.parent)
        if orig.parent.parent.exists():
            search_dirs.add(orig.parent.parent)

    videos_outplayed = Path.home() / "Videos" / "Overwolf" / "Outplayed"
    if videos_outplayed.exists():
        search_dirs.add(videos_outplayed)

    for d in extra_search_dirs or []:
        if d.exists():
            search_dirs.add(d)

    video_exts = {".mp4", ".mov", ".mkv"}
    candidates: list[dict] = []
    seen_files: set[Path] = set()

    for root in search_dirs:
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in video_exts:
                    continue
                rp = p.resolve()
                if rp in seen_files or rp in known_existing_paths:
                    continue
                seen_files.add(rp)
                meta = parse_mp4_metadata(rp) if rp.suffix.lower() in {".mp4", ".mov"} else None
                stat = rp.stat()
                candidates.append({
                    "path": rp,
                    "name": rp.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "duration": meta["duration"] if meta else None,
                })
        except Exception:
            continue

    results: dict[str, Path] = {}
    used_candidates: set[Path] = set()

    for item in missing_items:
        orig_path = Path(item["path"])
        expected_dur = item["duration_sec"]
        expected_size = item["raw"].get("fileSize") or item["raw"].get("size")

        best_cand = None
        best_score = -1.0

        for cand in candidates:
            if cand["path"] in used_candidates:
                continue
            score = 0.0
            if isinstance(expected_size, (int, float)) and expected_size > 0:
                if abs(cand["size"] - expected_size) < 4096:
                    score += 100.0
                else:
                    continue

            if cand["duration"] is not None and expected_dur > 0:
                diff = abs(cand["duration"] - expected_dur)
                if diff <= 0.5:
                    score += 60.0
                elif diff <= 2.0:
                    score += 35.0
                else:
                    continue
            elif cand["name"] == orig_path.name:
                score += 40.0
            else:
                continue

            if cand["path"].parent == orig_path.parent:
                score += 20.0
            elif orig_path.parent.name.lower() in str(cand["path"]).lower():
                score += 10.0

            if score > best_score:
                best_score = score
                best_cand = cand

        if best_cand and best_score >= 35.0:
            results[item["path"]] = best_cand["path"]
            used_candidates.add(best_cand["path"])

    return results


# ==========================================
# Генерация Premiere Pro XML (xmeml v4)
# ==========================================

def _path_to_premiere_url(p: Path) -> str:
    posix = p.resolve().as_posix()
    encoded = urllib.parse.quote(posix, safe="/")
    return f"file://localhost/{encoded.lstrip('/')}"


def _extract_event_duration_sec(ev: dict) -> float | None:
    for key in ("duration", "length"):
        val = ev.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val) / 1000.0 if val > 300 else float(val)
    if "endTime" in ev and "time" in ev:
        try:
            t1 = float(ev["time"])
            t2 = float(ev["endTime"])
            if t2 > t1:
                diff = t2 - t1
                return diff / 1000.0 if diff > 300 else diff
        except (TypeError, ValueError):
            pass
    return None


def build_premiere_xml(
    output_xml_path: Path,
    events: list[dict],
    sequence_name: str,
    fps: int = 60,
    duration_sec: float = 3600.0,
    video_path: Path | None = None,
    embed_video: bool = True,
    marker_mode: str = "normal",
    marker_style: str = "range",
    gap_before: float = 5.0,
    gap_after: float = 5.0,
    default_action_sec: float = 1.5,
    enabled_event_types: set[str] | None = None,
    event_colors: dict[str, str] | None = None,
    per_event_gaps: dict[str, dict] | None = None,
) -> int:
    video_meta = parse_mp4_metadata(video_path) if (video_path and video_path.exists()) else None
    actual_fps = fps if fps > 0 else 60
    width = 1920
    height = 1080
    audio_streams = [{"channels": 2, "samplerate": 48000}]

    if video_meta:
        if video_meta["duration"] > 0:
            duration_sec = video_meta["duration"]
        if video_meta["width"] > 0 and video_meta["height"] > 0:
            width = video_meta["width"]
            height = video_meta["height"]
        if fps <= 0 and video_meta["fps"] > 1:
            actual_fps = round(video_meta["fps"])
        if video_meta["audio_tracks"] > 0:
            audio_streams = video_meta["audio_streams"]
        else:
            audio_streams = []

    parsed_markers = []
    max_time = 0.0

    for ev in events:
        if not isinstance(ev, dict) or "time" not in ev:
            continue
        ev_type = str(ev.get("type") or ev.get("name") or "Event").strip().upper()
        if enabled_event_types is not None and ev_type not in enabled_event_types:
            continue

        try:
            time_sec = max(0.0, float(ev["time"]) / 1000.0)
        except (TypeError, ValueError):
            continue

        extras = []
        for k, v in ev.items():
            if k in ("type", "time") or v in (None, "", [], {}):
                continue
            extras.append(f"{k}: {v}")
        comment = " | ".join(extras)

        color_name = (event_colors or {}).get(ev_type) or default_color_for_event(ev_type)
        rgb = PREMIERE_COLORS.get(color_name)

        ev_dur = _extract_event_duration_sec(ev) or default_action_sec

        if marker_mode == "normal":
            max_time = max(max_time, time_sec)
            parsed_markers.append({
                "in_sec": time_sec,
                "out_sec": None,
                "name": ev_type,
                "comment": comment,
                "rgb": rgb,
            })

        elif marker_mode in ("action", "action_gap"):
            b_gap = 0.0
            a_gap = 0.0
            if marker_mode == "action_gap":
                ov = (per_event_gaps or {}).get(ev_type, {})
                b_gap = ov["before"] if ov.get("before") is not None else gap_before
                a_gap = ov["after"] if ov.get("after") is not None else gap_after

            start_sec = max(0.0, time_sec - b_gap)
            end_sec = time_sec + ev_dur + a_gap
            if duration_sec > 0:
                end_sec = min(end_sec, duration_sec)
            if end_sec <= start_sec:
                end_sec = start_sec + 0.5

            max_time = max(max_time, end_sec)

            if marker_style == "range":
                parsed_markers.append({
                    "in_sec": start_sec,
                    "out_sec": end_sec,
                    "name": ev_type,
                    "comment": f"[{start_sec:.1f}s - {end_sec:.1f}s] {comment}".strip(),
                    "rgb": rgb,
                })
            else:
                parsed_markers.append({
                    "in_sec": start_sec,
                    "out_sec": None,
                    "name": f"{ev_type} [START]",
                    "comment": comment,
                    "rgb": rgb,
                })
                parsed_markers.append({
                    "in_sec": end_sec,
                    "out_sec": None,
                    "name": f"{ev_type} [END]",
                    "comment": comment,
                    "rgb": rgb,
                })

    parsed_markers.sort(key=lambda m: m["in_sec"])

    seq_duration_sec = max(duration_sec, max_time + 2.0, 10.0)
    total_frames = max(1, round(seq_duration_sec * actual_fps))
    clip_frames = max(1, round((duration_sec if duration_sec > 0 else seq_duration_sec) * actual_fps))

    xmeml = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(xmeml, "sequence", id="sequence-1")

    ET.SubElement(sequence, "name").text = sequence_name
    ET.SubElement(sequence, "duration").text = str(total_frames)

    rate = ET.SubElement(sequence, "rate")
    ET.SubElement(rate, "timebase").text = str(actual_fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"

    timecode = ET.SubElement(sequence, "timecode")
    tc_rate = ET.SubElement(timecode, "rate")
    ET.SubElement(tc_rate, "timebase").text = str(actual_fps)
    ET.SubElement(tc_rate, "ntsc").text = "FALSE"
    ET.SubElement(timecode, "string").text = "00:00:00:00"
    ET.SubElement(timecode, "frame").text = "0"
    ET.SubElement(timecode, "displayformat").text = "NDF"

    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")

    fmt = ET.SubElement(video, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    sc_rate = ET.SubElement(sc, "rate")
    ET.SubElement(sc_rate, "timebase").text = str(actual_fps)
    ET.SubElement(sc_rate, "ntsc").text = "FALSE"
    ET.SubElement(sc, "width").text = str(width)
    ET.SubElement(sc, "height").text = str(height)
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    ET.SubElement(sc, "fielddominance").text = "none"

    v_track = ET.SubElement(video, "track")

    if embed_video and video_path and video_path.exists():
        v_clip = ET.SubElement(v_track, "clipitem", id="clipitem-1")
        ET.SubElement(v_clip, "name").text = video_path.name
        ET.SubElement(v_clip, "duration").text = str(clip_frames)
        vc_rate = ET.SubElement(v_clip, "rate")
        ET.SubElement(vc_rate, "timebase").text = str(actual_fps)
        ET.SubElement(vc_rate, "ntsc").text = "FALSE"
        ET.SubElement(v_clip, "start").text = "0"
        ET.SubElement(v_clip, "end").text = str(clip_frames)
        ET.SubElement(v_clip, "in").text = "0"
        ET.SubElement(v_clip, "out").text = str(clip_frames)

        file_el = ET.SubElement(v_clip, "file", id="file-1")
        ET.SubElement(file_el, "name").text = video_path.name
        ET.SubElement(file_el, "pathurl").text = _path_to_premiere_url(video_path)
        f_rate = ET.SubElement(file_el, "rate")
        ET.SubElement(f_rate, "timebase").text = str(actual_fps)
        ET.SubElement(f_rate, "ntsc").text = "FALSE"
        ET.SubElement(file_el, "duration").text = str(clip_frames)

        f_media = ET.SubElement(file_el, "media")
        f_vid = ET.SubElement(f_media, "video")
        f_vsc = ET.SubElement(f_vid, "samplecharacteristics")
        ET.SubElement(f_vsc, "width").text = str(width)
        ET.SubElement(f_vsc, "height").text = str(height)

        for stream in audio_streams:
            f_aud = ET.SubElement(f_media, "audio")
            f_asc = ET.SubElement(f_aud, "samplecharacteristics")
            ET.SubElement(f_asc, "depth").text = "16"
            ET.SubElement(f_asc, "samplerate").text = str(stream["samplerate"])
            ET.SubElement(f_aud, "channelcount").text = str(stream["channels"])

        all_links = [("clipitem-1", "video", 1)] + [
            (f"clipitem-{idx + 1}", "audio", idx)
            for idx in range(1, len(audio_streams) + 1)
        ]

        if audio_streams:
            for ref_id, mtype, tidx in all_links:
                lnk = ET.SubElement(v_clip, "link")
                ET.SubElement(lnk, "linkclipref").text = ref_id
                ET.SubElement(lnk, "mediatype").text = mtype
                ET.SubElement(lnk, "trackindex").text = str(tidx)
                ET.SubElement(lnk, "clipindex").text = "1"

            audio_node = ET.SubElement(media, "audio")
            for idx, _ in enumerate(audio_streams, start=1):
                a_track = ET.SubElement(audio_node, "track")
                a_clip = ET.SubElement(a_track, "clipitem", id=f"clipitem-{idx + 1}")
                ET.SubElement(a_clip, "name").text = video_path.name
                ET.SubElement(a_clip, "duration").text = str(clip_frames)
                ac_rate = ET.SubElement(a_clip, "rate")
                ET.SubElement(ac_rate, "timebase").text = str(actual_fps)
                ET.SubElement(ac_rate, "ntsc").text = "FALSE"
                ET.SubElement(a_clip, "start").text = "0"
                ET.SubElement(a_clip, "end").text = str(clip_frames)
                ET.SubElement(a_clip, "in").text = "0"
                ET.SubElement(a_clip, "out").text = str(clip_frames)
                ET.SubElement(a_clip, "file", id="file-1")

                st = ET.SubElement(a_clip, "sourcetrack")
                ET.SubElement(st, "mediatype").text = "audio"
                ET.SubElement(st, "trackindex").text = str(idx)

                for ref_id, mtype, tidx in all_links:
                    lnk = ET.SubElement(a_clip, "link")
                    ET.SubElement(lnk, "linkclipref").text = ref_id
                    ET.SubElement(lnk, "mediatype").text = mtype
                    ET.SubElement(lnk, "trackindex").text = str(tidx)
                    ET.SubElement(lnk, "clipindex").text = "1"

    for m in parsed_markers:
        in_frame = max(0, round(m["in_sec"] * actual_fps))
        out_frame = -1 if m["out_sec"] is None else max(in_frame + 1, round(m["out_sec"] * actual_fps))
        marker_el = ET.SubElement(sequence, "marker")
        ET.SubElement(marker_el, "name").text = m["name"]
        ET.SubElement(marker_el, "comment").text = m["comment"]
        ET.SubElement(marker_el, "in").text = str(in_frame)
        ET.SubElement(marker_el, "out").text = str(out_frame)
        if m.get("rgb"):
            r, g, b = m["rgb"]
            col_el = ET.SubElement(marker_el, "color")
            ET.SubElement(col_el, "alpha").text = "0"
            ET.SubElement(col_el, "red").text = str(r)
            ET.SubElement(col_el, "green").text = str(g)
            ET.SubElement(col_el, "blue").text = str(b)

    raw_xml = ET.tostring(xmeml, encoding="utf-8")
    pretty = minidom.parseString(raw_xml).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    lines = pretty.splitlines()
    lines.insert(1, "<!DOCTYPE xmeml>")

    output_xml_path.parent.mkdir(parents=True, exist_ok=True)
    output_xml_path.write_text("\n".join(lines), encoding="utf-8")
    return len(parsed_markers)


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    mins, secs = divmod(total, 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


# ==========================================
# Окно фильтра, цветов и индивидуальных гапов
# ==========================================

class EventSettingsDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        event_types: list[str],
        disabled_types: set[str],
        color_map: dict[str, str],
        per_event_gaps: dict[str, dict],
        global_before: float,
        global_after: float,
        theme: dict,
        is_dark: bool,
        on_save,
    ):
        super().__init__(parent)
        self.title("Фильтр, цвета и тайминги событий")
        self.geometry("680x540")
        self.transient(parent)
        self.grab_set()
        self.configure(bg=theme["bg"])
        set_windows_dark_titlebar(self, is_dark)

        self.on_save = on_save
        self.vars_enabled: dict[str, tk.BooleanVar] = {}
        self.vars_color: dict[str, tk.StringVar] = {}
        self.vars_before: dict[str, tk.StringVar] = {}
        self.vars_after: dict[str, tk.StringVar] = {}

        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(
            top,
            text=f"Настройка каждого события (пустые поля «До» / «После» берут общие: {global_before:g}с / {global_after:g}с):",
            wraplength=640,
        ).pack(anchor="w")

        btns_top = ttk.Frame(top)
        btns_top.pack(fill="x", pady=(8, 0))
        ttk.Button(btns_top, text="Выбрать все", command=lambda: self._set_all(True)).pack(side="left", padx=(0, 6))
        ttk.Button(btns_top, text="Снять все", command=lambda: self._set_all(False)).pack(side="left", padx=(0, 6))
        ttk.Button(btns_top, text="Сбросить гапы к общим", command=self._clear_gaps).pack(side="right")

        hdr = ttk.Frame(self, padding=(14, 6, 26, 4))
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Событие", width=24, font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(hdr, text="Цвет в Premiere", width=16, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(4, 12))
        ttk.Label(hdr, text="До (сек)", width=10, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        ttk.Label(hdr, text="После (сек)", width=10, font=("Segoe UI", 9, "bold")).pack(side="left")

        canvas_frame = ttk.Frame(self, padding=(12, 0, 12, 6))
        canvas_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bg=theme["panel"])
        sb = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=6)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _on_wheel(e):
            try:
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except tk.TclError:
                pass

        canvas.bind_all("<MouseWheel>", _on_wheel)
        self.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>") if e.widget is self else None)

        color_names = list(PREMIERE_COLORS.keys())
        for ev_type in event_types:
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=3, padx=2)

            var_en = tk.BooleanVar(value=(ev_type not in disabled_types))
            self.vars_enabled[ev_type] = var_en
            ttk.Checkbutton(row, text=ev_type, variable=var_en, width=23).pack(side="left")

            cur_color = color_map.get(ev_type) or default_color_for_event(ev_type)
            var_col = tk.StringVar(value=cur_color)
            self.vars_color[ev_type] = var_col
            ttk.Combobox(row, textvariable=var_col, values=color_names, state="readonly", width=13).pack(
                side="left", padx=(4, 14)
            )

            ev_gap = per_event_gaps.get(ev_type, {})
            b_val = "" if ev_gap.get("before") is None else f"{ev_gap['before']:g}"
            a_val = "" if ev_gap.get("after") is None else f"{ev_gap['after']:g}"

            vb = tk.StringVar(value=b_val)
            va = tk.StringVar(value=a_val)
            self.vars_before[ev_type] = vb
            self.vars_after[ev_type] = va

            ttk.Entry(row, textvariable=vb, width=9).pack(side="left", padx=(0, 14))
            ttk.Entry(row, textvariable=va, width=9).pack(side="left")

        bottom = ttk.Frame(self, padding=12)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Сохранить", style="Accent.TButton", command=self._save).pack(side="right", padx=(8, 0))
        ttk.Button(bottom, text="Отмена", command=self.destroy).pack(side="right")

    def _set_all(self, val: bool):
        for v in self.vars_enabled.values():
            v.set(val)

    def _clear_gaps(self):
        for v in self.vars_before.values():
            v.set("")
        for v in self.vars_after.values():
            v.set("")

    @staticmethod
    def _parse_opt_float(txt: str) -> float | None:
        s = txt.strip().replace(",", ".")
        if not s:
            return None
        try:
            return max(0.0, float(s))
        except ValueError:
            return None

    def _save(self):
        disabled = {k for k, v in self.vars_enabled.items() if not v.get()}
        colors = {k: v.get() for k, v in self.vars_color.items()}
        gaps = {}
        for ev in self.vars_enabled:
            b = self._parse_opt_float(self.vars_before[ev].get())
            a = self._parse_opt_float(self.vars_after[ev].get())
            if b is not None or a is not None:
                gaps[ev] = {"before": b, "after": a}
        self.on_save(disabled, colors, gaps)
        self.destroy()


# ==========================================
# Главное приложение
# ==========================================

class OutplayedPremiereApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Outplayed → Premiere Pro Sync")
        self.root.geometry("1280x780")
        self.root.minsize(1060, 660)

        icon_path = find_app_icon()
        if icon_path:
            try:
                self.root.iconbitmap(default=str(icon_path))
            except Exception:
                pass

        self.config = self._load_config()
        self.theme_name = self.config.get("theme", "dark")
        if self.theme_name not in THEMES:
            self.theme_name = "dark"
        self.theme = THEMES[self.theme_name]

        self.tips = TooltipManager(self.root)
        self.all_items: list[dict] = []
        self.all_event_types: list[str] = []
        self.row_to_item: dict[str, dict] = {}
        self.last_generated_xml: Path | None = None
        self.selected_item: dict | None = None
        self._refreshing = False

        self.thumb_cache: dict[Path, tk.PhotoImage] = {}
        self.current_preview_path: Path | None = None
        self._current_photo: tk.PhotoImage | None = None

        self.style = ttk.Style()
        self._build_ui()
        self._apply_theme()
        self._apply_config_to_ui()

        self.root.after(100, self.refresh_library)

    def _load_config(self) -> dict:
        default = {
            "theme": "dark",
            "fps": 0,
            "marker_mode": "action_gap",
            "marker_style": "range",
            "gap_before": 5.0,
            "gap_after": 5.0,
            "default_action_sec": 1.5,
            "embed_video": True,
            "auto_update_xml": True,
            "premiere_exe": str(DEFAULT_PREMIERE_EXE),
            "custom_paths": {},
            "extra_search_dirs": [],
            "manual_videos": [],
            "manual_folders": [],
            "ignored_manual_videos": [],
            "disabled_event_types": [],
            "event_colors": {},
            "per_event_gaps": {},
        }
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                default.update(saved)
            except Exception:
                pass
        return default

    def _save_config(self):
        fps_txt = self.var_fps.get().strip()
        self.config["theme"] = self.theme_name
        self.config["fps"] = 0 if fps_txt == "0" else self._safe_int(fps_txt, 60)
        self.config["marker_mode"] = self.var_mode.get()
        self.config["marker_style"] = self.var_style.get()
        self.config["gap_before"] = self._safe_float(self.var_gap_before.get(), 5.0)
        self.config["gap_after"] = self._safe_float(self.var_gap_after.get(), 5.0)
        self.config["default_action_sec"] = self._safe_float(self.var_action_sec.get(), 1.5)
        self.config["embed_video"] = self.var_embed_video.get()
        self.config["auto_update_xml"] = self.var_auto_update_xml.get()
        self.config["premiere_exe"] = self.var_premiere_path.get().strip() or str(DEFAULT_PREMIERE_EXE)
        try:
            CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _safe_int(val: str, default: int) -> int:
        try:
            v = int(val)
            return v if v > 0 else default
        except Exception:
            return default

    @staticmethod
    def _safe_float(val: str, default: float) -> float:
        try:
            v = float(val.replace(",", "."))
            return max(0.0, v)
        except Exception:
            return default

    def _apply_theme(self):
        t = THEMES[self.theme_name]
        self.theme = t
        is_dark = self.theme_name == "dark"

        self.style.theme_use("clam")
        self.root.configure(bg=t["bg"])
        set_windows_dark_titlebar(self.root, is_dark)
        self.tips.set_theme(t)

        self.style.configure(".", background=t["bg"], foreground=t["fg"], font=("Segoe UI", 9))
        self.style.configure("TFrame", background=t["bg"])
        self.style.configure("Card.TFrame", background=t["panel"])
        self.style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        self.style.configure("Muted.TLabel", background=t["bg"], foreground=t["muted"])
        self.style.configure("Card.TLabel", background=t["panel"], foreground=t["fg"])
        self.style.configure("CardMuted.TLabel", background=t["panel"], foreground=t["muted"])

        self.style.configure(
            "TLabelframe",
            background=t["panel"],
            foreground=t["fg"],
            bordercolor=t["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "TLabelframe.Label",
            background=t["panel"],
            foreground=t["fg"],
            font=("Segoe UI", 9, "bold"),
        )

        self.style.configure(
            "TNotebook",
            background=t["bg"],
            bordercolor=t["border"],
            tabmargins=[0, 2, 0, 0],
        )
        self.style.configure(
            "TNotebook.Tab",
            background=t["card"],
            foreground=t["muted"],
            padding=(16, 8),
            font=("Segoe UI", 9, "bold"),
            borderwidth=0,
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", t["panel"]), ("active", t["input"])],
            foreground=[("selected", t["fg"]), ("active", t["fg"])],
        )

        self.style.configure(
            "TButton",
            background=t["card"],
            foreground=t["fg"],
            bordercolor=t["border"],
            padding=(11, 5),
            relief="flat",
            borderwidth=1,
        )
        self.style.map(
            "TButton",
            background=[("active", t["input"]), ("pressed", t["border"]), ("disabled", t["bg"])],
            foreground=[("disabled", t["muted"])],
        )

        self.style.configure(
            "Accent.TButton",
            background=t["accent"],
            foreground="#ffffff",
            bordercolor=t["accent"],
            padding=(14, 7),
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", t["accent_hover"]), ("pressed", t["accent_pressed"]), ("disabled", t["card"])],
            foreground=[("disabled", t["muted"])],
        )

        self.style.configure(
            "TEntry",
            fieldbackground=t["input"],
            foreground=t["fg"],
            bordercolor=t["border"],
            insertcolor=t["fg"],
            padding=4,
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=t["input"],
            background=t["card"],
            foreground=t["fg"],
            arrowcolor=t["fg"],
            bordercolor=t["border"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", t["input"])],
            foreground=[("readonly", t["fg"])],
            selectbackground=[("readonly", t["select_bg"])],
        )

        self.style.configure("TCheckbutton", background=t["panel"], foreground=t["fg"])
        self.style.map("TCheckbutton", background=[("active", t["panel"])])
        self.style.configure("TRadiobutton", background=t["panel"], foreground=t["fg"])
        self.style.map("TRadiobutton", background=[("active", t["panel"])])

        self.style.configure("TSeparator", background=t["border"])
        self.style.configure("TPanedwindow", background=t["bg"])

        self.style.configure(
            "Treeview",
            background=t["panel"],
            foreground=t["fg"],
            fieldbackground=t["panel"],
            bordercolor=t["border"],
            rowheight=27,
            font=("Segoe UI", 9),
        )
        self.style.map(
            "Treeview",
            background=[("selected", t["select_bg"])],
            foreground=[("selected", "#ffffff")],
        )
        self.style.configure(
            "Treeview.Heading",
            background=t["card"],
            foreground=t["fg"],
            bordercolor=t["border"],
            relief="flat",
            padding=(6, 6),
            font=("Segoe UI", 9, "bold"),
        )
        self.style.map("Treeview.Heading", background=[("active", t["input"])])

        self.style.configure(
            "TScrollbar",
            background=t["card"],
            troughcolor=t["bg"],
            bordercolor=t["bg"],
            arrowcolor=t["muted"],
        )
        self.style.configure(
            "Status.TLabel",
            background=t["card"],
            foreground=t["muted"],
            padding=(10, 5),
        )

        # Обновляем обычные tk-виджеты
        if hasattr(self, "lbl_thumb"):
            self.lbl_thumb.configure(bg=t["preview_bg"], fg=t["muted"])
        if hasattr(self, "txt_guide"):
            self.txt_guide.configure(
                bg=t["panel"],
                fg=t["fg"],
                insertbackground=t["fg"],
                selectbackground=t["select_bg"],
            )
            self.txt_guide.tag_configure("h1", foreground=t["fg"], font=("Segoe UI", 13, "bold"))
            self.txt_guide.tag_configure("h2", foreground=t["accent_hover"] if is_dark else t["accent"], font=("Segoe UI", 10, "bold"))
            self.txt_guide.tag_configure("bold", foreground=t["fg"], font=("Segoe UI", 9, "bold"))
            self.txt_guide.tag_configure("code", foreground=t["ok"], font=("Consolas", 9))
            self.txt_guide.tag_configure("muted", foreground=t["muted"], font=("Segoe UI", 9))

        if hasattr(self, "btn_theme"):
            self.btn_theme.configure(text="☀ Светлая тема" if is_dark else "🌙 Тёмная тема")
        if hasattr(self, "var_theme_ui"):
            self.var_theme_ui.set(self.theme_name)

    def toggle_theme(self, target: str | None = None):
        if target in ("dark", "light"):
            self.theme_name = target
        else:
            self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self._apply_theme()
        self._save_config()

    def _build_ui(self):
        # Верхняя шапка приложения
        header = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        header.pack(fill="x")

        ttk.Label(
            header,
            text="Outplayed → Premiere Pro Sync",
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(0, 14))

        self.btn_refresh = ttk.Button(
            header, text="🔄 Обновить базу", style="Accent.TButton", command=self.refresh_library
        )
        self.btn_refresh.pack(side="left", padx=(0, 8))
        self.tips.attach(
            self.btn_refresh,
            "Заново читает живую базу Outplayed (IndexedDB) и бэкап JSON, проверяет новые видео в добавленных папках и обновляет XML (если включено автообновление).",
        )

        self.btn_theme = ttk.Button(header, text="☀ Светлая тема", command=self.toggle_theme)
        self.btn_theme.pack(side="right", padx=(8, 0))
        self.tips.attach(self.btn_theme, "Мгновенно переключает оформление между тёмной и светлой темой.")

        self.lbl_lib_info = ttk.Label(header, text="База: загрузка...", style="Muted.TLabel")
        self.lbl_lib_info.pack(side="right", padx=(0, 8))

        # Вкладки
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.tab_main = ttk.Frame(self.notebook, padding=8)
        self.tab_settings = ttk.Frame(self.notebook, padding=12)
        self.tab_guide = ttk.Frame(self.notebook, padding=12)

        self.notebook.add(self.tab_main, text="  🎬 Видео и XML  ")
        self.notebook.add(self.tab_settings, text="  ⚙ Настройки меток и экспорта  ")
        self.notebook.add(self.tab_guide, text="  📖 Руководство и Инфо  ")

        self._build_main_tab()
        self._build_settings_tab()
        self._build_guide_tab()

        # Нижняя строка состояния
        self.var_status = tk.StringVar(value="Готово.")
        ttk.Label(
            self.root, textvariable=self.var_status, style="Status.TLabel", anchor="w"
        ).pack(fill="x", side="bottom")

    def _build_main_tab(self):
        pane = ttk.PanedWindow(self.tab_main, orient="horizontal")
        pane.pack(fill="both", expand=True)

        # ---- Левая часть: панель инструментов + таблица ----
        left_frame = ttk.Frame(pane)
        pane.add(left_frame, weight=3)

        row_tools_1 = ttk.Frame(left_frame, padding=(0, 0, 0, 6))
        row_tools_1.pack(fill="x")

        ttk.Label(row_tools_1, text="Поиск:").pack(side="left")
        self.var_search = tk.StringVar()
        self.var_search.trace_add("write", lambda *_: self._filter_table())
        ent_search = ttk.Entry(row_tools_1, textvariable=self.var_search, width=28)
        ent_search.pack(side="left", padx=(6, 10))
        self.tips.attach(ent_search, "Фильтрует список по названию видео, папке, типам меток или полному пути.")

        btn_add_vid = ttk.Button(row_tools_1, text="+ Добавить видео…", command=self.add_manual_files)
        btn_add_vid.pack(side="left", padx=3)
        self.tips.attach(
            btn_add_vid,
            "Добавляет выбранные видеофайлы в список и СРАЗУ создаёт рядом с каждым файл *_markers.xml для Premiere Pro.",
        )

        btn_add_dir = ttk.Button(row_tools_1, text="+ Добавить папку…", command=self.add_manual_folder)
        btn_add_dir.pack(side="left", padx=3)
        self.tips.attach(
            btn_add_dir,
            "Добавляет все видео из выбранной папки, запоминает её для будущих обновлений и сразу создаёт *_markers.xml.",
        )

        btn_remove = ttk.Button(row_tools_1, text="Убрать добавленные", command=self.remove_selected_manual)
        btn_remove.pack(side="left", padx=3)
        self.tips.attach(
            btn_remove,
            "Удаляет из списка выбранные видео, которые были добавлены вручную (оригинальные записи Outplayed не удаляются).",
        )

        btn_auto = ttk.Button(row_tools_1, text="Найти потерянные", command=self.run_auto_find)
        btn_auto.pack(side="right")
        self.btn_auto_find = btn_auto
        self.tips.attach(
            btn_auto,
            "Ищет на диске переименованные или перемещённые видео из базы Outplayed (сравнивает размер файла и длительность).",
        )

        # Таблица с горизонтальной и вертикальной прокруткой
        tree_wrap = ttk.Frame(left_frame)
        tree_wrap.pack(fill="both", expand=True)
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        cols = ("name", "folder", "dur", "events", "xml", "status", "video_path", "xml_path")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("name", text="Видеофайл")
        self.tree.heading("folder", text="Игра / Папка")
        self.tree.heading("dur", text="Длит.")
        self.tree.heading("events", text="Метки")
        self.tree.heading("xml", text="XML")
        self.tree.heading("status", text="Статус")
        self.tree.heading("video_path", text="Полный путь к видео")
        self.tree.heading("xml_path", text="Полный путь к XML")

        self.tree.column("name", width=230, minwidth=140, stretch=True)
        self.tree.column("folder", width=130, minwidth=90, stretch=False)
        self.tree.column("dur", width=60, minwidth=50, anchor="center", stretch=False)
        self.tree.column("events", width=185, minwidth=120, stretch=False)
        self.tree.column("xml", width=65, minwidth=55, anchor="center", stretch=False)
        self.tree.column("status", width=85, minwidth=70, anchor="center", stretch=False)
        self.tree.column("video_path", width=360, minwidth=180, stretch=False)
        self.tree.column("xml_path", width=360, minwidth=180, stretch=False)

        ysb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

        # ---- Правая часть: превью + пути + действия ----
        right_frame = ttk.Frame(pane, padding=(10, 0, 0, 0))
        pane.add(right_frame, weight=1)

        grp_prev = ttk.LabelFrame(right_frame, text="Превью видео", padding=10)
        grp_prev.pack(fill="x", pady=(0, 8))

        self.lbl_thumb = tk.Label(
            grp_prev,
            text="Выбери видео в таблице",
            width=40,
            height=9,
            font=("Segoe UI", 9),
        )
        self.lbl_thumb.pack(fill="x")
        self.lbl_vid_meta = ttk.Label(grp_prev, text="", style="CardMuted.TLabel", font=("Segoe UI", 8))
        self.lbl_vid_meta.pack(anchor="w", pady=(6, 0))

        # Карточка путей выбранного видео
        grp_paths = ttk.LabelFrame(right_frame, text="Пути выбранной записи", padding=10)
        grp_paths.pack(fill="x", pady=(0, 8))

        self.var_info_disk = tk.StringVar(value="—")
        self.var_info_orig = tk.StringVar(value="—")
        self.var_info_xml = tk.StringVar(value="—")

        ttk.Label(grp_paths, text="Видео на диске:", style="CardMuted.TLabel", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        ttk.Entry(grp_paths, textvariable=self.var_info_disk, state="readonly").pack(fill="x", pady=(2, 6))

        ttk.Label(grp_paths, text="Запись в базе Outplayed:", style="CardMuted.TLabel", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        ttk.Entry(grp_paths, textvariable=self.var_info_orig, state="readonly").pack(fill="x", pady=(2, 6))

        ttk.Label(grp_paths, text="Файл XML (маркеры):", style="CardMuted.TLabel", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        ttk.Entry(grp_paths, textvariable=self.var_info_xml, state="readonly").pack(fill="x", pady=(2, 6))

        path_btns = ttk.Frame(grp_paths, style="Card.TFrame")
        path_btns.pack(fill="x")
        btn_open_vid_dir = ttk.Button(path_btns, text="Папка видео", command=self.open_selected_video_folder)
        btn_open_vid_dir.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.tips.attach(btn_open_vid_dir, "Открывает Проводник и выделяет выбранный видеофайл.")

        self.btn_reset_bind = ttk.Button(path_btns, text="Сбросить привязку", command=self.reset_manual_binding)
        self.btn_reset_bind.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.tips.attach(
            self.btn_reset_bind,
            "Отменяет ручную замену видеофайла и возвращает оригинальный путь из базы Outplayed.",
        )

        # Кнопки экспорта
        grp_act = ttk.LabelFrame(right_frame, text="Экспорт в Premiere Pro", padding=10)
        grp_act.pack(fill="x")

        btn_prem = ttk.Button(
            grp_act,
            text="▶ Открыть в Premiere Pro",
            style="Accent.TButton",
            command=lambda: self.export_selected(open_in_premiere=True),
        )
        btn_prem.pack(fill="x", pady=(0, 6))
        self.tips.attach(
            btn_prem,
            "Создаёт/обновляет XML для выбранных видео (с текущими настройками меток) и сразу отправляет его в Adobe Premiere Pro.",
        )

        act_row = ttk.Frame(grp_act, style="Card.TFrame")
        act_row.pack(fill="x", pady=(0, 6))

        btn_xml = ttk.Button(
            act_row, text="Создать / обновить XML", command=lambda: self.export_selected(open_in_premiere=False)
        )
        btn_xml.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.tips.attach(
            btn_xml,
            "Генерирует файл *_markers.xml рядом с выбранным видео без запуска Premiere Pro.",
        )

        btn_xml_dir = ttk.Button(act_row, text="Папка с XML", command=self.open_last_xml_folder)
        btn_xml_dir.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.tips.attach(btn_xml_dir, "Открывает в Проводнике папку с последним созданным или выбранным XML-файлом.")

        btn_rebind = ttk.Button(
            grp_act, text="Привязать другой видеофайл…", command=self.locate_manual_file
        )
        btn_rebind.pack(fill="x")
        self.tips.attach(
            btn_rebind,
            "Привязывает метки выбранной записи к другому видеофайлу на диске (например, если ролик был переименован или перенесён).",
        )

    def _build_settings_tab(self):
        # Две колонки аккуратных карточек
        cols = ttk.Frame(self.tab_settings)
        cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=1)

        # ---- Левая колонка: Настройки меток ----
        grp_markers = ttk.LabelFrame(cols, text="Режим меток и гапы (тайминги)", padding=12)
        grp_markers.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))

        self.var_mode = tk.StringVar(value="action_gap")
        r1 = ttk.Radiobutton(
            grp_markers,
            text="1) Как обычно (одиночная точка в момент события)",
            variable=self.var_mode,
            value="normal",
            command=self._toggle_gap_inputs,
        )
        r1.pack(anchor="w", pady=2)
        self.tips.attach(r1, "Ставит обычный точечный маркер ровно на таймкоде события без длительности.")

        r2 = ttk.Radiobutton(
            grp_markers,
            text="2) Только действие (от начала до конца события)",
            variable=self.var_mode,
            value="action",
            command=self._toggle_gap_inputs,
        )
        r2.pack(anchor="w", pady=2)
        self.tips.attach(r2, "Создаёт маркер по длительности самого события (без дополнительного запаса до и после).")

        r3 = ttk.Radiobutton(
            grp_markers,
            text="3) Действие + гап (буфер секунд до и после события)",
            variable=self.var_mode,
            value="action_gap",
            command=self._toggle_gap_inputs,
        )
        r3.pack(anchor="w", pady=2)
        self.tips.attach(r3, "Добавляет буфер секунд ДО и ПОСЛЕ события — удобно для нарезки хайлайтов.")

        self.frm_gaps = ttk.Frame(grp_markers, style="Card.TFrame", padding=(18, 8, 0, 4))
        self.frm_gaps.pack(fill="x")

        g1 = ttk.Frame(self.frm_gaps, style="Card.TFrame")
        g1.pack(fill="x", pady=3)
        ttk.Label(g1, text="Общий буфер — До (сек):", style="Card.TLabel").pack(side="left")
        self.var_gap_before = tk.StringVar(value="5")
        e_gb = ttk.Entry(g1, textvariable=self.var_gap_before, width=7)
        e_gb.pack(side="left", padx=(6, 12))
        self.tips.attach(e_gb, "Сколько секунд добавить ДО начала события (если не задано индивидуально).")

        ttk.Label(g1, text="После (сек):", style="Card.TLabel").pack(side="left")
        self.var_gap_after = tk.StringVar(value="5")
        e_ga = ttk.Entry(g1, textvariable=self.var_gap_after, width=7)
        e_ga.pack(side="left", padx=6)
        self.tips.attach(e_ga, "Сколько секунд добавить ПОСЛЕ окончания события (если не задано индивидуально).")

        g2 = ttk.Frame(self.frm_gaps, style="Card.TFrame")
        g2.pack(fill="x", pady=3)
        ttk.Label(g2, text="Мин. длительность действия (сек):", style="Card.TLabel").pack(side="left")
        self.var_action_sec = tk.StringVar(value="1.5")
        e_act = ttk.Entry(g2, textvariable=self.var_action_sec, width=7)
        e_act.pack(side="left", padx=6)
        self.tips.attach(e_act, "Используется, если в событии Outplayed не записана его собственная длительность.")

        g3 = ttk.Frame(self.frm_gaps, style="Card.TFrame")
        g3.pack(fill="x", pady=(8, 2))
        btn_fetch_gaps = ttk.Button(
            g3, text="Взять гапы из Outplayed", command=self.load_gaps_from_outplayed
        )
        btn_fetch_gaps.pack(side="left", padx=(0, 8))
        self.tips.attach(
            btn_fetch_gaps,
            "Сканирует IndexedDB (V8), LocalStorage и логи Outplayed в поисках сохранённых секунд До/После.",
        )

        btn_ev_cfg = ttk.Button(
            g3, text="Фильтр, цвета и гапы событий…", command=self.open_event_settings
        )
        btn_ev_cfg.pack(side="left")
        self.tips.attach(
            btn_ev_cfg,
            "Открывает таблицу всех событий (KILL, DEATH, ASSIST и др.): отключение ненужных, выбор цвета в Premiere и индивидуальные секунды До/После.",
        )

        ttk.Separator(grp_markers, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(grp_markers, text="Вид меток в Premiere Pro:", style="Card.TLabel", font=("Segoe UI", 9, "bold")).pack(
            anchor="w", pady=(0, 4)
        )

        self.var_style = tk.StringVar(value="range")
        rs1 = ttk.Radiobutton(
            grp_markers,
            text="Растянутый маркер (единая цветная полоса от старта до конца)",
            variable=self.var_style,
            value="range",
        )
        rs1.pack(anchor="w", pady=2)
        self.tips.attach(rs1, "В Premiere Pro маркер будет растянут по длительности (удобно тянуть и видеть границы фрагмента).")

        rs2 = ttk.Radiobutton(
            grp_markers,
            text="Две отдельные метки: [START] и [END]",
            variable=self.var_style,
            value="two_markers",
        )
        rs2.pack(anchor="w", pady=2)
        self.tips.attach(rs2, "Ставит две обычные точечные метки — на начало и на конец фрагмента.")

        # ---- Правая колонка: Видео, Premiere Pro, Автообновление, Тема ----
        right_col = ttk.Frame(cols)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))

        grp_vid = ttk.LabelFrame(right_col, text="Видео, XML и автоматизация", padding=12)
        grp_vid.pack(fill="x", pady=(0, 10))

        fps_row = ttk.Frame(grp_vid, style="Card.TFrame")
        fps_row.pack(fill="x", pady=3)
        ttk.Label(fps_row, text="FPS секвенции (0 = авто из MP4):", style="Card.TLabel").pack(side="left")
        self.var_fps = tk.StringVar(value="0")
        e_fps = ttk.Entry(fps_row, textvariable=self.var_fps, width=7)
        e_fps.pack(side="left", padx=8)
        self.tips.attach(e_fps, "При значении 0 частота кадров считывается автоматически из заголовка MP4/MOV.")

        self.var_embed_video = tk.BooleanVar(value=True)
        chk_embed = ttk.Checkbutton(
            grp_vid,
            text="Вставлять видео + все аудиодорожки в XML",
            variable=self.var_embed_video,
            command=self._save_config,
        )
        chk_embed.pack(anchor="w", pady=4)
        self.tips.attach(chk_embed, "Включает в секвенцию сам видеофайл и все его аудиодорожки (микрофон, игра, Discord и т.д.).")

        self.var_auto_update_xml = tk.BooleanVar(value=True)
        chk_auto_xml = ttk.Checkbutton(
            grp_vid,
            text="Авто-обновлять XML при запуске и нажатии «Обновить»",
            variable=self.var_auto_update_xml,
            command=self._save_config,
        )
        chk_auto_xml.pack(anchor="w", pady=4)
        self.tips.attach(
            chk_auto_xml,
            "Автоматически создаёт и обновляет *_markers.xml для всех найденных видео при запуске программы и обновлении базы.",
        )

        btn_search_dir = ttk.Button(
            grp_vid, text="Добавить папку поиска переименованных видео…", command=self.add_search_folder
        )
        btn_search_dir.pack(anchor="w", pady=(8, 2))
        self.tips.attach(
            btn_search_dir,
            "Добавляет дополнительную папку, где программа будет искать переименованные ролики из базы Outplayed.",
        )

        grp_prem = ttk.LabelFrame(right_col, text="Adobe Premiere Pro", padding=12)
        grp_prem.pack(fill="x", pady=(0, 10))

        ttk.Label(grp_prem, text="Путь к Adobe Premiere Pro.exe:", style="CardMuted.TLabel").pack(anchor="w")
        prem_row = ttk.Frame(grp_prem, style="Card.TFrame")
        prem_row.pack(fill="x", pady=(4, 2))
        self.var_premiere_path = tk.StringVar(value=str(DEFAULT_PREMIERE_EXE))
        ttk.Entry(prem_row, textvariable=self.var_premiere_path).pack(side="left", fill="x", expand=True)
        btn_prem_browse = ttk.Button(prem_row, text="Обзор…", command=self.browse_premiere)
        btn_prem_browse.pack(side="left", padx=(6, 0))
        self.tips.attach(
            btn_prem_browse,
            "По умолчанию используется D:\\Apps\\Adobe\\Adobe Premiere Pro 2026\\Adobe Premiere Pro.exe (или автопоиск в системе).",
        )

        grp_theme = ttk.LabelFrame(right_col, text="Оформление", padding=12)
        grp_theme.pack(fill="x")

        self.var_theme_ui = tk.StringVar(value=self.theme_name)
        theme_row = ttk.Frame(grp_theme, style="Card.TFrame")
        theme_row.pack(fill="x")
        ttk.Radiobutton(
            theme_row, text="🌙 Тёмная тема", variable=self.var_theme_ui, value="dark",
            command=lambda: self.toggle_theme("dark")
        ).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(
            theme_row, text="☀ Светлая тема", variable=self.var_theme_ui, value="light",
            command=lambda: self.toggle_theme("light")
        ).pack(side="left")

        btn_save_settings = ttk.Button(
            right_col, text="Сохранить настройки", style="Accent.TButton", command=self._on_save_settings_click
        )
        btn_save_settings.pack(anchor="e", pady=(12, 0))
        self.tips.attach(btn_save_settings, "Сохраняет все параметры в файл конфигурации ~/.outplayed_premiere_sync.json.")

    def _build_guide_tab(self):
        info_bar = ttk.Frame(self.tab_guide, padding=(0, 0, 0, 8))
        info_bar.pack(fill="x")

        ttk.Label(
            info_bar,
            text=f"Outplayed → Premiere Pro Sync  v{APP_VERSION}",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        btn_overwolf = ttk.Button(info_bar, text="Папка базы Overwolf", command=self.open_overwolf_folder)
        btn_overwolf.pack(side="right", padx=(6, 0))
        self.tips.attach(btn_overwolf, "Открывает каталог %LOCALAPPDATA%\\Overwolf, где хранится живая база IndexedDB.")

        btn_cfg = ttk.Button(info_bar, text="Папка конфига", command=self.open_config_folder)
        btn_cfg.pack(side="right")
        self.tips.attach(btn_cfg, f"Открывает папку с файлом настроек {CONFIG_PATH.name}.")

        wrap = ttk.Frame(self.tab_guide)
        wrap.pack(fill="both", expand=True)

        self.txt_guide = tk.Text(
            wrap,
            wrap="word",
            relief="flat",
            padx=16,
            pady=14,
            spacing1=2,
            spacing2=2,
            spacing3=4,
            font=("Segoe UI", 10),
        )
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt_guide.yview)
        self.txt_guide.configure(yscrollcommand=sb.set)
        self.txt_guide.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._populate_guide_text()
        self.txt_guide.configure(state="disabled")

    def _populate_guide_text(self):
        t = self.txt_guide
        t.delete("1.0", "end")

        sections = [
            ("h1", "Руководство пользователя\n\n"),
            ("h2", "1. Откуда программа берёт видео и метки\n"),
            ("normal", "• При каждом запуске и по кнопке "),
            ("bold", "«🔄 Обновить базу»"),
            ("normal", " программа читает "),
            ("bold", "живую базу Overwolf IndexedDB (формат V8)"),
            ("normal", " и резервный файл "),
            ("code", "library-*.json"),
            ("normal", ". Поэтому самые свежие записи попадают в список сразу после матча, не дожидаясь бэкапа Outplayed.\n"
                       "• Рядом с каждым найденным видео создаётся файл "),
            ("code", "<имя_видео>_markers.xml"),
            ("normal", " (если включена опция автообновления XML).\n\n"),

            ("h2", "2. Что делают кнопки на главной вкладке «🎬 Видео и XML»\n"),
            ("bold", "• + Добавить видео…"),
            ("normal", " — позволяет выбрать любые сторонние ролики (OBS, ShadowPlay, телефон и т.д.). Сразу добавляет их в список и создаёт рядом XML.\n"),
            ("bold", "• + Добавить папку…"),
            ("normal", " — добавляет все видео из выбранной папки, запоминает её (новые файлы будут появляться сами при обновлении) и создаёт XML.\n"),
            ("bold", "• Убрать добавленные"),
            ("normal", " — убирает из списка только те видео, которые были добавлены вручную.\n"),
            ("bold", "• Найти потерянные"),
            ("normal", " — ищет на диске переименованные или перемещённые ролики из Outplayed по точному размеру файла и длительности.\n"),
            ("bold", "• ▶ Открыть в Premiere Pro"),
            ("normal", " — обновляет XML для выделенных строк и сразу передаёт секвенцию (видео + все аудиодорожки + цветные метки) в Adobe Premiere Pro.\n"),
            ("bold", "• Создать / обновить XML"),
            ("normal", " — генерирует или перезаписывает "),
            ("code", "*_markers.xml"),
            ("normal", " без запуска Premiere Pro.\n"),
            ("bold", "• Привязать другой видеофайл…"),
            ("normal", " — привязывает метки из выбранной строки Outplayed к другому видеофайлу на диске. В карточке справа всегда видны оба пути и кнопка "),
            ("bold", "«Сбросить привязку»"),
            ("normal", ", если нужно вернуть исходный файл.\n\n"),

            ("h2", "3. Вкладка «⚙ Настройки меток и экспорта»\n"),
            ("normal", "• "),
            ("bold", "Режим 1 (Точка): "),
            ("normal", "одиночный маркер ровно в секунду события.\n• "),
            ("bold", "Режим 2 (Только действие): "),
            ("normal", "маркер от начала до конца события.\n• "),
            ("bold", "Режим 3 (Действие + гап): "),
            ("normal", "добавляет запас секунд «До» и «После» события (например, 8 сек до убийства и 4 сек после).\n• "),
            ("bold", "Взять гапы из Outplayed: "),
            ("normal", "пробует вытащить настроенные секунды До/После из IndexedDB, LocalStorage и логов Overwolf.\n• "),
            ("bold", "Фильтр, цвета и гапы событий…: "),
            ("normal", "можно выключить лишние события (например, DEATH), задать цвет каждого типа маркеров в Premiere Pro и указать индивидуальные секунды До/После.\n\n"),

            ("h2", "4. Полезная информация и сборка .exe\n"),
            ("normal", "• Конфигурация хранится в: "),
            ("code", f"{CONFIG_PATH}\n"),
            ("normal", "• Путь к Premiere Pro по умолчанию: "),
            ("code", f"{DEFAULT_PREMIERE_EXE}\n"),
            ("normal", "• Сборка в единый .exe со своей иконкой:\n  "),
            ("code", 'pyinstaller --noconsole --onefile --name "OutplayedPremiereSync" --icon="circular-app-icon-logo_cropped.ico" --add-data "circular-app-icon-logo_cropped.ico;." outplayed_sync.py\n'),
        ]

        for tag, txt in sections:
            if tag == "normal":
                t.insert("end", txt)
            else:
                t.insert("end", txt, tag)

    def _on_save_settings_click(self):
        self._save_config()
        self.var_status.set("Настройки сохранены.")
        messagebox.showinfo("Настройки", "Настройки сохранены.")

    def _apply_config_to_ui(self):
        self.var_fps.set(str(self.config.get("fps", 0)))
        self.var_mode.set(self.config.get("marker_mode", "action_gap"))
        self.var_style.set(self.config.get("marker_style", "range"))
        self.var_gap_before.set(str(self.config.get("gap_before", 5.0)))
        self.var_gap_after.set(str(self.config.get("gap_after", 5.0)))
        self.var_action_sec.set(str(self.config.get("default_action_sec", 1.5)))
        self.var_embed_video.set(bool(self.config.get("embed_video", True)))
        self.var_auto_update_xml.set(bool(self.config.get("auto_update_xml", True)))
        self.var_premiere_path.set(self.config.get("premiere_exe", str(DEFAULT_PREMIERE_EXE)))
        self._toggle_gap_inputs()

    def _toggle_gap_inputs(self):
        mode = self.var_mode.get()
        state = "normal" if mode in ("action", "action_gap") else "disabled"
        for child in self.frm_gaps.winfo_children():
            try:
                child.configure(state=state)
            except tk.TclError:
                for sub in child.winfo_children():
                    try:
                        sub.configure(state=state)
                    except tk.TclError:
                        pass

    @staticmethod
    def _get_expected_xml_path(item: dict) -> Path:
        video_path = item["resolved_path"]
        fallback_name = Path(item["path"]).stem
        if video_path and video_path.parent.exists():
            return video_path.with_name(f"{video_path.stem}_markers.xml")
        return Path.home() / "Videos" / "Outplayed_Markers" / f"{fallback_name}_markers.xml"

    def _write_xml_for_item(self, item: dict) -> tuple[Path, int]:
        fps_txt = self.var_fps.get().strip()
        fps = 0 if fps_txt == "0" else self._safe_int(fps_txt, 60)
        mode = self.var_mode.get()
        style = self.var_style.get()
        gap_before = self._safe_float(self.var_gap_before.get(), 5.0)
        gap_after = self._safe_float(self.var_gap_after.get(), 5.0)
        action_sec = self._safe_float(self.var_action_sec.get(), 1.5)
        embed_video = self.var_embed_video.get()

        disabled = set(self.config.get("disabled_event_types", []))
        enabled_types = set(self.all_event_types) - disabled if self.all_event_types else None
        event_colors = self.config.get("event_colors", {})
        per_event_gaps = self.config.get("per_event_gaps", {})

        video_path = item["resolved_path"]
        out_xml = self._get_expected_xml_path(item)
        seq_name = f"{(video_path.stem if video_path else Path(item['path']).stem)}_Markers"

        count = build_premiere_xml(
            output_xml_path=out_xml,
            events=item["raw"]["events"],
            sequence_name=seq_name,
            fps=fps,
            duration_sec=item["duration_sec"],
            video_path=video_path,
            embed_video=embed_video,
            marker_mode=mode,
            marker_style=style,
            gap_before=gap_before,
            gap_after=gap_after,
            default_action_sec=action_sec,
            enabled_event_types=enabled_types,
            event_colors=event_colors,
            per_event_gaps=per_event_gaps,
        )
        item["xml_exists"] = out_xml.exists()
        item["xml_path"] = out_xml
        return out_xml, count

    def _build_manual_item(self, vid_path: Path) -> dict:
        dur = 0.0
        if vid_path.suffix.lower() in {".mp4", ".mov"}:
            meta = parse_mp4_metadata(vid_path)
            if meta and meta["duration"] > 0:
                dur = meta["duration"]
        mtime = vid_path.stat().st_mtime if vid_path.exists() else time.time()
        xml_path = vid_path.with_name(f"{vid_path.stem}_markers.xml")
        return {
            "raw": {"path": str(vid_path), "events": []},
            "path": str(vid_path),
            "resolved_path": vid_path if vid_path.exists() else None,
            "status": "Найден" if vid_path.exists() else "Не найден",
            "duration_sec": dur,
            "events_summary": "0 (добавлен вручную)",
            "folder": vid_path.parent.name,
            "mtime": mtime,
            "is_manual": True,
            "is_rebound": False,
            "xml_exists": xml_path.exists(),
            "xml_path": xml_path,
        }

    def refresh_library(self):
        if self._refreshing:
            return
        self._refreshing = True
        self.btn_refresh.configure(state="disabled")
        self.var_status.set("Читаю живую базу Outplayed (IndexedDB) и бэкап...")

        def worker():
            lib_path = find_latest_library_json()
            backup_medias: list[dict] = []
            read_err = None

            if lib_path:
                try:
                    raw_data = json.loads(lib_path.read_text(encoding="utf-8"))
                    data = deep_json_loads(raw_data)
                    backup_medias = collect_medias_with_events(data)
                except Exception as e:
                    read_err = str(e)

            idb_medias, idb_count = load_outplayed_indexeddb_medias()
            self.root.after(0, lambda: self._finish_refresh(lib_path, backup_medias, idb_medias, idb_count, read_err))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_refresh(
        self,
        lib_path: Path | None,
        backup_medias: list[dict],
        idb_medias: list[dict],
        idb_count: int,
        read_err: str | None,
    ):
        try:
            if read_err:
                messagebox.showerror("Ошибка чтения backup JSON", read_err)

            now_str = datetime.now().strftime("%H:%M:%S")
            src_parts = []
            if idb_medias:
                src_parts.append(f"IndexedDB ({idb_count})")
            if lib_path:
                src_parts.append(lib_path.name)
            if src_parts:
                self.lbl_lib_info.configure(text=f"База: {' + '.join(src_parts)} (обновлено {now_str})")
            else:
                self.lbl_lib_info.configure(text=f"База не найдена ({now_str})")

            unique_medias: dict[str, dict] = {}
            for m in backup_medias + idb_medias:
                p = m.get("path")
                if not isinstance(p, str) or not p:
                    continue
                norm_key = os.path.normcase(os.path.normpath(p))
                prev = unique_medias.get(norm_key)
                if prev is None or len(m.get("events", [])) >= len(prev.get("events", [])):
                    unique_medias[norm_key] = m

            custom_paths = self.config.get("custom_paths", {})
            items = []
            ev_type_set: set[str] = set()
            known_resolved: set[str] = set()

            for m in unique_medias.values():
                orig_str = m["path"]
                orig_path = Path(orig_str)
                resolved_path: Path | None = None
                status = "Не найден"
                is_rebound = False

                if orig_str in custom_paths and Path(custom_paths[orig_str]).exists():
                    resolved_path = Path(custom_paths[orig_str])
                    status = "Перепривязан"
                    is_rebound = True
                elif orig_path.exists():
                    resolved_path = orig_path
                    status = "Найден"

                if resolved_path:
                    known_resolved.add(os.path.normcase(str(resolved_path.resolve())))

                dur = get_media_duration_sec(m)
                counts = Counter()
                for e in m["events"]:
                    if isinstance(e, dict):
                        t = str(e.get("type") or e.get("name") or "Event").strip().upper()
                        counts[t] += 1
                        ev_type_set.add(t)
                stats = ", ".join(f"{k}: {v}" for k, v in counts.most_common(3))
                mtime = resolved_path.stat().st_mtime if resolved_path else float(m.get("startTime", 0) or 0)

                item = {
                    "raw": m,
                    "path": orig_str,
                    "resolved_path": resolved_path,
                    "status": status,
                    "duration_sec": dur,
                    "events_summary": f"{len(m['events'])} ({stats})",
                    "folder": (resolved_path.parent.name if resolved_path else orig_path.parent.name),
                    "mtime": mtime,
                    "is_manual": False,
                    "is_rebound": is_rebound,
                    "xml_exists": False,
                    "xml_path": None,
                }
                xml_p = self._get_expected_xml_path(item)
                item["xml_path"] = xml_p
                item["xml_exists"] = xml_p.exists()
                items.append(item)

            ignored_set = {os.path.normcase(p) for p in self.config.get("ignored_manual_videos", [])}
            manual_cfg = list(self.config.get("manual_videos", []))
            valid_folders: list[dict] = []

            for f_entry in self.config.get("manual_folders", []):
                f_path = Path(f_entry["path"])
                if not f_path.exists():
                    continue
                valid_folders.append(f_entry)
                recursive = bool(f_entry.get("recursive", False))
                iterator = f_path.rglob("*") if recursive else f_path.iterdir()
                try:
                    for p in iterator:
                        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                            rp_str = str(p.resolve())
                            rp_norm = os.path.normcase(rp_str)
                            if rp_norm not in ignored_set and rp_norm not in {os.path.normcase(x) for x in manual_cfg}:
                                manual_cfg.append(rp_str)
                except Exception:
                    pass

            self.config["manual_folders"] = valid_folders

            valid_manual: list[str] = []
            for m_str in manual_cfg:
                mp = Path(m_str)
                if not mp.exists():
                    continue
                rmp = mp.resolve()
                rmp_norm = os.path.normcase(str(rmp))
                if rmp_norm in ignored_set:
                    continue
                valid_manual.append(str(rmp))
                if rmp_norm in known_resolved:
                    continue
                known_resolved.add(rmp_norm)
                items.append(self._build_manual_item(rmp))

            self.config["manual_videos"] = valid_manual
            self.all_event_types = sorted(ev_type_set)

            updated_xml_count = 0
            if self.var_auto_update_xml.get():
                for item in items:
                    if item["resolved_path"] and item["resolved_path"].exists():
                        out_xml, _ = self._write_xml_for_item(item)
                        self.last_generated_xml = out_xml
                        updated_xml_count += 1

            self._save_config()

            items.sort(key=lambda x: x["mtime"], reverse=True)
            self.all_items = items
            self._filter_table()
            missing_count = sum(1 for i in items if i["resolved_path"] is None)

            status_msg = f"Обновлено: видео {len(items)}, не найдено {missing_count}."
            if self.var_auto_update_xml.get():
                status_msg += f" Обновлено XML: {updated_xml_count}."
            self.var_status.set(status_msg)
        finally:
            self._refreshing = False
            self.btn_refresh.configure(state="normal")

    def _add_paths_to_list(self, paths: list[Path]):
        existing = {
            os.path.normcase(str(i["resolved_path"].resolve()))
            for i in self.all_items if i["resolved_path"]
        }
        added_paths: list[str] = []
        manual_cfg = self.config.setdefault("manual_videos", [])
        ignored_cfg = self.config.setdefault("ignored_manual_videos", [])
        created_xml = 0

        for p in paths:
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
                continue
            rp = p.resolve()
            rp_str = str(rp)
            rp_norm = os.path.normcase(rp_str)

            ignored_cfg[:] = [x for x in ignored_cfg if os.path.normcase(x) != rp_norm]

            if rp_norm in existing:
                for item in self.all_items:
                    if item["resolved_path"] and os.path.normcase(str(item["resolved_path"].resolve())) == rp_norm:
                        out_xml, _ = self._write_xml_for_item(item)
                        self.last_generated_xml = out_xml
                        created_xml += 1
                continue

            existing.add(rp_norm)
            item = self._build_manual_item(rp)
            out_xml, _ = self._write_xml_for_item(item)
            self.last_generated_xml = out_xml
            created_xml += 1

            self.all_items.insert(0, item)
            if rp_norm not in {os.path.normcase(x) for x in manual_cfg}:
                manual_cfg.append(rp_str)
            added_paths.append(rp_str)

        if not added_paths and created_xml == 0:
            self.var_status.set("Новых видео не найдено.")
            return

        self._save_config()
        self._filter_table()

        to_select = [
            iid for iid, item in self.row_to_item.items()
            if item["path"] in added_paths
        ]
        if to_select:
            self.tree.selection_set(to_select)
            self.tree.see(to_select[0])
            self._on_row_select()

        self.var_status.set(f"Добавлено видео: {len(added_paths)}. Создано/обновлено XML: {created_xml}.")

    def add_manual_files(self):
        chosen = filedialog.askopenfilenames(
            title="Выбери видеофайлы (для них сразу создадутся XML)",
            filetypes=[("Видеофайлы", "*.mp4 *.mov *.mkv *.avi"), ("Все файлы", "*.*")],
        )
        if not chosen:
            return
        self._add_paths_to_list([Path(c) for c in chosen])

    def add_manual_folder(self):
        folder = filedialog.askdirectory(title="Выбери папку с видео (для всех создадутся XML)")
        if not folder:
            return
        root_dir = Path(folder).resolve()
        recursive = messagebox.askyesno(
            "Вложенные папки",
            "Искать видеофайлы также во вложенных папках?",
        )
        folders_cfg = self.config.setdefault("manual_folders", [])
        if not any(os.path.normcase(str(Path(f["path"]).resolve())) == os.path.normcase(str(root_dir)) for f in folders_cfg):
            folders_cfg.append({"path": str(root_dir), "recursive": recursive})

        iterator = root_dir.rglob("*") if recursive else root_dir.iterdir()
        found = sorted(
            (p for p in iterator if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not found:
            self._save_config()
            messagebox.showinfo("Пусто", "В выбранной папке не найдено видеофайлов (.mp4, .mov, .mkv, .avi).")
            return
        self._add_paths_to_list(found)

    def remove_selected_manual(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Выбор", "Выбери в списке вручную добавленные видео, которые нужно убрать.")
            return

        removed = 0
        manual_cfg = self.config.get("manual_videos", [])
        ignored_cfg = self.config.setdefault("ignored_manual_videos", [])
        to_remove_items = set()

        for iid in sel:
            item = self.row_to_item.get(iid)
            if item and item.get("is_manual"):
                to_remove_items.add(item["path"])
                norm_p = os.path.normcase(item["path"])
                manual_cfg[:] = [x for x in manual_cfg if os.path.normcase(x) != norm_p]
                if norm_p not in {os.path.normcase(x) for x in ignored_cfg}:
                    ignored_cfg.append(item["path"])
                removed += 1

        if not removed:
            messagebox.showinfo("Информация", "Убрать можно только видео, добавленные вручную (через кнопки «+ Добавить…»).")
            return

        self.all_items = [i for i in self.all_items if not (i.get("is_manual") and i["path"] in to_remove_items)]
        self._save_config()
        self._filter_table()
        self.var_status.set(f"Убрано добавленных видео: {removed}.")

    def _filter_table(self):
        query = self.var_search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        self.row_to_item.clear()

        for item in self.all_items:
            display_name = (
                item["resolved_path"].name if item["resolved_path"] else Path(item["path"]).name
            )
            vid_full = str(item["resolved_path"]) if item["resolved_path"] else item["path"]
            xml_full = str(item.get("xml_path") or self._get_expected_xml_path(item))

            haystack = f"{display_name} {item['folder']} {item['events_summary']} {vid_full} {xml_full}".lower()
            if query and query not in haystack:
                continue

            xml_txt = "Создан" if item.get("xml_exists") else "—"
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    display_name,
                    item["folder"],
                    format_duration(item["duration_sec"]),
                    item["events_summary"],
                    xml_txt,
                    item["status"],
                    vid_full,
                    xml_full,
                ),
            )
            self.row_to_item[iid] = item

    def _update_path_card(self, item: dict | None):
        if not item:
            self.var_info_disk.set("—")
            self.var_info_orig.set("—")
            self.var_info_xml.set("—")
            self.btn_reset_bind.configure(state="disabled")
            return

        disk_str = str(item["resolved_path"]) if item["resolved_path"] else "Не найден на диске"
        orig_str = item["path"]
        xml_p = item.get("xml_path") or self._get_expected_xml_path(item)
        xml_str = f"{xml_p}" + (" (существует)" if xml_p.exists() else " (ещё не создан)")

        self.var_info_disk.set(disk_str)
        self.var_info_orig.set(orig_str)
        self.var_info_xml.set(xml_str)
        self.btn_reset_bind.configure(state="normal" if item.get("is_rebound") else "disabled")

    def _on_row_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            self.selected_item = None
            self._update_path_card(None)
            return
        item = self.row_to_item.get(sel[0])
        if not item:
            return

        self.selected_item = item
        self._update_path_card(item)

        vid_path = item["resolved_path"]
        if not vid_path or not vid_path.exists():
            self.current_preview_path = None
            self.lbl_thumb.configure(image="", text="Видеофайл не найден", width=40, height=9)
            self.lbl_vid_meta.configure(text="")
            return

        self.current_preview_path = vid_path
        if vid_path in self.thumb_cache:
            photo = self.thumb_cache[vid_path]
            self._current_photo = photo
            self.lbl_thumb.configure(image=photo, text="", width=0, height=0)
        else:
            self.lbl_thumb.configure(image="", text="Загрузка превью...", width=40, height=9)

        def worker(target_path: Path):
            meta = parse_mp4_metadata(target_path) if target_path.suffix.lower() in {".mp4", ".mov"} else None
            ppm = None if target_path in self.thumb_cache else get_windows_thumbnail_ppm(target_path, 304, 171)
            self.root.after(0, lambda: self._apply_preview(target_path, ppm, meta))

        threading.Thread(target=worker, args=(vid_path,), daemon=True).start()

    def _apply_preview(self, target_path: Path, ppm: bytes | None, meta: dict | None):
        if ppm:
            try:
                photo = tk.PhotoImage(data=ppm)
                self.thumb_cache[target_path] = photo
            except Exception:
                pass

        if self.current_preview_path != target_path:
            return

        if target_path in self.thumb_cache:
            self._current_photo = self.thumb_cache[target_path]
            self.lbl_thumb.configure(image=self._current_photo, text="", width=0, height=0)
        else:
            self.lbl_thumb.configure(image="", text="Превью недоступно", width=40, height=9)

        if meta:
            fps_txt = f"{meta['fps']:.1f}" if meta["fps"] > 0 else "—"
            self.lbl_vid_meta.configure(
                text=f"{meta['width']}×{meta['height']}  |  {fps_txt} FPS  |  Аудиодорожек: {meta['audio_tracks']}"
            )
        else:
            self.lbl_vid_meta.configure(text=target_path.name)

    def open_event_settings(self):
        if not self.all_event_types:
            messagebox.showinfo("Нет событий", "Сначала загрузи базу с событиями Outplayed.")
            return
        disabled = set(self.config.get("disabled_event_types", []))
        colors = dict(self.config.get("event_colors", {}))
        per_gaps = dict(self.config.get("per_event_gaps", {}))
        gb = self._safe_float(self.var_gap_before.get(), 5.0)
        ga = self._safe_float(self.var_gap_after.get(), 5.0)

        def on_save(new_disabled: set[str], new_colors: dict[str, str], new_gaps: dict[str, dict]):
            self.config["disabled_event_types"] = sorted(new_disabled)
            self.config["event_colors"] = new_colors
            self.config["per_event_gaps"] = new_gaps
            self._save_config()
            self.var_status.set(
                f"Сохранено: скрыто типов {len(new_disabled)}, индив. гапов {len(new_gaps)}."
            )

        EventSettingsDialog(
            self.root,
            self.all_event_types,
            disabled,
            colors,
            per_gaps,
            gb,
            ga,
            self.theme,
            self.theme_name == "dark",
            on_save,
        )

    def run_auto_find(self):
        missing = [i for i in self.all_items if i["resolved_path"] is None]
        if not missing:
            messagebox.showinfo("Всё найдено", "Все видеофайлы уже привязаны!")
            return

        known = {i["resolved_path"].resolve() for i in self.all_items if i["resolved_path"]}
        extra_dirs = [Path(d) for d in self.config.get("extra_search_dirs", [])]

        self.btn_auto_find.configure(state="disabled")
        self.var_status.set(f"Ищу переименованные файлы ({len(missing)} шт.)...")

        def worker():
            found_map = auto_locate_renamed_videos(missing, known, extra_dirs)
            self.root.after(0, lambda: self._finish_auto_find(found_map))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_auto_find(self, found_map: dict[str, Path]):
        self.btn_auto_find.configure(state="normal")
        if not found_map:
            self.var_status.set("Автопоиск не нашёл новых совпадений.")
            messagebox.showinfo(
                "Поиск завершён",
                "Не удалось автоматически найти переименованные файлы.\n"
                "Добавь папку во вкладке «Настройки» или нажми «Привязать другой видеофайл…».",
            )
            return

        for item in self.all_items:
            if item["path"] in found_map:
                new_p = found_map[item["path"]]
                item["resolved_path"] = new_p
                item["status"] = "Перепривязан"
                item["is_rebound"] = True
                item["folder"] = new_p.parent.name
                self.config.setdefault("custom_paths", {})[item["path"]] = str(new_p)
                if self.var_auto_update_xml.get():
                    self._write_xml_for_item(item)

        self._save_config()
        self._filter_table()
        self.var_status.set(f"Найдено переименованных файлов: {len(found_map)}.")

    def add_search_folder(self):
        folder = filedialog.askdirectory(title="Выбери папку, где лежат переименованные видео")
        if not folder:
            return
        dirs = self.config.setdefault("extra_search_dirs", [])
        if folder not in dirs:
            dirs.append(folder)
            self._save_config()
        self.run_auto_find()

    def locate_manual_file(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Выбор", "Сначала выбери строку в таблице.")
            return
        item = self.row_to_item.get(selected[0])
        if not item:
            return

        init_dir = (
            str(item["resolved_path"].parent)
            if item["resolved_path"]
            else str(Path(item["path"]).parent if Path(item["path"]).parent.exists() else Path.home())
        )
        chosen = filedialog.askopenfilename(
            title=f"Привязать видеофайл к меткам: {Path(item['path']).name}",
            initialdir=init_dir,
            filetypes=[("Видеофайлы", "*.mp4 *.mov *.mkv *.avi"), ("Все файлы", "*.*")],
        )
        if not chosen:
            return

        p = Path(chosen).resolve()
        item["resolved_path"] = p
        item["folder"] = p.parent.name
        item["is_rebound"] = (os.path.normcase(str(p)) != os.path.normcase(item["path"]))
        item["status"] = "Перепривязан" if item["is_rebound"] else "Найден"

        if item["is_rebound"]:
            self.config.setdefault("custom_paths", {})[item["path"]] = str(p)
        else:
            self.config.get("custom_paths", {}).pop(item["path"], None)

        if self.var_auto_update_xml.get():
            self._write_xml_for_item(item)
        self._save_config()
        self._filter_table()
        self._update_path_card(item)
        self.var_status.set(f"Файл привязан: {p.name}")

    def reset_manual_binding(self):
        if not self.selected_item:
            return
        item = self.selected_item
        self.config.get("custom_paths", {}).pop(item["path"], None)
        orig = Path(item["path"])
        item["is_rebound"] = False
        if orig.exists():
            item["resolved_path"] = orig
            item["status"] = "Найден"
            item["folder"] = orig.parent.name
        else:
            item["resolved_path"] = None
            item["status"] = "Не найден"
            item["folder"] = orig.parent.name
        item["xml_path"] = self._get_expected_xml_path(item)
        item["xml_exists"] = item["xml_path"].exists()

        self._save_config()
        self._filter_table()
        self._update_path_card(item)
        self.var_status.set("Ручная привязка сброшена к исходному пути Outplayed.")

    def load_gaps_from_outplayed(self):
        self.var_status.set("Сканирую IndexedDB, LocalStorage и логи Outplayed...")
        self.root.update_idletasks()
        res = detect_outplayed_gap_settings(self.all_event_types)
        if res:
            b, a = res["global"]
            self.var_gap_before.set(str(b))
            self.var_gap_after.set(str(a))
            if res["per_event"]:
                cur = self.config.setdefault("per_event_gaps", {})
                cur.update(res["per_event"])
            self._save_config()
            cnt = len(res["per_event"])
            msg = f"Найдены настройки ({res['source']}):\nОбщие: До {b} с, После {a} с"
            if cnt:
                preview = ", ".join(f"{k}: {v['before']}/{v['after']}" for k, v in list(res["per_event"].items())[:6])
                msg += f"\nЗагружено индивидуальных событий ({cnt}): {preview}"
            self.var_status.set(f"Гапы загружены из {res['source']}.")
            messagebox.showinfo("Outplayed", msg)
        else:
            self.var_status.set("Тайминги Outplayed не найдены в IndexedDB/LocalStorage.")
            messagebox.showinfo(
                "Outplayed",
                "В файлах Overwolf (IndexedDB, LocalStorage, логи) не удалось обнаружить сохранённые тайминги До/После.\n\n"
                "Возможные причины: настройки в Outplayed хранятся в облачном профиле Overwolf или под нестандартными ключами.\n"
                "Нажми «Фильтр, цвета и гапы событий…» — задай нужные секунды один раз, и они сохранятся навсегда.",
            )

    def browse_premiere(self):
        chosen = filedialog.askopenfilename(
            title="Выбери Adobe Premiere Pro.exe",
            filetypes=[("Executable", "*.exe")],
        )
        if chosen:
            self.var_premiere_path.set(chosen)
            self._save_config()

    def export_selected(self, open_in_premiere: bool = False):
        selected_ids = self.tree.selection()
        if not selected_ids:
            messagebox.showwarning("Ничего не выбрано", "Выбери хотя бы одно видео в таблице.")
            return

        self._save_config()
        generated: list[Path] = []
        total_markers = 0

        for iid in selected_ids:
            item = self.row_to_item.get(iid)
            if not item:
                continue
            out_xml, count = self._write_xml_for_item(item)
            generated.append(out_xml)
            total_markers += count

        if not generated:
            return

        self.last_generated_xml = generated[-1]
        self._filter_table()
        if self.selected_item:
            self._update_path_card(self.selected_item)
        self.var_status.set(f"Создано/обновлено XML: {len(generated)} (меток: {total_markers}) -> {self.last_generated_xml}")

        if open_in_premiere:
            prem_exe = find_premiere_exe(self.var_premiere_path.get().strip())
            if not prem_exe or not prem_exe.exists():
                messagebox.showwarning(
                    "Premiere Pro не найден",
                    f"XML создан, но не найден Premiere Pro:\n{self.var_premiere_path.get()}\n\nФайл XML:\n{generated[0]}",
                )
                return
            self._launch_premiere_with_xmls(prem_exe, generated)
        else:
            messagebox.showinfo(
                "Готово",
                f"Создано/обновлено XML-файлов: {len(generated)}\n"
                f"Всего меток: {total_markers}\n\n"
                f"Последний файл:\n{self.last_generated_xml}",
            )

    def _launch_premiere_with_xmls(self, prem_exe: Path, xml_files: list[Path]):
        pids = get_premiere_pids()
        hwnd = find_premiere_main_hwnd(pids)

        if pids and hwnd:
            for xml_file in xml_files:
                subprocess.Popen([str(prem_exe), str(xml_file)])
            focus_window(hwnd)
            self.var_status.set("XML сразу отправлен в открытый Premiere Pro.")
            return

        self.var_status.set("Запускаю Premiere Pro (~15–20 сек), после появления окна отправлю XML...")

        def worker():
            try:
                if not pids:
                    subprocess.Popen([str(prem_exe)])

                deadline = time.time() + 45.0
                ready_hwnd = None
                while time.time() < deadline:
                    cur_pids = get_premiere_pids()
                    ready_hwnd = find_premiere_main_hwnd(cur_pids)
                    if ready_hwnd:
                        time.sleep(1.0)
                        break
                    time.sleep(0.4)

                for xml_file in xml_files:
                    subprocess.Popen([str(prem_exe), str(xml_file)])
                if ready_hwnd:
                    focus_window(ready_hwnd)

                self.root.after(0, lambda: self.var_status.set("XML передан в Premiere Pro."))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Ошибка запуска Premiere Pro", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def open_selected_video_folder(self):
        if self.selected_item and self.selected_item["resolved_path"] and self.selected_item["resolved_path"].exists():
            subprocess.Popen(["explorer", "/select,", str(self.selected_item["resolved_path"])])
        else:
            messagebox.showinfo("Нет файла", "Выбери видео, найденное на диске.")

    def open_last_xml_folder(self):
        target = None
        if self.selected_item:
            cand = self.selected_item.get("xml_path") or self._get_expected_xml_path(self.selected_item)
            if cand.exists():
                target = cand
        if not target and self.last_generated_xml and self.last_generated_xml.exists():
            target = self.last_generated_xml

        if target:
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            messagebox.showinfo("Нет файла", "Сначала создай XML для выбранного видео.")

    def open_config_folder(self):
        if CONFIG_PATH.exists():
            subprocess.Popen(["explorer", "/select,", str(CONFIG_PATH)])
        else:
            subprocess.Popen(["explorer", str(CONFIG_PATH.parent)])

    def open_overwolf_folder(self):
        p = Path(os.environ.get("LOCALAPPDATA", "")) / "Overwolf"
        if p.exists():
            subprocess.Popen(["explorer", str(p)])
        else:
            messagebox.showinfo("Не найдено", f"Папка не найдена:\n{p}")


def main():
    root = tk.Tk()
    OutplayedPremiereApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()