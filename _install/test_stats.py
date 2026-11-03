# -*- coding: utf-8 -*-
"""验证：任务完成后返回平均 token 速度（avg_tps / totals.tokens）。"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18081"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path, payload=None, raw=None, q=""):
    data = raw if raw is not None else json.dumps(payload or {}).encode()
    req = urllib.request.Request(BASE + path + q, data=data, method="POST")
    with OP.open(req, timeout=120) as r:
        return json.load(r)


def get(path):
    with OP.open(BASE + path, timeout=30) as r:
        return json.load(r)


def main():
    src = Path(r"D:\HyMT-Translator\示例文件\sample_zh.txt")
    up = post("/api/upload", raw=src.read_bytes(), q="?name=" + src.name)
    assert up["supported"], up
    print("上传 OK →", up["path"])

    job = post("/api/jobs", {"paths": [up["path"]],
                             "settings": {"tgt_lang": "en", "src_lang": "zh",
                                          "out_mode": "none", "chunk_chars": 800}})
    jid = job["job"]
    print("任务", jid)

    seen_progress = []
    t0 = time.time()
    while time.time() - t0 < 600:
        d = get("/api/jobs?job=" + jid)
        f = d["files"][0]
        if f.get("avg_tps"):
            seen_progress.append(f["avg_tps"])
        if d["finished"]:
            break
        time.sleep(1.0)

    print("状态", d["status"])
    print("单文件 avg_tps =", f.get("avg_tps"), "| 实时 tps =", f.get("tps"),
          "| 用时", f.get("elapsed"), "s | 字数", f.get("chars"))
    print("totals =", d["totals"])
    tot = d["totals"]
    ok = bool(f.get("avg_tps")) and tot.get("tokens", 0) > 0 and tot.get("gen_sec", 0) > 0
    if ok:
        print("整体平均 =", round(tot["tokens"] / tot["gen_sec"], 1), "tok/s")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
