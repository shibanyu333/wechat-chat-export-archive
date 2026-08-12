#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 macOS Vision 引擎对图片做中文OCR，返回文字行 + 像素坐标框。"""
import re, sys
import Vision
import Quartz
from Foundation import NSURL


def ocr_image(path):
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    W = Quartz.CGImageGetWidth(cg)
    H = Quartz.CGImageGetHeight(cg)

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok, err = handler.performRequests_error_([req], None)
    lines = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        text = cand[0].string()
        conf = cand[0].confidence()
        b = obs.boundingBox()  # 归一化, 原点左下
        x = b.origin.x * W
        w = b.size.width * W
        h = b.size.height * H
        y = (1 - b.origin.y - b.size.height) * H  # 翻成左上原点
        lines.append({"text": text, "conf": float(conf),
                      "x": x, "y": y, "w": w, "h": h})
    lines.sort(key=lambda l: l["y"])
    return lines, W, H


def ocr_image_sliced(path, slice_h=1800, overlap=240):
    """把高图切成横条分别OCR(保持原分辨率), 合并去重。返回 (lines, W, H)。"""
    from PIL import Image
    import tempfile, os
    im = Image.open(path).convert("RGB")
    W, H = im.size
    if H <= slice_h:
        return ocr_image(path)
    lines = []
    y = 0
    tmpd = tempfile.mkdtemp()
    while y < H:
        y1 = min(H, y + slice_h)
        crop = im.crop((0, y, W, y1))
        fp = os.path.join(tmpd, f"s_{y}.png")
        crop.save(fp)
        part, _, _ = ocr_image(fp)
        for l in part:
            l = dict(l); l["y"] += y
            lines.append(l)
        os.remove(fp)
        if y1 >= H:
            break
        y += slice_h - overlap
    # 去重(重叠区同一行出现两次)：同一位置的两次识别文本可能略有差异
    # (多个空格/图标符号)，故按"归一化后互相包含 + x范围重叠"判断物理重复
    def _n(s):
        return re.sub(r'[\s•·]+', '', s)
    lines.sort(key=lambda l: (l["y"], l["x"]))
    dedup = []
    for l in lines:
        dup = False
        for k in reversed(dedup):
            if l["y"] - k["y"] > 16:
                break
            a, b = _n(k["text"]), _n(l["text"])
            if a and b and (a in b or b in a):
                lo = max(k["x"], l["x"])
                hi = min(k["x"] + k["w"], l["x"] + l["w"])
                if hi - lo > 0.6 * min(k["w"], l["w"]):
                    dup = True
                    break
        if not dup:
            dedup.append(l)
    try:
        os.rmdir(tmpd)
    except OSError:
        pass
    return dedup, W, H


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "stitch_inspect.png"
    lines, W, H = ocr_image(path)
    print(f"图片 {W}x{H}, 识别 {len(lines)} 行:\n")
    for l in lines:
        side = "右(我)" if (l["x"] + l["w"] / 2) > W * 0.5 else "左"
        print(f"  y={l['y']:5.0f} x={l['x']:5.0f} w={l['w']:4.0f} [{side}] c={l['conf']:.2f}  {l['text']}")
