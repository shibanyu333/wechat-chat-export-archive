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


class FocusLost(Exception):
    """抓取途中微信被别的窗口抢走了焦点。"""
    pass


def ensure_front_or_raise():
    """滚动前确认微信仍在前台。

    滚轮事件是投递给"屏幕该坐标处最上层窗口"的，微信一旦失去前台(用户切了
    浏览器、通知弹窗抢焦点)，滚动就全打到别的窗口上，画面纹丝不动——而抓取
    循环会把"不动"读成"已到顶/已到底"，于是悄悄导出半截记录。实测踩过：
    抓到一半焦点跑到 Chrome，最后只导出了 1 屏 5 条消息还以为成功了。
    所以每次滚动前都要查，掉了就抢回来，抢不回来直接报错，绝不静默截断。"""
    if frontmost_name() in ("WeChat", "微信"):
        return
    if ensure_front(timeout=4.0):
        time.sleep(0.35)
        return
    raise FocusLost(
        f"微信被 {frontmost_name()} 抢走了前台，且无法自动切回。"
        "抓取期间请不要点击其他窗口；请重新运行。")


def scroll_lines(gx, gy, lines, steps=3, settle=0.32):
    ensure_front_or_raise()
    move_mouse(gx, gy)
    for _ in range(steps):
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines)
        Quartz.CGEventSetLocation(ev, Quartz.CGPointMake(gx, gy))
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.02)
    time.sleep(settle)


