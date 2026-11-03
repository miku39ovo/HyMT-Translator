# -*- coding: utf-8 -*-
"""端到端：把 EPUB 样张真译一遍，同时观察新的进度字段（stage/note/eta）。"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18081"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with OP.open(req, timeout=300) as r:
        return json.loads(r.read().decode())


def get(path):
    with OP.open(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode())


def upload(name, data):
    req = urllib.request.Request(BASE + "/api/upload?name=" + urllib.parse.quote(name),
                                 data=data, headers={"Content-Type": "application/octet-stream"})
    return json.loads(OP.open(req, timeout=120).read().decode())


src = Path(r"D:\HyMT-Translator\示例文件\sample_book.epub")
up = upload(src.name, src.read_bytes())
print("上传:", up["supported"], up["size"], "字节")
assert up["supported"], up.get("reason")

settings = {"tgt_lang": "zh", "src_lang": "auto", "out_mode": "interleave",
            "out_dir_mode": "app", "out_dir": r"D:\HyMT-Translator\output",
            "chunk_chars": 1200, "use_context": True, "glossary": []}
jid = post("/api/jobs", {"paths": [up["path"]], "settings": settings})["job"]
print("job:", jid, "（首次会先加载模型）")

t0 = time.time()
last_line = ""
while time.time() - t0 < 900:
    time.sleep(1.5)
    snap = get("/api/jobs?job=" + jid)
    f = snap["files"][0]
    line = "%-9s %3d%% %-8s %s  eta=%s" % (
        f["status"], int(f["progress"] * 100), f["stage"], f.get("note", ""), f.get("eta"))
    if line != last_line:
        print("  %5.1fs  %s" % (time.time() - t0, line))
        last_line = line
    if snap["finished"]:
        print("结束:", f["status"], "| error:", f.get("error"), "| 用时 %.0fs" % (time.time() - t0))
        for p in f.get("outputs") or []:
            p = Path(p)
            print("输出:", p, p.stat().st_size, "字节")
            import sys
            sys.path.insert(0, r"D:\HyMT-Translator\app")
            import pipeline
            txt = pipeline.epub_text(p).split("\n")
            print("--- 前 8 段 ---")
            for x in txt[:8]:
                print("   ", x[:90])
        break
else:
    print("超时未完成")
