# -*- coding: utf-8 -*-
"""用用户当前配置（zh→ja / 仅译文）跑一个正常中文文件"""
import json, time, urllib.request, urllib.parse, os
BASE = "http://127.0.0.1:18081"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def post(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with OP.open(req, timeout=120) as r:
        return json.loads(r.read().decode())

def get(path):
    with OP.open(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())

src = r"D:/HyMT-Translator/示例文件/sample_zh.txt"
print("源文件大小:", os.path.getsize(src), "字节")
req = urllib.request.Request(BASE + "/api/upload?name=" + urllib.parse.quote("中文样张.txt"),
                             data=open(src, "rb").read(),
                             headers={"Content-Type": "application/octet-stream"})
up = json.loads(OP.open(req, timeout=60).read().decode())
print("上传:", up["supported"], up["size"])

settings = {"tgt_lang": "ja", "src_lang": "zh", "out_mode": "none",
            "out_dir_mode": "app", "out_dir": r"D:/HyMT-Translator/output",
            "chunk_chars": 1200, "use_context": True, "glossary": []}
jid = post("/api/jobs", {"paths": [up["path"]], "settings": settings})["job"]
t0 = time.time()
while time.time() - t0 < 300:
    time.sleep(2)
    snap = get("/api/jobs?job=" + jid)
    f = snap["files"][0]
    print("  %-8s %3d%% %s" % (f["status"], int(f["progress"] * 100), f["stage"][:30]))
    if snap["finished"]:
        print("结果:", f["status"], "| error:", f.get("error"))
        for p in f.get("outputs") or []:
            print("输出:", p, os.path.getsize(p), "字节")
            print("----")
            print(open(p, encoding="utf-8").read().strip()[:400])
        break
