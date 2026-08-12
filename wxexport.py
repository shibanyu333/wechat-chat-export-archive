#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信聊天记录导出 命令行版(全量自动导出，不带图形选择界面)。
用法: 先在微信打开会话，再运行:
    ./.venv/bin/python wxexport.py --format docx,md
图形界面请用 微信导出.app 或 启动App(备用).command
"""
import argparse, os, time
from engine import capture_and_parse
from render_docx import render
from render_md import render_md

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "导出结果")


def main():
    ap = argparse.ArgumentParser(description="微信聊天记录导出 CLI")
    ap.add_argument("--name", help="文件名/标题(默认自动识别会话名)")
    ap.add_argument("--max", type=int, default=500, help="最大滚动屏数")
    ap.add_argument("--format", default="docx", help="docx / md / docx,md")
    ap.add_argument("--voice", action="store_true", help="语音自动转文字(实验,较慢)")
    ap.add_argument("--out", help="输出路径(不含扩展名，默认存到 导出结果/)")
    ap.add_argument("--keep-image", action="store_true", help="保留拼接长图")
    args = ap.parse_args()

    print("== 微信聊天记录导出(命令行) ==")
    print("即将自动滚动微信，请勿操作鼠标键盘。3秒后开始...")
    for i in (3, 2, 1):
        print(f"  {i}...", end="", flush=True); time.sleep(1)
    print(" 开始!\n")

    try:
        res = capture_and_parse(args.max, args.voice, progress=print)
    except RuntimeError as e:
        print("!!", e); return

    im, msgs, scale = res["im"], res["msgs"], res["scale"]
    title = args.name or res["title"]
    n_msg = sum(1 for m in msgs if m["type"] != "time")
    safe = "".join(c for c in title if c not in '/\\:*?"<>|').strip()[:40] or "微信会话"
    if args.out:
        base = os.path.abspath(args.out)
    else:
        os.makedirs(OUT_DIR, exist_ok=True)
        base = os.path.join(OUT_DIR, f"{safe}_聊天记录")
    fmts = [f.strip().lower() for f in args.format.split(",") if f.strip()]
    date = time.strftime("%Y-%m-%d %H:%M")

    outputs = []
    if "docx" in fmts:
        o, _ = render(im, msgs, title, base + ".docx", scale=scale); outputs.append(o)
    if "md" in fmts or "markdown" in fmts:
        o, _ = render_md(im, msgs, title, base + ".md", scale=scale,
                         export_date=date); outputs.append(o)

    print(f"\n✓ 导出完成 ({n_msg} 条消息):")
    for o in outputs:
        print("  ·", o)

    sp = res.get("stitched_path")
    if args.keep_image and sp:
        print("  · 拼接长图:", sp)
    elif sp and os.path.exists(sp):
        os.remove(sp)
    if outputs:
        os.system(f'open -R "{outputs[0]}"')


if __name__ == "__main__":
    main()
