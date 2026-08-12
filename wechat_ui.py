#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信 UI 驱动共享模块：前台化、几何、滚动、截帧、位移匹配。"""
import os, subprocess, time
import numpy as np
from PIL import Image
import Quartz
import AppKit
from geometry import find_main_window, capture_window, detect_regions

# 临时/调试文件统一放 _tmp/，不污染项目目录。
# 锚定到本模块所在目录，避免从 .app 启动时(cwd=/)写到根目录。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "_tmp")
os.makedirs(TMP_DIR, exist_ok=True)
_TMP = os.path.join(TMP_DIR, "frame_tmp.png")


# ==== 停止抓取：快捷键 ⌃⌥⌘+.(全局读硬件按键，无需焦点) 或 App 里的停止按钮 ====
class StopRequested(Exception):
    """用户请求停止抓取。"""
    pass


_soft_stop = False


def request_stop():
    """App 停止按钮调用：置软停止标记，抓取循环下次轮询时中断。"""
    global _soft_stop
    _soft_stop = True


def clear_stop():
    global _soft_stop
    _soft_stop = False


_STOP_STATE = Quartz.kCGEventSourceStateCombinedSessionState
_STOP_KEY = 47  # kVK_ANSI_Period '.'
_STOP_MODS = (Quartz.kCGEventFlagMaskControl |
              Quartz.kCGEventFlagMaskAlternate |
              Quartz.kCGEventFlagMaskCommand)


def stop_requested():
    """强制停止快捷键(⌃⌥⌘+.)当前是否被按下。"""
    flags = Quartz.CGEventSourceFlagsState(_STOP_STATE)
    if (flags & _STOP_MODS) != _STOP_MODS:
        return False
    return bool(Quartz.CGEventSourceKeyState(_STOP_STATE, _STOP_KEY))


def check_stop():
    """在抓取循环里调用；若停止按钮或快捷键触发则抛出 StopRequested 中断整个流程。"""
    if _soft_stop or stop_requested():
        raise StopRequested("已手动停止")


def wechat_pid():
    return int(subprocess.check_output(["pgrep", "-x", "WeChat"]).split()[0])


def frontmost_name():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.localizedName() if app else "?"


def ensure_front(timeout=3.0):
    """用 open -a 把微信顶到最前，并验证。"""
    subprocess.run(["open", "-a", "WeChat"])
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = frontmost_name()
        if n in ("WeChat", "微信"):
            time.sleep(0.25)
            return True
        time.sleep(0.15)
    return frontmost_name() in ("WeChat", "微信")


def move_mouse(gx, gy):
    ev = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved,
                                        Quartz.CGPointMake(gx, gy), 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    time.sleep(0.04)


def scroll_lines(gx, gy, lines, steps=6, settle=0.32):
    move_mouse(gx, gy)
    for _ in range(steps):
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines)
        Quartz.CGEventSetLocation(ev, Quartz.CGPointMake(gx, gy))
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.02)
    time.sleep(settle)


class WeChatView:
    def __init__(self):
        self.pid = wechat_pid()
        self.refresh_geometry()

    def refresh_geometry(self):
        self.win = find_main_window()
        capture_window(self.win["id"], _TMP)
        self.reg = detect_regions(_TMP, self.win)
        s = self.reg["scale"]
        self.scale = s
        self.pane_cx = self.win["x"] + (self.reg["pane_x_px"] + self.reg["W"]) / 2 / s
        self.pane_cy = self.win["y"] + (self.reg["top_px"] + self.reg["bottom_px"]) / 2 / s

    def grab(self):
        capture_window(self.win["id"], _TMP)
        im = Image.open(_TMP)
        crop = im.crop((self.reg["pane_x_px"], self.reg["top_px"], self.reg["W"], self.reg["bottom_px"]))
        rgb = crop.convert("RGB")
        gray = np.asarray(crop.convert("L"), dtype=np.float32)
        return rgb, gray

    def scroll(self, lines, steps=6, settle=0.32):
        scroll_lines(self.pane_cx, self.pane_cy, lines, steps, settle)


def match_shift(prev_gray, cur_gray, max_shift=None):
    """内容从 prev 到 cur 向下移动了 d 像素(d>=0)；用左中部竖条 + 边缘增强做互相关。"""
    # 两帧尺寸可能差几像素(窗口切前台动画/截图取整)，先裁到公共尺寸，避免 np.dot 长度不一致崩溃
    if prev_gray.shape != cur_gray.shape:
        h = min(prev_gray.shape[0], cur_gray.shape[0])
        w = min(prev_gray.shape[1], cur_gray.shape[1])
        prev_gray = prev_gray[:h, :w]
        cur_gray = cur_gray[:h, :w]
    H, W = prev_gray.shape
    c0, c1 = int(W * 0.05), int(W * 0.48)
    A = prev_gray[:, c0:c1]
    B = cur_gray[:, c0:c1]
    # 边缘增强(竖直方向梯度)，让文字行更突出，抵抗平滑背景
    A = np.abs(np.diff(A, axis=0))
    B = np.abs(np.diff(B, axis=0))
    Hh = A.shape[0]
    if max_shift is None:
        max_shift = int(Hh * 0.9)
    best_d, best_score = 0, -1e9
    for d in range(0, max_shift):
        h = Hh - d
        if h < Hh * 0.12:
            break
        a = A[0:h].ravel(); b = B[d:d + h].ravel()
        a = a - a.mean(); b = b - b.mean()
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom < 1e-6:
            continue
        score = float(np.dot(a, b) / denom)
        if score > best_score:
            best_score, best_d = score, d
    return best_d, best_score
