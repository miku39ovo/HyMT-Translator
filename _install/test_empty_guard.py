# -*- coding: utf-8 -*-
"""回归：空文件不再产出空白文件，正常文件照常出译文"""
import json, time, urllib.request, urllib.parse
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

def upload(name, data):
    req = urllib.request.Request(BASE + "/api/upload?name=" + urllib.parse.quote(name),
                                 data=data, headers={"Content-Type": "application/octet-stream"})
    return json.loads(OP.open(req, timeout=60).read().decode())

print("=== 1. 上传 0 字节文件 ===")
a = upload("空文件.txt", b"")
print("   supported=%s reason=%s" % (a["supported"], a.get("reason")))

print("=== 2. 上传只有空白字符的文件 ===")
b = upload("全是空格.txt", "   \n\n\t  \n".encode())
print("   supported=%s reason=%s" % (b["supported"], b.get("reason")))

print("=== 3. 正常中文文件走完整任务 ===")
c = upload("正常文件.txt", "机器翻译这几年进步很大。\n\n本地部署的模型可以保护隐私。".encode())
print("   supported=%s" % c["supported"])
settings = {"tgt_lang": "en", "src_lang": "auto", "out_mode": "interleave",
            "out_dir_mode": "app", "out_dir": r"D:/HyMT-Translator/output",
            "chunk_chars": 1200, "use_context": True, "glossary": []}
jid = post("/api/jobs", {"paths": [c["path"]], "settings": settings})["job"]
for _ in range(90):
    time.sleep(2)
    snap = get("/api/jobs?job=" + jid)
    if snap["finished"]:
        f = snap["files"][0]
        print("   状态=%s 输出=%s" % (f["status"], f.get("outputs")))
        for p in f.get("outputs") or []:
            print("   ---- 内容 ----")
            print(open(p, encoding="utf-8").read().strip()[:300])
        break

print("=== 4. 强行提交 0 字节文件（绕过上传校验）===")
jid2 = post("/api/jobs", {"paths": [a["path"]], "settings": settings})["job"]
for _ in range(90):
    time.sleep(2)
    snap = get("/api/jobs?job=" + jid2)
    if snap["finished"]:
        f = snap["files"][0]
        print("   状态=%s error=%s outputs=%s" % (f["status"], f.get("error"), f.get("outputs")))
        break
import glob
print("   目录里有无该空白产物:",
      [p for p in glob.glob(r"D:/HyMT-Translator/output/**/空文件*", recursive=True)])
