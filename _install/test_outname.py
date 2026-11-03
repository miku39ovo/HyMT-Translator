"""验证输出命名：同一批内同名自动加序号，跨批次重跑仍覆盖同名文件。"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "D:/HyMT-Translator/app")
import config
import engine
import pipeline

SRC = Path("D:/HyMT-Translator/示例文件/sample_zh.txt")
TMP = Path("D:/HyMT-Translator/_install/_同名测试")
NOOP = lambda **kw: None


def make_settings():
    s = dict(config.load())
    s.update(tgt_lang="ja", src_lang="zh", out_mode="none",
             glossary=[], use_context=False, chunk_chars=600)
    # 断言里写死了带「.译文.ja」的文件名，这里必须固定开关，
    # 否则用户把界面上的「加译文标记」关掉后这个测试会假失败
    s["rename_output"] = True
    s["out_dir_mode"] = "app"
    s["out_dir"] = str(config.OUTPUT)
    return s


def main():
    eng = engine.get_engine(config.load())
    eng.boot()
    st = eng.status()
    print(f"引擎: {st.get('state')} / {st.get('backend')}")

    # 1) 单文件，跨两次「新批次」→ 应该都是同一个文件名（覆盖，不堆积）
    s1 = make_settings()
    o1 = pipeline.process(SRC, s1, eng, NOOP, lambda: False)
    s2 = make_settings()
    o2 = pipeline.process(SRC, s2, eng, NOOP, lambda: False)
    print("第1批:", [p.name for p in o1])
    print("第2批:", [p.name for p in o2])
    same_name = [p.name for p in o1] == [p.name for p in o2] == ["sample_zh.译文.ja.txt"]

    # 2) 同一批里两个不同目录的同名文件 → 第二个加 (2)，不能覆盖第一个
    for sub in ("第1章", "第2章"):
        d = TMP / sub
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(SRC, d / "说明.txt")
    s3 = make_settings()
    a = pipeline.process(TMP / "第1章" / "说明.txt", s3, eng, NOOP, lambda: False)
    b = pipeline.process(TMP / "第2章" / "说明.txt", s3, eng, NOOP, lambda: False)
    print("同名 A:", [p.name for p in a])
    print("同名 B:", [p.name for p in b])
    ok_pair = ([p.name for p in a] == ["说明.译文.ja.txt"]
               and [p.name for p in b] == ["说明(2).译文.ja.txt"])
    exists = all(p.exists() for p in a + b)
    print("两个文件都在磁盘上:", exists)

    ok = same_name and ok_pair and exists
    print("RESULT:", "PASS" if ok else "FAIL")

    shutil.rmtree(TMP, ignore_errors=True)
    for p in a + b + o1 + o2:
        Path(p).unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
