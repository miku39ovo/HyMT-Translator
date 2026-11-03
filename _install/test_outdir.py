"""验证输出位置三选一 + 文件命名开关的所有组合（不跑模型）。"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "D:/HyMT-Translator/app")
import config
import pipeline

TMP = Path("D:/HyMT-Translator/_install/_输出测试")
CUSTOM = TMP / "自定义目录"
OUT = TMP / "统一目录"
SRC = TMP / "原件" / "说明.txt"

cases = []


def mk(**kw):
    s = {"tgt_lang": "ja", "out_dir": str(OUT), "custom_dir": str(CUSTOM)}
    s.update(kw)
    return s


def name_of(p: Path):
    return str(p)


def run(label, settings, tag="译文", ext=".txt", src=SRC):
    p = pipeline.out_path(src, settings, tag, ext)
    rel = p
    try:
        rel = p.relative_to(TMP)
    except ValueError:
        pass
    cases.append((label, str(p), str(rel)))
    return p


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    SRC.parent.mkdir(parents=True, exist_ok=True)
    SRC.write_text("这是一段测试文本。", encoding="utf-8")

    # 1) 统一目录 + 改名开（默认）
    p = run("统一+改名", mk(out_dir_mode="app", rename_output=True))
    ok1 = p.parent.parent == OUT and p.name == "说明.译文.ja.txt"

    # 2) 统一目录 + 改名关
    p = run("统一+不改名", mk(out_dir_mode="app", rename_output=False))
    ok2 = p.parent.parent == OUT and p.name == "说明.txt"

    # 3) 自定义目录 + 改名开（不应再套日期）
    p = run("自定义+改名", mk(out_dir_mode="custom", rename_output=True))
    ok3 = p.parent == CUSTOM and p.name == "说明.译文.ja.txt"

    # 4) 自定义目录 + 改名关
    p = run("自定义+不改名", mk(out_dir_mode="custom", rename_output=False))
    ok4 = p.parent == CUSTOM and p.name == "说明.txt"

    # 5) 源文件同目录 + 改名关 → 不能覆盖源文件，自动变 (译文)
    p = run("源同目录+不改名", mk(out_dir_mode="source", rename_output=False))
    ok5 = p.parent == SRC.parent and p.name == "说明(译文).txt"

    # 6) 源文件同目录 + 改名开 → 正常带标记
    p = run("源同目录+改名", mk(out_dir_mode="source", rename_output=True))
    ok6 = p.parent == SRC.parent and p.name == "说明.译文.ja.txt"

    # 7) 不改名 + 双文件：译文沿用原名，对照必须带标记，否则撞名
    s7 = mk(out_dir_mode="custom", rename_output=False)
    a = pipeline.out_path(SRC, s7, "译文", ".txt")
    b = pipeline.out_path(SRC, s7, "对照", ".txt")
    ok7 = a.name == "说明.txt" and b.name == "说明.对照.txt"

    # 8) 上传件（uploads 下）+ 源文件同目录 → 回退统一输出目录
    up = config.UPLOADS / "testslug" / "说明.txt"
    up.parent.mkdir(parents=True, exist_ok=True)
    up.write_text("上传的临时文件。", encoding="utf-8")
    p = run("上传件+源同目录", mk(out_dir_mode="source", rename_output=True), src=up)
    ok8 = str(OUT) in str(p) and p.parent.name.count("-") == 2

    for label, full, rel in cases:
        print(f"  {label:<18} -> {rel}")

    print()
    verdict = [ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8]
    for i, ok in enumerate(verdict, 1):
        print(f"  场景{i}: {'OK' if ok else 'FAIL'}")
    print("RESULT:", "PASS" if all(verdict) else "FAIL")

    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(up.parent, ignore_errors=True)
    return 0 if all(verdict) else 1


if __name__ == "__main__":
    sys.exit(main())