def capture_window_rgb(win_id):
    """直接向 CoreGraphics 要窗口位图，返回 (H, W, 3) 的 RGB 数组；失败返回 None。

    原来每抓一帧都要 fork 一个 screencapture 进程、把 2480x1660 编码成 PNG 落盘、
    再让 PIL 解码回来——实测 112ms/帧。抓一段长会话要滚几十上百帧，这是整个导出
    最大的一笔开销。直接问 CoreGraphics 要位图是 18ms(实测 6.2x)，而且像素与
    screencapture 逐通道完全一致(BGRA 取 [2,1,0] 得 RGB，平均通道差 0.00)。
    kCGWindowImageBoundsIgnoreFraming 对应 screencapture 的 -o(去窗口阴影)，
    kCGWindowImageBestResolution 保证拿到 Retina 2x 原分辨率。"""
    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, win_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageBestResolution)
    if img is None:
        return None
    h = Quartz.CGImageGetHeight(img)
    w = Quartz.CGImageGetWidth(img)
    bpr = Quartz.CGImageGetBytesPerRow(img)
    if not h or not w or bpr < w * 4:
        return None
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    buf = np.frombuffer(data, dtype=np.uint8)
    if buf.size < h * bpr:
        return None
    return buf[:h * bpr].reshape(h, bpr // 4, 4)[:, :w, 2::-1]   # BGRA → RGB


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

    def _full_rgb(self):
        """整窗 RGB 数组。CoreGraphics 偶发返回空(窗口刚切换空间等)时退回截图命令。"""
        a = capture_window_rgb(self.win["id"])
        if a is None:
            capture_window(self.win["id"], _TMP)
            a = np.asarray(Image.open(_TMP).convert("RGB"))
        return a

    def grab(self):
        r = self.reg
        a = self._full_rgb()
        crop = np.ascontiguousarray(
            a[r["top_px"]:r["bottom_px"], r["pane_x_px"]:r["W"]])
        rgb = Image.fromarray(crop)
        # 灰度直接用 numpy 算(ITU-R 601 权重，与 PIL convert("L") 等价)，
        # 省掉一次 PIL 转换
        gray = (crop[:, :, 0] * 0.299 + crop[:, :, 1] * 0.587
                + crop[:, :, 2] * 0.114).astype(np.float32)
        return rgb, gray

    def scroll(self, lines, steps=3, settle=0.32):
        scroll_lines(self.pane_cx, self.pane_cy, lines, steps, settle)

    def _full_gray(self):
        a = self._full_rgb()
        return (a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587
                + a[:, :, 2] * 0.114).astype(np.float32)

    def _moved_rows(self, a, b, pad=6):
        """两帧之间发生变化的行范围(只看聊天列)。"""
        h = min(a.shape[0], b.shape[0])
        d = np.abs(a[:h] - b[:h])[:, self.reg["pane_x_px"]:]
        row = d.mean(axis=1)
        if row.max() < 2.0:
            return None
        idx = np.where(row > max(2.0, row.max() * 0.12))[0]
        if len(idx) < 40:
            return None
        return int(idx.min()), int(idx.max()) + pad

    def calibrate_bottom(self, progress=None):
        """把输入框排除在消息区之外。

        微信 4.x 深色模式下输入框和消息区背景同色、没有分界线，单帧的逐行方差
        分不开两者：输入框里有草稿时，草稿那几行会被当成"最后一条消息"抓进长图
        (实测把草稿 'ek y' 导成了一条消息)，而且输入框是静止的，混进匹配区还会
        让帧间相关偏向"没动"。滚一下就没这个歧义了——只有消息区会动。

        做法保守：只把【下边界之下、且没跟着滚动的内容】切掉，绝不因为当前这屏
        底部恰好是空白就把下边界往上收，否则后面滚出来的内容会被裁掉。"""
        a = self._full_gray()
        moved = None
        for delta in (-2, 2):            # 先试向下，滚不动(已在底部)再试向上
            self.scroll(delta, steps=2, settle=0.5)
            b = self._full_gray()
            moved = self._moved_rows(a, b)
            self.scroll(-delta, steps=2, settle=0.5)
            if moved:
                break
            a = self._full_gray()
        if not moved:
            return False
        m_bottom = moved[1]
        if m_bottom >= self.reg["bottom_px"]:
            return False                 # 静态检测本来就没多框，不动它
        # 从"最后一行动过的内容"往下找第一条静止内容(草稿/占位符)，切在它上面
        full = self._full_gray()
        pane = full[:, self.reg["pane_x_px"]:]
        rs = pane.std(axis=1)
        content = rs > rs.max() * 0.12
        new_bottom = self.reg["bottom_px"]
        for y in range(m_bottom, min(self.reg["bottom_px"], full.shape[0])):
            if content[y]:
                new_bottom = max(m_bottom, y - 4)
                break
        if new_bottom < self.reg["bottom_px"]:
            if progress:
                progress(f"  · 消息区下边界 {self.reg['bottom_px']} → {new_bottom}"
                         f"(排除输入框/草稿)")
            self.reg["bottom_px"] = new_bottom
            self.pane_cy = self.win["y"] + (self.reg["top_px"] + new_bottom) / 2 / self.scale
            return True
        return False


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
    # 列方向抽稀 4 倍：d 是行位移，抽列不影响它的精度，运算量直接降到 1/4
    A = A[:, ::4]
    B = B[:, ::4]
    Hh = A.shape[0]
    if max_shift is None:
        max_shift = int(Hh * 0.9)

    def score_at(d):
        h = Hh - d
        if h < Hh * 0.12:
            return None
        a = A[0:h].ravel(); b = B[d:d + h].ravel()
        a = a - a.mean(); b = b - b.mean()
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom < 1e-6:
            return None
        return float(np.dot(a, b) / denom)

    # 粗搜(步长4)定位波峰，再在峰附近逐像素细搜——文字行的相关峰有好几像素宽，
    # 步长 4 不会跳过它。两段加起来约 1/4 的计算量，d 仍然是像素级精确的。
    best_d, best_score = 0, -1e9
    for d in range(0, max_shift, 4):
        sc = score_at(d)
        if sc is not None and sc > best_score:
            best_score, best_d = sc, d
    lo = max(0, best_d - 5)
    for d in range(lo, min(max_shift, best_d + 6)):
        sc = score_at(d)
        if sc is not None and sc > best_score:
            best_score, best_d = sc, d
    return best_d, best_score
