# -*- coding: utf-8 -*-
"""验收：pipeline.scan_dir 的扫描结果（递归、过滤、上限、错误分支）。"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))

import pipeline  # noqa: E402

fails = []


def ck(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  -> " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


root = Path(tempfile.mkdtemp(prefix="scan_")) / "小说合集"
(root / "子目录A" / "更深一层").mkdir(parents=True)
(root / ".git").mkdir()
(root / "node_modules").mkdir()

def w(p, text="hello world\n"):
    p.write_text(text, encoding="utf-8")

w(root / "a.txt")
w(root / "b.md")
w(root / "子目录A" / "c.srt", "1\n00:00:01,000 --> 00:00:02,000\nhi\n")
w(root / "子目录A" / "更深一层" / "d.docx.bak", "x")   # 不支持的扩展名
w(root / "子目录A" / "更深一层" / "e.lrc", "[00:01.00]hi\n")
w(root / ".git" / "skip.txt")
w(root / "node_modules" / "skip.txt")
w(root / "~$锁文件.txt")
w(root / "Thumbs.db", "")
(root / "空文件.txt").write_bytes(b"")
(root / "说明.pdf").write_bytes(b"%PDF-1.4 not really")
(root / "封面.jpg").write_bytes(b"xx")

r = pipeline.scan_dir(root)
names = sorted(f["name"] for f in r["files"])
print("---- 递归 ----")
print("files:", names)
ck("ok", r["ok"] is True, r.get("error"))
ck("递归共 5 个", r["count"] == 5, r["count"])   # a b c e + 说明.pdf
ck("跳过 .git/node_modules", not any(f["name"] == "skip.txt" for f in r["files"]))
ck("跳过 ~$ 锁文件", not any("~$" in f["name"] for f in r["files"]))
ck("跳过 Thumbs.db", not any(f["name"] == "Thumbs.db" for f in r["files"]))
ck("0 字节单列", r["empty"] == 1, r["empty"])
ck("不支持格式计数", r["unsupported"] >= 2, r["unsupported"])
ck("扩展名对不上 .bak 被过滤", not any(f["name"].endswith(".bak") for f in r["files"]))
rel_c = [f["rel"] for f in r["files"] if f["name"] == "c.srt"][0]
ck("rel 是相对路径", rel_c == "子目录A/c.srt", rel_c)
dir_c = [f["dir"] for f in r["files"] if f["name"] == "c.srt"][0]
ck("dir 是所在目录", dir_c == "子目录A", dir_c)
ck("root 是文件夹名", r["root"] == "小说合集", r["root"])
ck("默认不截断", r["truncated"] is False)
ck("路径为真实绝对路径", all(os.path.isabs(f["path"]) and os.path.exists(f["path"]) for f in r["files"]))

print("---- 不递归 ----")
r2 = pipeline.scan_dir(root, recursive=False)
ck("不递归只扫根目录", r2["count"] == 3, [f["name"] for f in r2["files"]])

print("---- 上限 ----")
r3 = pipeline.scan_dir(root, limit=2)
ck("limit 生效", r3["count"] == 2, r3["count"])
ck("上限时标记 truncated", r3["truncated"] is True)

print("---- 错误分支 ----")
ck("不存在", pipeline.scan_dir(root / "no_such_dir")["ok"] is False)
ck("不存在有提示", "不存在" in pipeline.scan_dir(root / "no_such_dir")["error"])
ck("传文件不算文件夹", "不是文件夹" in pipeline.scan_dir(root / "a.txt")["error"])
ck("空路径", pipeline.scan_dir("")["ok"] is False)

empty_dir = Path(tempfile.mkdtemp(prefix="emptydir_"))
r4 = pipeline.scan_dir(empty_dir)
ck("空文件夹报错", r4["ok"] is False and "没有能翻译的文件" in r4["error"], r4.get("error"))

print()
print("FAILED:", fails if fails else "none")
shutil.rmtree(root.parent, ignore_errors=True)
shutil.rmtree(empty_dir, ignore_errors=True)
sys.exit(1 if fails else 0)
