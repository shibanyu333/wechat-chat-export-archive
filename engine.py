#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取+解析引擎：CLI 和桌面 App 共用。"""
import os, sys, time, subprocess
from PIL import Image
from wechat_ui import (WeChatView, ensure_front, frontmost_name, match_shift,
                       check_stop, StopRequested, TMP_DIR)
from geometry import capture_window
from ocr import ocr_image
from parse3 import parse_image


def preflight():
    """返回 (ok, message)。"""
    try:
        subprocess.check_output(["pgrep", "-x", "WeChat"])
    except subprocess.CalledProcessError:
        return False, "没检测到微信在运行，请先打开微信并登录，再打开要导出的会话。"
    import ApplicationServices as AX
    if not AX.AXIsProcessTrusted():
        AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
        return False, "缺少【辅助功能】权限：系统设置→隐私与安全性→辅助功能，勾选运行本程序的 App 后重试。"
    test = os.path.join(TMP_DIR, "_pf.png")
    r = subprocess.run(["screencapture", "-x", "-t", "png", test],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        return False, "缺少【屏幕录制】权限：系统设置→隐私与安全性→屏幕录制，勾选运行本程序的 App 后重试。"
    if os.path.exists(test):
        os.remove(test)
    return True, "ok"


def detect_title(v):
    from wechat_ui import _TMP
    capture_window(v.win["id"], _TMP)
    im = Image.open(_TMP)
    s = v.scale
    x0 = v.reg["pane_x_px"] + int(10 * s)
    x1 = v.reg["pane_x_px"] + int((v.reg["W"] - v.reg["pane_x_px"]) * 0.62)
    y0 = int(8 * s); y1 = v.reg["top_px"] - int(6 * s)
    if y1 <= y0 + 10:
        return None
    crop = im.crop((x0, y0, x1, y1))
    tmp = os.path.join(TMP_DIR, "_title.png"); crop.save(tmp)
    lines, _, _ = ocr_image(tmp)
    if os.path.exists(tmp):
        os.remove(tmp)
    lines = [l for l in lines if l["text"].strip()]
    if not lines:
        return None
    lines.sort(key=lambda l: -l["w"])
    return lines[0]["text"].strip()


def stitch_down(v, max_steps=500, progress=print, on_frame=None):
    """从当前位置向下滚动拼整段(当前顶部→最新)。on_frame(v) 每帧后执行(如语音转文字)，
    返回是否需要重抓。向下滚时内容向上移动，新内容出现在底部，逐段贴到画布下方。

    微信从「搜索聊天记录」跳到历史位置后消息是分块加载的：滚到已加载块的假底部会
    先停滞，随后加载完成、视图被自动往上重新锚定(用户看到的"自动上翻")。因此按
    匹配分数区分三种帧，绝不把假底部当成真到底：
    - 高分且位移>=8 → 正常前进，拼接；
    - 高分且无位移 → 停滞，多等几轮再认定真到底(假底部通常一两秒内就会跳块)；
    - 低分 → 视图被微信挪走了，不拼接，只向下滚动追赶，直到重新对上拼接位置。"""
    SCORE_OK = 0.5
    rgb, gray = v.grab()
    canvas = rgb
    H = gray.shape[0]
    # 起始帧也要处理语音(顶部这一屏的语音气泡)
    if on_frame is not None and on_frame(v):
        rgb, gray = v.grab(); canvas = rgb
    prev_gray = gray
    still = 0     # 连续停滞轮数(高分、无位移)
    seek = 0      # 视图跳到上方后向下追赶的轮数(反向匹配成功)
    lost = 0      # 与上次拼接位置完全对不上的轮数(双向都低分)
    rebases = 0   # 放弃对位、直接续接的次数
    for i in range(max_steps):
        check_stop()
        v.scroll(-3)                       # 向下(朝最新消息)
        rgb, gray = v.grab()
        if on_frame is not None and on_frame(v):
            rgb, gray = v.grab()
        # 内容向上移动了 d 像素：match_shift(cur, prev) 得到该上移量
        d, score = match_shift(gray, prev_gray)
        if score >= SCORE_OK and d >= 8:
            still = seek = lost = 0
            d = min(d, H)
            new_bottom = rgb.crop((0, rgb.height - d, rgb.width, rgb.height))
            merged = Image.new("RGB", (canvas.width, canvas.height + d))
            merged.paste(canvas, (0, 0)); merged.paste(new_bottom, (0, canvas.height))
            canvas = merged
            prev_gray = gray
            if i % 8 == 0:
                progress(f"  ...已抓取 {canvas.height}px")
            continue
        if score >= SCORE_OK:
            # 高分停滞：真到底，或分块加载中的假底部(随后跳块会落到下面的分支)
            seek = lost = 0
            still += 1
            if still >= 6:
                break
            time.sleep(0.7)
            continue
        still = 0
        dr, sr = match_shift(prev_gray, gray)
        if sr >= SCORE_OK and dr >= 8:
            # 视图在拼接位置上方且内容有重叠(典型"自动上翻")：向下追赶即可重新对上
            seek += 1; lost = 0
            if seek == 1:
                progress("  · 微信视图自动上翻(历史分块加载)，正在向下重新对位...")
            if seek < 40:
                continue
        else:
            # 双向都对不上：跳变后微信重排了版面或落点跳过了原屏
            lost += 1
            if lost == 1 and seek == 0:
                progress("  · 微信视图自动跳动(历史分块加载)，尝试重新对位...")
            if lost < 10:
                continue
        # 追不上/对不上：放弃精确对位，从当前位置直接续接。
        # 画一条背景色分隔带作接缝标记；宁可少量重复或缺失，也不整段丢弃
        rebases += 1
        if rebases > 20:
            progress("  ! 视图跳变过于频繁，提前结束；结果可能不完整")
            break
        progress("  · 无法精确对位，从当前位置续接(接缝处可能有少量重复或缺失)")
        bg = rgb.crop((0, 0, rgb.width, 4)).resize((1, 1), Image.LANCZOS).getpixel((0, 0))
        merged = Image.new("RGB", (canvas.width, canvas.height + 30 + rgb.height), bg)
        merged.paste(canvas, (0, 0)); merged.paste(rgb, (0, canvas.height + 30))
        canvas = merged
        prev_gray = gray
        still = seek = lost = 0
        time.sleep(1.2)   # 续接后让微信把分块加载/重排安顿下来，避免连环跳变
    return canvas


def capture_and_parse(max_steps=500, do_voice=False, progress=print, stitched_out=None):
    ok, msg = preflight()
    if not ok:
        raise RuntimeError(msg)
    if not ensure_front():
        raise RuntimeError(f"无法把微信切到前台(当前:{frontmost_name()})，请手动点开微信主窗口后重试")
    v = WeChatView(); v.refresh_geometry()
    title = detect_title(v) or "微信会话"
    progress(f"· 会话: {title}")

    # 语音转文字用「两趟」：先从当前位置滚到底把语音全部转完(转写永久留在微信里)，
    # 回到起点上方，再干净地向下拼图。绝不能边滚边转——转写文字在气泡下方、常已滚出当前帧。
    n_voice = 0
    try:
        if do_voice:
            from voice import transcribe_down
            progress("· 语音自动转文字：从当前位置向下逐屏转写(较慢；急停 ⌃⌥⌘+.)...")
            steps, n_voice = transcribe_down(v, progress=progress, max_steps=max_steps)
            progress(f"· 语音转写完成，共 {n_voice} 条；回到起点准备抓取...")
            # 用与下行相同的步长(2)回滚，再加余量补偿转写后长出来的文字高度；
            # 多回滚到起点上方，多抓的顶部可在预览里裁掉
            for _ in range(steps + 8):
                check_stop(); v.scroll(2)
            # 大幅回滚后微信的历史分块还在加载，立即下滚会触发重新锚定跳变；多等一会
            time.sleep(1.8); v.refresh_geometry()

        progress("· 从当前顶部位置向下抓取到最新(请勿操作鼠标键盘；急停 ⌃⌥⌘+.)...")
        canvas = stitch_down(v, max_steps, progress=progress)
    except StopRequested as e:
        raise RuntimeError(str(e))
    stitched_path = stitched_out or os.path.join(TMP_DIR, "stitched_full.png")
    canvas.save(stitched_path)
    progress("· OCR + 解析中...")
    im, msgs = parse_image(stitched_path)
    n_msg = sum(1 for m in msgs if m["type"] != "time")
    progress(f"· 解析出 {n_msg} 条消息" + (f"，语音转写 {n_voice} 条" if do_voice else ""))
    return {"im": im, "msgs": msgs, "title": title, "scale": v.scale,
            "stitched_path": stitched_path}
