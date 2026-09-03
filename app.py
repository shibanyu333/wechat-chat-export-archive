#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信聊天记录导出 桌面App(pywebview)。抓取→预览→点选起止→导出 Word/Markdown。"""
import os, io, json, time, base64
HERE = os.path.dirname(os.path.abspath(__file__))
# 双击 .app 启动时工作目录是 /(根目录)，相对路径会写到根目录而失败；
# 必须在导入 engine/wechat_ui 之前切到项目目录。
os.chdir(HERE)
import webview
from engine import capture_and_parse, preflight
from wechat_ui import request_stop, clear_stop
from render_docx import render
from render_md import render_md
from wxfiles import resolve_and_collect

OUT_DIR = os.path.join(HERE, "导出结果")
window = None


class Api:
    def __init__(self):
        self.res = None

    def check_env(self):
        ok, msg = preflight()
        return {"ok": ok, "msg": msg}

    def stop(self):
        """停止按钮：置软停止标记，抓取循环会尽快中断。"""
        request_stop()
        return True

    def capture(self, max_steps, do_voice):
        def prog(m):
            try:
                window.evaluate_js("window.addLog(%s)" % json.dumps(str(m)))
            except Exception:
                pass
        clear_stop()
        try:
            window.minimize()
        except Exception:
            pass
        try:
            res = capture_and_parse(int(max_steps), bool(do_voice), progress=prog)
        except Exception as e:
            self._to_front()
            return {"ok": False, "msg": str(e)}
        self._to_front()
        self.res = res
        im = res["im"]
        pw = 480
        ratio = pw / im.width
        prev = im.resize((pw, max(1, int(im.height * ratio))))
        buf = io.BytesIO(); prev.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        msgs = []
        for i, m in enumerate(res["msgs"]):
            msgs.append({
                "i": i, "type": m["type"], "sender": m.get("sender", ""),
                "name": m.get("name", ""),
                "y0": int(m["y0"] * ratio), "y1": int(m["y1"] * ratio),
                "text": (m.get("text") or "")[:60],
            })
        return {"ok": True, "title": res["title"], "img": b64,
                "pw": pw, "ph": prev.height, "messages": msgs}

    def _to_front(self):
        """抓取结束后把 App 从最小化恢复并带回前台(此前微信在前台)。"""
        try:
            window.restore()
        except Exception:
            pass
        try:
            window.on_top = True
            time.sleep(0.4)
            window.on_top = False
        except Exception:
            pass

    def export(self, start_i, end_i, formats, name):
        if not self.res:
            return {"ok": False, "msg": "还没有抓取会话"}
        msgs = self.res["msgs"]
        if start_i is None or end_i is None:
            sel = msgs
        else:
            a, b = sorted([int(start_i), int(end_i)])
            sel = msgs[a:b + 1]
        # 去掉选区首尾多余的时间分隔
        while sel and sel[0]["type"] == "time":
            sel = sel[1:]
        while sel and sel[-1]["type"] == "time":
            sel = sel[:-1]
        if not sel:
            return {"ok": False, "msg": "选区为空"}
        title = (name or "").strip() or self.res["title"]
        safe = "".join(c for c in title if c not in '/\\:*?"<>|').strip()[:40] or "微信会话"
        os.makedirs(OUT_DIR, exist_ok=True)
        base = os.path.join(OUT_DIR, safe + "_聊天记录")
        im = self.res["im"]; scale = self.res["scale"]
        date = time.strftime("%Y-%m-%d %H:%M")
        try:
            n_hit, n_file = resolve_and_collect(sel, base + "_文件")
        except Exception:
            n_hit = n_file = 0
        outputs = []
        fl = [f.lower() for f in (formats or [])]
        try:
            if "docx" in fl:
                o, _ = render(im, sel, title, base + ".docx", scale=scale); outputs.append(o)
            if "md" in fl:
                o, _ = render_md(im, sel, title, base + ".md", scale=scale,
                                 export_date=date); outputs.append(o)
        except Exception as e:
            return {"ok": False, "msg": "导出失败: " + str(e)}
        if not outputs:
            return {"ok": False, "msg": "请至少勾选一种格式"}
        return {"ok": True, "outputs": outputs, "folder": OUT_DIR,
                "count": sum(1 for m in sel if m["type"] != "time"),
                "files": n_hit, "files_total": n_file}

    def reveal(self, path):
        os.system('open -R "%s"' % path)
        return True

    def open_folder(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        os.system('open "%s"' % OUT_DIR)
        return True


def main():
    global window
    api = Api()
    html = os.path.join(HERE, "ui", "index.html")
    window = webview.create_window("微信聊天记录导出", url=html, js_api=api,
                                   width=580, height=860, min_size=(480, 640))
    webview.start()


if __name__ == "__main__":
    main()
