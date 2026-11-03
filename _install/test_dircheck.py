# -*- coding: utf-8 -*-
"""目录体检逻辑回归：check_dir / check_out_dir 各分支。"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\HyMT-Translator\app")
import config  # noqa: E402
import pipeline  # noqa: E402

BASE = Path(r"D:\HyMT-Translator\_test_dircheck")
ok_all = True


def show(name, cond, extra=""):
    global ok_all
    ok_all = ok_all and cond
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


shutil.rmtree(BASE, ignore_errors=True)

# 1. 不存在的多级目录 → 自动创建
r = pipeline.check_dir(str(BASE / "a" / "b"))
show("不存在则创建", r["ok"] and r["created"] and (BASE / "a" / "b").is_dir(),
     "created=%s" % r["created"])

# 2. 已存在目录 → 不重复创建
r = pipeline.check_dir(str(BASE / "a" / "b"))
show("已存在不重建", r["ok"] and r["exists"] and not r["created"])

# 3. create=False 且不存在 → 报不存在，且不建
r = pipeline.check_dir(str(BASE / "nope"), create=False)
show("只查不建", (not r["ok"]) and (not r["exists"]) and not (BASE / "nope").exists(),
     r["error"])

# 4. 指向文件 → 明确报错
f = BASE / "a" / "b" / "x.txt"
f.write_text("hi", encoding="utf-8")
r = pipeline.check_dir(str(f))
show("路径是文件", (not r["ok"]) and "文件" in r["error"], r["error"])

# 5. 空路径
r = pipeline.check_dir("")
show("空路径", (not r["ok"]) and bool(r["error"]), r["error"])

# 6. 带引号的路径（用户粘贴常见）
r = pipeline.check_dir('"%s"' % (BASE / "quoted"))
show("去掉引号", r["ok"] and (BASE / "quoted").is_dir())

# 7. 三种输出模式
stamp = datetime.now().strftime("%Y-%m-%d")

r = pipeline.check_out_dir({"out_dir_mode": "app", "out_dir": str(BASE / "app")}, create=False)
show("app 模式体检", r["ok"] or (not r["exists"]), "exists=%s" % r["exists"])

r = pipeline.check_out_dir({"out_dir_mode": "app", "out_dir": str(BASE / "app")}, create=True)
show("app 模式建日期目录", r["ok"] and (BASE / "app" / stamp).is_dir(), r["path"])

r = pipeline.check_out_dir({"out_dir_mode": "custom", "custom_dir": str(BASE / "custom")})
show("custom 模式", r["ok"] and r["path"].endswith("custom"), r["path"])

r = pipeline.check_out_dir({"out_dir_mode": "custom", "custom_dir": ""})
show("custom 未选目录", (not r["ok"]) and "选择" in r["error"], r["error"])

sample = BASE / "a" / "b" / "x.txt"
r = pipeline.check_out_dir({"out_dir_mode": "source"}, sample_src=str(sample))
show("source 模式查父目录", r["ok"] and Path(r["path"]) == sample.parent, r["path"])

shutil.rmtree(BASE, ignore_errors=True)
print("\nRESULT:", "ALL PASS" if ok_all else "SOME FAILED")
