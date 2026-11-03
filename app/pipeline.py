# -*- coding: utf-8 -*-
"""文件处理管线：TXT / SRT / LRC / DOCX / PDF / EPUB 的解析、分段、翻译与回写。

设计要点
- 原文文件绝不修改，一律另存为新文件；
- DOCX 采用「复制后原地替换段落文本」的方式，完整保留原始样式、图片、页眉页脚；
- PDF 只能抽取文本层，输出为 DOCX/TXT（无法还原原版式），并做分栏排序与断词还原；
- EPUB 按 OPF spine 顺序逐章改写 XHTML，整包重建（mimetype 保持首个且不压缩）；
- 长文本按句子切块，并用上一块原文作为上下文（官方 contextual 模板）保证前后一致；
- 用户中途点停止时，已翻好的部分照样写出文件，未翻到的段落保留原文。
"""
import io
import os
import posixpath
import re
import shutil
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import config
from engine import chunk_paragraphs, detect_lang, split_long

try:
    from lxml import etree as _etree
    _HAS_LXML = True
except Exception:
    _HAS_LXML = False

try:
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    _HAS_DOCX = True
except Exception:
    _HAS_DOCX = False

try:
    import pymupdf as fitz  # PyMuPDF 新入口
    _HAS_FITZ = True
except Exception:
    try:
        import fitz  # 兼容旧版
        _HAS_FITZ = True
    except Exception:
        _HAS_FITZ = False


class Cancelled(RuntimeError):
    def __init__(self, outputs=None):
        super().__init__("已停止")
        self.outputs = list(outputs or [])      # 停止前已经写出的产物


TEXT_EXT = {".txt", ".md", ".text"}
SUPPORT_EXT = {".txt", ".md", ".srt", ".lrc", ".docx", ".pdf", ".epub"}


# ------------------------------------------------------------------ 工具
def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16", "shift_jis", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def write_text_file(path: Path, text: str, newline="\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def out_base_dir(src: Path, settings) -> Path:
    """按「输出位置」算出这一次应该写到哪个目录。"""
    mode = settings.get("out_dir_mode") or "app"
    stamp = datetime.now().strftime("%Y-%m-%d")

    if mode == "source":
        # 上传进来的文件先落在 uploads/<临时目录>/ 里，它的 parent 是临时目录，
        # 写进去用户根本找不到。这种情况回退到统一输出目录。
        try:
            src.resolve().relative_to(config.UPLOADS.resolve())
            in_uploads = True
        except (ValueError, OSError):
            in_uploads = False
        if in_uploads:
            base = Path(settings.get("out_dir") or config.OUTPUT)
            return base / stamp
        return src.parent

    if mode == "custom":
        d = str(settings.get("custom_dir") or "").strip()
        # 用户自己指定的目录就原样用，不再套一层日期子目录
        return Path(d) if d else Path(settings.get("out_dir") or config.OUTPUT) / stamp

    base = Path(settings.get("out_dir") or config.OUTPUT)
    return base / stamp


def check_dir(path, create=True) -> dict:
    """检查一个目录能否用来放译文：是否存在 / 能不能建 / 是不是可写。

    结果给前端做「开跑前自检」用，所以每一步都单独标明，
    失败时把原因写进 error，别只回一个 False。
    """
    raw = str(path or "").strip().strip('"')
    res = {"path": raw, "exists": False, "created": False,
           "writable": False, "ok": False, "error": ""}
    if not raw:
        res["error"] = "还没有选择目录"
        return res

    p = Path(raw)
    try:
        if p.exists():
            res["exists"] = True
            if not p.is_dir():
                res["error"] = "这个路径是一个文件，请选文件夹"
                return res
        elif create:
            p.mkdir(parents=True, exist_ok=True)
            res["created"] = True
        else:
            res["error"] = "目录不存在"
            return res
    except OSError as e:
        res["error"] = "无法创建目录：%s" % (e.strerror or e)
        return res

    # 真写一个临时文件再删掉，比 os.access 可靠（网络盘/只读盘上
    # os.access 常常报可写，真写才失败）
    probe = p / (".hymt_probe_%d_%d" % (os.getpid(), int(datetime.now().timestamp() * 1000) % 100000))
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        res["writable"] = True
        res["ok"] = True
        res["path"] = str(p)
    except OSError as e:
        res["error"] = "目录不可写：%s" % (e.strerror or e)
        try:
            if probe.exists():
                probe.unlink()
        except OSError:
            pass
    return res


def check_out_dir(settings, sample_src=None, create=True) -> dict:
    """按当前「输出位置」设置，检查这次翻译真正会写入的目录。

    create=False 时只体检不建目录（界面刚打开时用），避免用户还没点
    确认就凭空多出一堆日期文件夹。
    """
    mode = settings.get("out_dir_mode") or "app"
    if mode == "custom":
        res = check_dir(settings.get("custom_dir") or "", create=create)
    elif mode == "source":
        if sample_src:
            res = check_dir(Path(sample_src).parent, create=False)
        else:
            res = {"path": "", "ok": True, "mode": mode, "error": "",
                   "exists": True, "created": False, "writable": True}
    else:
        base = Path(settings.get("out_dir") or config.OUTPUT)
        if create:
            res = check_dir(base / datetime.now().strftime("%Y-%m-%d"), create=True)
        else:
            res = check_dir(base, create=False)
    res["mode"] = mode
    return res


def _out_name(src: Path, tag: str, ext: str, tgt: str, rename: bool, suffix: str) -> str:
    # suffix 是批内重名时的 (2)/(3)，放在扩展名之前、紧跟原文件名，
    # 这样「说明(2).译文.ja.txt」比「说明.译文.ja(2).txt」更好认
    if rename:
        return f"{src.stem}{suffix}.{tag}.{tgt}{ext}"
    # 不改名：只要产出的是译文、扩展名也没变，就原样沿用原文件名
    if tag == "译文" and ext.lower() == src.suffix.lower():
        return f"{src.stem}{suffix}{ext}"
    # 对照 / 扩展名变了（如 md→txt）→ 保留标记，否则同批产出的几份文件分不清
    return f"{src.stem}{suffix}.{tag}{ext}"


def out_path(src: Path, settings, tag: str, ext: str) -> Path:
    base_dir = out_base_dir(src, settings)
    tgt = settings.get("tgt_lang", "zh")
    rename = settings.get("rename_output", True)
    # 导入整个文件夹时，不同子目录下经常有同名文件（第1章/说明.txt、第2章/说明.txt）。
    # 这里按「同一批任务内已占用的文件名」去重，避免后一个把前一个的译文覆盖掉；
    # 跨批次重新翻译同一个文件仍然是覆盖，不会越跑越多。
    used = settings.setdefault("_used_names", set())
    n = 1
    while True:
        suffix = "" if n == 1 else f"({n})"
        name = _out_name(src, tag, ext, tgt, rename, suffix)
        # 关掉改名 + 输出位置选「源文件同目录」时，目标会正好等于源文件本身，
        # 那一步会把原件覆盖掉，必须挡下来
        try:
            same_as_src = (base_dir / name).resolve() == src.resolve()
        except OSError:
            same_as_src = False
        if same_as_src:
            name = f"{src.stem}(译文){suffix}{ext}"
        if name.lower() not in used:
            break
        n += 1
    used.add(name.lower())
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / name


def join_lines(lines):
    """原文换行合并：中日文直接拼接，西文补空格。"""
    txt = ""
    for ln in lines:
        ln = ln.strip()
        if not txt:
            txt = ln
        elif re.search(r"[A-Za-z0-9,.;:!?\"')\]]$", txt) and re.match(r"[A-Za-z0-9(\"'\[$]", ln):
            txt += " " + ln
        else:
            txt += ln
    return txt


_WIDE = r"[\u1100-\u11ff\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]"
_CLOSERS = "。，、；：！？）」』】》…—,.!?;:)]}\"'”’"


def _is_wide(ch):
    return bool(re.match(_WIDE, ch))


def _width(s):
    return sum(1.0 if _is_wide(c) else 0.55 for c in s)


def wrap_text(text: str, cjk_width=24, latin_width=46, max_lines=2):
    """字幕/歌词换行：按标点断行，避免行首出现标点。"""
    text = " ".join((text or "").split())
    if not text:
        return [""]
    wide_ratio = sum(1 for c in text if _is_wide(c)) / max(1, len(text))
    limit = cjk_width if wide_ratio > 0.4 else latin_width

    lines, rest = [], text
    while rest:
        if _width(rest) <= limit:
            lines.append(rest)
            break
        acc, cut = 0.0, 0
        for i, ch in enumerate(rest):
            w = 1.0 if _is_wide(ch) else 0.55
            if acc + w > limit:
                break
            acc += w
            cut = i + 1
        seg = rest[:cut]
        # 优先在标点/空格处断开
        best = -1
        for p in "。！？；：，、,.!?;: ":
            k = seg.rfind(p)
            if k > best:
                best = k
        if best >= cut * 0.5:
            cut = best + 1
        # 行首不能是收尾标点
        while cut < len(rest) and rest[cut] in _CLOSERS:
            cut += 1
        lines.append(rest[:cut].strip())
        rest = rest[cut:].strip()

    if len(lines) > max_lines:      # 超出行数就并到最后一行，宁长勿丢
        lines = lines[: max_lines - 1] + ["".join(lines[max_lines - 1:])]
    return [l for l in lines if l]


def _set_cn_font(run, name="等线", size=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size:
        run.font.size = Pt(size)


# ------------------------------------------------------------------ 段落级翻译（所有格式共用）
class Translator:
    def __init__(self, engine, settings, report, cancel):
        self.engine = engine
        self.s = settings
        self.report = report or (lambda **kw: None)
        self.cancel = cancel or (lambda: False)
        self.glossary = settings.get("glossary") or []
        self.limit = int(settings.get("chunk_chars", 1200))
        self.use_ctx = bool(settings.get("use_context", True))
        self.src = settings.get("src_lang", "auto")
        self.tgt = settings.get("tgt_lang", "zh")
        self.ctx_tail = ""
        self.total = 0
        self.done = 0
        self.chars = 0
        self.tokens = 0          # 本轮累计生成的 token 数（用于算平均速度）
        self.gen_sec = 0.0       # 本轮累计的纯推理耗时（不含解析/写文件）
        self.canceled = False       # 用户点了停止：已译部分保留，交给上层落盘

    def check(self):
        if self.cancel():
            raise Cancelled()

    def set_total(self, n: int):
        self.total = max(1, n)
        self.report(done=0, total=self.total, stage="翻译中",
                    note=f"共 {self.total} 段待翻译")

    def _one(self, text: str, ctx=None):
        self.check()
        res = self.engine.translate(text, self.src, self.tgt, self.glossary, ctx, self.cancel)
        self.done += 1
        self.chars += len(text)
        self.tokens += int(res.get("tokens") or 0)
        self.gen_sec += float(res.get("seconds") or 0)
        self.report(done=self.done, total=self.total, stage="翻译中",
                    tps=round(getattr(self.engine, "last_tps", 0) or 0, 1) or None,
                    used_chars=self.chars,
                    tokens=self.tokens, gen_sec=self.gen_sec,
                    note=f"已完成 {self.done}/{self.total} 段")
        return res["text"]

    # 段落数组 → 译文数组（保持一一对应）
    def paragraphs(self, texts):
        n = len(texts)
        self.set_total(max(1, n))
        out = [""] * n
        groups = []            # [(chunk_text, [idx...])]
        cur, idxs, size = [], [], 0
        for i, t in enumerate(texts):
            t = (t or "").strip()
            if not t:
                continue
            if len(t) > self.limit:
                # 超长段落单独处理，按句切分后合并
                groups.append((t, [i], True))
                continue
            if cur and size + len(t) > self.limit:
                groups.append(("\n\n".join(cur), idxs, False))
                cur, idxs, size = [], [], 0
            cur.append(t)
            idxs.append(i)
            size += len(t) + 2
        if cur:
            groups.append(("\n\n".join(cur), idxs, False))

        try:
            for gtext, gidx, long_single in groups:
                self.check()
                if long_single:
                    pieces = split_long(gtext, self.limit)
                    parts = []
                    try:
                        for p in pieces:
                            ctx = self.ctx_tail if self.use_ctx else None
                            parts.append(self._one(p, ctx))
                            if self.use_ctx:
                                self.ctx_tail = p
                    finally:
                        # 即使中途停止，这一大段已翻好的部分也要留下
                        if parts:
                            out[gidx[0]] = "\n".join(parts)
                    continue

                ctx = self.ctx_tail if self.use_ctx else None
                translated = self._one(gtext, ctx)
                if self.use_ctx:
                    self.ctx_tail = gtext
                parts = [p for p in re.split(r"\n\s*\n", translated) if p.strip()]
                if len(parts) == len(gidx):
                    for k, gi in enumerate(gidx):
                        out[gi] = parts[k].strip().replace("\n", " ")
                elif len(gidx) == 1:
                    out[gidx[0]] = translated.strip().replace("\n", " ")
                else:
                    # 段落数不匹配 → 逐段重译，保证对齐
                    for gi in gidx:
                        out[gi] = self._one(texts[gi], None).strip().replace("\n", " ")
        except Cancelled:
            # 中途停止：不再往下翻，未翻到的段落先拿原文占位，保证产物结构完整
            self.canceled = True
            self.report(stage="已停止", note=f"停在 {self.done}/{self.total} 段")

        for i in range(n):
            if not out[i] and (texts[i] or "").strip():
                out[i] = texts[i].strip()
        return out


def _tr(tr, engine, settings, report, cancel):
    return tr or Translator(engine, settings, report, cancel)


# ------------------------------------------------------------------ TXT
def do_txt(src: Path, settings, engine, report, cancel, tr=None):
    report(stage="解析中", note="读取文本文件")
    text = read_text_file(src).replace("\r\n", "\n").replace("\r", "\n")
    paras = text.split("\n\n")
    _require_text(paras, src)
    tr = _tr(tr, engine, settings, report, cancel)
    translated = tr.paragraphs(paras)
    mode = settings.get("out_mode", "interleave")
    outs = []
    report(stage="写出文件", note="正在生成译文")

    body_pair = []
    for o, t in zip(paras, translated):
        if not o.strip():
            body_pair.append(("", ""))
        else:
            body_pair.append((o.strip(), t.strip()))

    def render(mode_):
        if mode_ == "none":
            return "\n\n".join(t for _, t in body_pair if t)
        buf = []
        for o, t in body_pair:
            if not o and not t:
                continue
            if o:
                buf.append(o)
            if t and not _same_text(o, t):
                buf.append(t)
            buf.append("")
        return "\n".join(buf).strip() + "\n"

    if mode == "separate":
        p1 = out_path(src, settings, "译文", ".txt")
        p2 = out_path(src, settings, "对照", ".txt")
        write_text_file(p1, render("none"))
        write_text_file(p2, render("interleave"))
        outs += [p1, p2]
    else:
        p = out_path(src, settings, "译文" if mode == "none" else "对照", ".txt")
        write_text_file(p, render(mode if mode in ("none", "interleave") else "interleave"))
        outs.append(p)
    return outs


# ------------------------------------------------------------------ SRT
SRT_TIME = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->")


def parse_srt(text: str):
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = []
    for raw in re.split(r"\n\s*\n", text):
        lines = [l for l in raw.split("\n") if l.strip()]
        if not lines:
            continue
        idx, tl, body = "", "", []
        if re.fullmatch(r"\d+", lines[0].strip()):
            idx = lines[0].strip()
            rest = lines[1:]
        else:
            rest = lines
        if rest and SRT_TIME.search(rest[0]):
            tl = rest[0].strip()
            body = rest[1:]
        else:
            body = rest
        blocks.append({"index": idx, "time": tl, "lines": body})
    return blocks


def _tc(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def do_srt(src: Path, settings, engine, report, cancel, tr=None):
    report(stage="解析中", note="解析字幕时间轴")
    blocks = parse_srt(read_text_file(src))
    if not blocks:
        raise ValueError("未解析到字幕块，请确认是标准 SRT 文件")
    texts = [join_lines(b["lines"]) for b in blocks]
    _require_text(texts, src)
    tr = _tr(tr, engine, settings, report, cancel)
    translated = tr.paragraphs(texts)
    report(stage="写出文件", note=f"回写字幕 {len(blocks)} 条")

    def render(mode_):
        out = []
        for i, b in enumerate(blocks):
            n = i + 1
            t = translated[i] or ""
            head = b["time"] or f"{_tc(i * 3.0)} --> {_tc(i * 3.0 + 2.5)}"
            out.append(str(n))
            out.append(head)
            if mode_ == "none":
                out.extend(wrap_text(t))
            else:
                if b["lines"]:
                    out.extend([l.strip() for l in b["lines"] if l.strip()])
                if not _same_text(" ".join(b["lines"]), t):
                    out.extend(wrap_text(t))
            out.append("")
        return "\n".join(out)

    outs = []
    mode = settings.get("out_mode", "interleave")
    if mode == "separate":
        p1 = out_path(src, settings, "译文", ".srt")
        p2 = out_path(src, settings, "对照", ".srt")
        write_text_file(p1, render("none"))
        write_text_file(p2, render("interleave"))
        outs += [p1, p2]
    else:
        p = out_path(src, settings, "译文" if mode == "none" else "对照", ".srt")
        write_text_file(p, render(mode if mode in ("none", "interleave") else "interleave"))
        outs.append(p)
    return outs


# ------------------------------------------------------------------ LRC
LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
LRC_META = re.compile(r"^\[(ti|ar|al|by|offset|re|ve|length):(.*)\]\s*$", re.I)


def do_lrc(src: Path, settings, engine, report, cancel, tr=None):
    report(stage="解析中", note="解析歌词时间轴")
    raw = read_text_file(src).replace("\r\n", "\n")
    entries = []          # (kind, payload)
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            entries.append(("blank", ""))
            continue
        m = LRC_META.match(s)
        if m:
            entries.append(("meta", s))
            continue
        times = LRC_TIME.findall(s)
        if times:
            body = LRC_TIME.sub("", s).strip()
            stamps = [f"[{a}:{b}" + (f".{c}" if c else "") + "]" for a, b, c in times]
            entries.append(("line", (stamps, body)))
        else:
            entries.append(("plain", s))

    idxs = [i for i, (k, p) in enumerate(entries) if k in ("line", "plain") and (p[1] if k == "line" else p)]
    texts = []
    for i in idxs:
        k, p = entries[i]
        texts.append(p[1] if k == "line" else p)
    _require_text(texts, src)

    tr = _tr(tr, engine, settings, report, cancel)
    translated = tr.paragraphs(texts) if texts else []
    report(stage="写出文件", note=f"回写歌词 {len(idxs)} 行")
    tmap = {i: t for i, t in zip(idxs, translated)}

    mode = settings.get("out_mode", "interleave")
    if mode == "table":
        mode = "interleave"

    def render(mode_):
        out = []
        for i, (k, p) in enumerate(entries):
            if k == "meta":
                out.append(p)
            elif k == "blank":
                out.append("")
            elif k == "plain":
                if mode_ == "none":
                    out.append(tmap.get(i, p) or "")
                else:
                    out.append(p)
                    if tmap.get(i):
                        out.append(tmap[i])
            else:
                stamps, body = p
                t = tmap.get(i, "") or ""
                if not body:
                    out.append("".join(stamps))
                    continue
                if mode_ == "none":
                    for st in stamps:
                        out.append(f"{st}{t}")
                else:
                    for st in stamps:
                        out.append(f"{st}{body}")
                    if not _same_text(body, t):
                        for st in stamps:
                            out.append(f"{st}{t}")
        return "\n".join(out).rstrip() + "\n"

    outs = []
    if mode == "separate":
        p1 = out_path(src, settings, "译文", ".lrc")
        p2 = out_path(src, settings, "对照", ".lrc")
        write_text_file(p1, render("none"))
        write_text_file(p2, render("interleave"))
        outs += [p1, p2]
    else:
        p = out_path(src, settings, "译文" if mode == "none" else "对照", ".lrc")
        write_text_file(p, render(mode))
        outs.append(p)
    return outs


# ------------------------------------------------------------------ DOCX

def _same_text(a, b):
    """译文与原文一致（专有名词/已有中文段）时不重复输出。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) <= 12 and a.lower() == b.lower()

def _set_para_text(par, text):
    """把段落文本整体替换，保留第一个 run 的字体格式与段落样式。"""
    text = text or ""
    runs = par.runs
    if not runs:
        par.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r.text = ""


def _insert_after(par, text, style=None):
    """在段落之后插入一段译文：克隆原段落属性（缩进/编号/对齐），再换成译文文本。"""
    from copy import deepcopy
    from docx.text.paragraph import Paragraph
    src_p = par._p
    new_p = deepcopy(src_p)
    for tag in ("w:r", "w:hyperlink", "w:bookmarkStart", "w:bookmarkEnd"):
        for el in new_p.findall(qn(tag)):
            new_p.remove(el)
    src_p.addnext(new_p)
    np = Paragraph(new_p, par._parent)
    run = np.add_run(text)
    if par.runs:
        rPr = par.runs[0]._element.find(qn("w:rPr"))
        if rPr is not None:
            run._element.insert(0, deepcopy(rPr))
    return np


def _iter_block_items(parent):
    from docx.document import Document as _Doc
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    if isinstance(parent, _Doc):
        parent_elm = parent.element.body
    else:
        parent_elm = parent._tc
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _docx_paragraph_slots(document):
    """收集所有可翻译的段落：正文段落 + 表格单元格段落（含嵌套表）。

    单元格合并时 python-docx 的 row.cells 会重复返回同一个 w:tc，
    这里按 XML 节点去重，否则同一段会被翻译两次、表格模式还会出现重复行。
    """
    slots, seen_p, seen_tc = [], set(), set()

    def add_par(p):
        key = id(p._p)
        if key in seen_p:
            return
        seen_p.add(key)
        slots.append(p)

    def walk(parent):
        for block in _iter_block_items(parent):
            if block.__class__.__name__ == "Paragraph":
                add_par(block)
            else:
                for row in block.rows:
                    for cell in row.cells:
                        k = id(cell._tc)
                        if k in seen_tc:
                            continue
                        seen_tc.add(k)
                        walk(cell)

    walk(document)
    return slots


def do_docx(src: Path, settings, engine, report, cancel, tr=None):
    if not _HAS_DOCX:
        raise RuntimeError("缺少 python-docx，无法处理 Word 文件")
    mode = settings.get("out_mode", "interleave")
    report(stage="解析中", note="读取 Word 结构")
    document = docx.Document(str(src))
    slots = _docx_paragraph_slots(document)
    texts = [p.text.strip() for p in slots]
    _require_text(texts, src)

    tr = _tr(tr, engine, settings, report, cancel)
    translated = tr.paragraphs(texts)
    report(stage="写出文件", note=f"回写 Word 段落 {len(texts)} 个")

    # 段落样式用于左右对照时保留标题层级
    if mode == "table":
        return _docx_table_output(src, settings, [(o, t) for o, t in zip(texts, translated) if o])

    out_doc = docx.Document(str(src))          # 复制一份原始结构（含图片/页眉）
    slots2 = _docx_paragraph_slots(out_doc)
    for i, par in enumerate(slots2):
        if i >= len(translated):
            break
        orig = texts[i]
        t = translated[i]
        if not orig:
            continue
        if mode == "none":
            _set_para_text(par, t)
        else:
            _set_para_text(par, orig)
            if t and not _same_text(orig, t):
                _insert_after(par, t, par.style)

    outs = []
    if mode == "separate":
        only = docx.Document(str(src))
        for i, par in enumerate(_docx_paragraph_slots(only)):
            if i < len(translated) and texts[i]:
                _set_para_text(par, translated[i])
        p1 = out_path(src, settings, "译文", ".docx")
        p2 = out_path(src, settings, "对照", ".docx")
        only.save(str(p1))
        out_doc.save(str(p2))
        outs += [p1, p2]
    else:
        p = out_path(src, settings, "译文" if mode == "none" else "对照", ".docx")
        out_doc.save(str(p))
        outs.append(p)
    return outs


def _docx_table_output(src: Path, settings, pairs):
    """左右对照：原文 | 译文 两列表格，适合论文精读。"""
    doc = docx.Document()
    sec = doc.sections[0]
    st = doc.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "等线")

    title = doc.add_paragraph()
    r = title.add_run(f"{src.stem}  ·  原文 / 译文对照")
    r.bold = True
    r.font.size = Pt(14)

    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    hdr = table.rows[0].cells
    for i, name in enumerate(("原文", "译文")):
        p = hdr[i].paragraphs[0]
        run = p.add_run(name)
        run.bold = True
        _set_cn_font(run, "等线", 10.5)
    for o, t in pairs:
        cells = table.add_row().cells
        for i, txt in enumerate((o, t)):
            p = cells[i].paragraphs[0]
            run = p.add_run(txt)
            _set_cn_font(run, "等线", 10.5)
    p = out_path(src, settings, "对照", ".docx")
    doc.save(str(p))
    return [p]


# ------------------------------------------------------------------ PDF
def _join_pdf_lines(lines):
    """把同一段落的若干行拼成一段：英文断词还原、中日文直接相连。"""
    text = ""
    for ln in lines:
        t = ln["t"].strip()
        if not t:
            continue
        if not text:
            text = t
            continue
        if re.search(r"[A-Za-z]-$", text) and re.match(r"[a-z]", t):
            text = text[:-1] + t                     # exam-\nple -> example
        elif re.search(r"[A-Za-z0-9,.;:!?\"')\]]$", text) and re.match(r"[A-Za-z0-9(\"'\[$]", t):
            text += " " + t
        else:
            text += t
    return re.sub(r"\s{2,}", " ", text).strip()


def _group_pdf_lines(lines, paras):
    """按行距/字号/缩进把行合并成段落，结果追加到 paras。"""
    cur = []
    for ln in lines:
        if cur:
            prev = cur[-1]
            gap = ln["y0"] - prev["y1"]
            lh = max(prev["y1"] - prev["y0"], 1.0)
            same_size = abs(ln["size"] - prev["size"]) < 1.3
            end_punct = prev["t"][-1] in "。！？.!?:：；;”\""
            indent = ln["x0"] > prev["x0"] + max(6.0, prev["size"] * 0.9)
            new_para = (
                gap > max(lh * 0.85, prev["size"] * 0.85)
                or (end_punct and gap > lh * 0.45)
                or not same_size
                or (indent and gap > lh * 0.3)
            )
            if new_para:
                paras.append(cur)
                cur = []
        cur.append(ln)
    if cur:
        paras.append(cur)


def extract_pdf_paragraphs(path: Path):
    if not _HAS_FITZ:
        raise RuntimeError("缺少 PyMuPDF，无法处理 PDF")
    doc = fitz.open(str(path))
    out, low = [], 0
    for page in doc:
        raw = page.get_text("text")
        if not raw.strip():
            low += 1
        W, H = page.rect.width, page.rect.height
        mid = W / 2
        lines = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                txt = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if not txt:
                    continue
                x0, y0, x1, y1 = line["bbox"]
                size = max((s.get("size", 10) for s in line.get("spans", [])), default=10)
                lines.append({"t": txt, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                              "size": size, "col": 0})
        # 去掉页眉页脚里的纯页码
        keep = []
        for ln in lines:
            if (ln["y1"] < H * 0.05 or ln["y0"] > H * 0.95) and len(ln["t"]) < 24:
                continue
            if re.fullmatch(r"[\s\-–—·\dIVXivx]{1,10}", ln["t"]):
                continue
            keep.append(ln)
        for ln in keep:
            if ln["x1"] - ln["x0"] > W * 0.62:
                ln["col"] = 2
            else:
                ln["col"] = 0 if (ln["x0"] + ln["x1"]) / 2 < mid else 1

        groups = []
        for col in (0, 1, 2):
            cl = sorted([l for l in keep if l["col"] == col], key=lambda l: (round(l["y0"], 1), l["x0"]))
            _group_pdf_lines(cl, groups)

        has_two = any(g[0]["col"] == 0 for g in groups) and any(g[0]["col"] == 1 for g in groups)
        if has_two:
            full = sorted([g for g in groups if g[0]["col"] == 2], key=lambda g: g[0]["y0"])

            def band(y):
                return sum(1 for f in full if f[0]["y0"] < y - 2)

            groups.sort(key=lambda g: (band(g[0]["y0"]), g[0]["col"], g[0]["y0"]))
        else:
            groups.sort(key=lambda g: (round(g[0]["y0"], 1), g[0]["x0"]))

        for g in groups:
            txt = _join_pdf_lines(g)
            if txt:
                out.append(txt)
    pages = len(doc)
    doc.close()
    return out, pages, low


def do_pdf(src: Path, settings, engine, report, cancel, tr=None):
    report(stage="解析中", note="抽取 PDF 文本层")
    paras, pages, low = extract_pdf_paragraphs(src)
    if not paras or (low and low >= max(1, pages * 0.9) and sum(len(p) for p in paras) < 200):
        raise ValueError("该 PDF 没有可用文本层（疑似扫描件），需要先 OCR 再翻译")
    _require_text(paras, src)
    report(stage="解析中", note=f"已抽取 {pages} 页 / {len(paras)} 段", force=True)

    tr = _tr(tr, engine, settings, report, cancel)
    translated = tr.paragraphs(paras)
    report(stage="写出文件", note=f"生成对照文档 {len(paras)} 段")
    pairs = [(o, t) for o, t in zip(paras, translated) if o.strip() and not _same_text(o, t)]
    mode = settings.get("out_mode", "interleave")
    outs = []

    def build_doc(mode_):
        doc = docx.Document()
        st = doc.styles["Normal"]
        st.font.name = "Times New Roman"
        st.font.size = Pt(11)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "等线")
        title = doc.add_paragraph()
        run = title.add_run(src.stem)
        run.bold = True
        run.font.size = Pt(15)
        _set_cn_font(run, "等线", 15)
        for o, t in pairs:
            if mode_ == "interleave":
                p1 = doc.add_paragraph()
                r1 = p1.add_run(o)
                _set_cn_font(r1, "等线", 10.5)
                r1.font.color.rgb = RGBColor(0x55, 0x5F, 0x6D)
                p2 = doc.add_paragraph()
                r2 = p2.add_run(t)
                _set_cn_font(r2, "等线", 11)
                p2.paragraph_format.space_after = Pt(8)
            else:
                p = doc.add_paragraph()
                r = p.add_run(t)
                _set_cn_font(r, "等线", 11)
                p.paragraph_format.space_after = Pt(6)
        return doc

    if mode == "table":
        outs += _docx_table_output(src, settings, pairs)
        # 同时给一份纯译文 docx，方便直接用
        p = out_path(src, settings, "译文", ".docx")
        build_doc("none").save(str(p))
        outs.append(p)
        return outs

    if mode == "separate":
        p1 = out_path(src, settings, "译文", ".docx")
        p2 = out_path(src, settings, "对照", ".docx")
        build_doc("none").save(str(p1))
        build_doc("interleave").save(str(p2))
        outs += [p1, p2]
        t = out_path(src, settings, "对照", ".txt")
        write_text_file(t, "\n\n".join(f"{o}\n{t_}" for o, t_ in pairs))
        outs.append(t)
        return outs

    p = out_path(src, settings, "译文" if mode == "none" else "对照", ".docx")
    build_doc("none" if mode == "none" else "interleave").save(str(p))
    outs.append(p)
    return outs


# ------------------------------------------------------------------ EPUB
# EPUB = zip(mimetype + META-INF/container.xml + OPF + 若干 XHTML 章节)
# 思路：按 OPF spine 顺序逐章解析 XHTML，只翻译「最内层块级元素」的文字，
#       整包按原压缩方式重建，mimetype 保持第一条且不压缩（否则部分阅读器拒读）。
_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd",
               "blockquote", "td", "th", "figcaption", "caption"}
_SKIP_TAGS = {"pre", "code", "script", "style", "svg", "math", "textarea", "kbd", "samp"}


def _local(el):
    if not isinstance(el.tag, str):
        return ""
    return el.tag.rsplit("}", 1)[-1]


def _epub_spine(zf):
    """返回按阅读顺序排列的 [(zip 内路径, lxml tree), ...]。"""
    parser = _etree.XMLParser(recover=True, resolve_entities=False,
                              huge_tree=True, no_network=True, load_dtd=False)
    names = set(zf.namelist())
    root = None
    try:
        c = _etree.fromstring(zf.read("META-INF/container.xml"), parser)
        for el in c.iter():
            if _local(el) == "rootfile" and el.get("full-path"):
                root = el.get("full-path")
                break
    except Exception:
        pass
    if (not root or root not in names):
        root = next((n for n in sorted(names) if n.lower().endswith(".opf")), None)
    if not root:
        raise ValueError("不是有效的 EPUB：找不到 OPF 包文件（META-INF/container.xml 缺失）")

    base = posixpath.dirname(root)
    opf = _etree.fromstring(zf.read(root), parser)
    manifest, order = {}, []
    for el in opf.iter():
        if _local(el) == "item" and el.get("id") and el.get("href"):
            manifest[el.get("id")] = (el.get("href"), (el.get("media-type") or "").lower())
        elif _local(el) == "itemref" and el.get("idref"):
            order.append(el.get("idref"))
    if not order:
        order = [k for k, (h, m) in manifest.items() if "html" in m]

    docs, seen = [], set()
    for idref in order:
        href = manifest.get(idref, (None, ""))[0]
        if not href:
            continue
        name = posixpath.normpath(posixpath.join(base, unquote(href.split("#")[0])))
        if name in seen or name not in names:
            continue
        seen.add(name)
        try:
            docs.append((name, _etree.parse(io.BytesIO(zf.read(name)), parser)))
        except Exception:
            continue
    if not docs:                       # spine 异常时退化为扫描全部 xhtml
        for name in sorted(names):
            if name.lower().endswith((".xhtml", ".html", ".htm")):
                try:
                    docs.append((name, _etree.parse(io.BytesIO(zf.read(name)), parser)))
                except Exception:
                    pass
    return docs


def _epub_blocks(tree):
    """收集最内层块级元素及其纯文本（外层的 blockquote/li 等不重复取）。"""
    res = []
    for el in tree.iter():
        if _local(el) not in _BLOCK_TAGS:
            continue
        skip = False
        p = el.getparent()
        while p is not None:
            if _local(p) in _SKIP_TAGS:
                skip = True
                break
            p = p.getparent()
        if skip:
            continue
        if any(_local(d) in _BLOCK_TAGS for d in el.iterdescendants()):
            continue
        txt = "".join(el.itertext()).strip()
        if txt:
            res.append((el, txt))
    return res


_INLINE_TAGS = {"i", "b", "em", "strong", "span", "a", "u", "small", "sub", "sup",
                "code", "mark", "cite", "q", "s", "del", "ins", "abbr"}


def _epub_set(el, text):
    """整段替换文字：清空内部所有节点文字与 tail，再把译文放到段落开头。"""
    for d in el.iter():
        d.text = None
        if d is not el:
            d.tail = None
    el.text = text
    # 顺手清掉被清空的内联标签（否则会留下 <i/> 这种空壳）；带 id 的锚点保留
    for child in list(el.iter())[::-1]:
        if child is el or _local(child) not in _INLINE_TAGS:
            continue
        if child.get("id") or child.get("name"):
            continue
        if not len(child) and not (child.text or "").strip():
            parent = child.getparent()
            if parent is not None:
                parent.remove(child)


def _epub_insert_after(el, text):
    """段段对照：克隆原段落（保留标签/class/样式），在它后面插一段译文。"""
    new = deepcopy(el)
    _epub_set(new, text)
    new.tail, el.tail = el.tail, None
    el.addnext(new)


def epub_text(path: Path) -> str:
    """把整本 EPUB 的正文抽成纯文本（预览与空文件判断用）。"""
    if not _HAS_LXML:
        return ""
    with zipfile.ZipFile(path) as zf:
        parts = []
        for _, tree in _epub_spine(zf):
            parts += [t for _, t in _epub_blocks(tree)]
        return "\n".join(parts)


def do_epub(src: Path, settings, engine, report, cancel, tr=None):
    if not _HAS_LXML:
        raise RuntimeError("缺少 lxml，无法处理 EPUB")

    report(stage="解析中", note="解析 EPUB 目录结构")
    with zipfile.ZipFile(src) as zf:
        docs = _epub_spine(zf)
        if not docs:
            raise ValueError("EPUB 里没有可读的 XHTML 章节")
        texts = []
        for _, tree in docs:
            texts += [t for _, t in _epub_blocks(tree)]
        _require_text(texts, src)
        report(stage="解析中", note=f"{len(docs)} 个章节 / {len(texts)} 个段落", force=True)

        tr = _tr(tr, engine, settings, report, cancel)
        translated = tr.paragraphs(texts)

        mode = settings.get("out_mode", "interleave")
        if mode == "table":
            mode = "interleave"          # EPUB 无「左右两栏」排版，退化为段段对照

        report(stage="写出文件", note=f"重建 EPUB（{len(docs)} 章）", force=True)

        def build(interleave):
            """重新解析一份干净的树，套上译文，返回 {章节路径: 新字节}。"""
            with zipfile.ZipFile(src) as z2:
                docs2 = _epub_spine(z2)
                units = [el for _, tree in docs2 for el, _ in _epub_blocks(tree)]
                if len(units) != len(texts):
                    raise ValueError("EPUB 结构在翻译过程中发生变化，已中止")
                for el, orig, t in zip(units, texts, translated):
                    if not t or _same_text(orig, t):
                        continue
                    if interleave:
                        _epub_insert_after(el, t)
                    else:
                        _epub_set(el, t)
                out = {}
                for name, tree in docs2:
                    doc = tree.docinfo.doctype or None
                    out[name] = _etree.tostring(tree, xml_declaration=True,
                                                encoding="utf-8", doctype=doc)
                return out

        def save(payload, path):
            with zipfile.ZipFile(src) as zin, zipfile.ZipFile(path, "w") as zout:
                if "mimetype" in zin.namelist():          # 必须首个且不压缩
                    zout.writestr(zipfile.ZipInfo("mimetype"),
                                  zin.read("mimetype"),
                                  compress_type=zipfile.ZIP_STORED)
                for item in zin.infolist():
                    if item.filename == "mimetype":
                        continue
                    zout.writestr(item, payload.get(item.filename) or zin.read(item.filename))

        outs = []
        if mode == "separate":
            p1 = out_path(src, settings, "译文", ".epub")
            p2 = out_path(src, settings, "对照", ".epub")
            save(build(False), p1)
            save(build(True), p2)
            outs += [p1, p2]
        else:
            p = out_path(src, settings, "译文" if mode == "none" else "对照", ".epub")
            save(build(mode != "none"), p)
            outs.append(p)
        return outs


# ------------------------------------------------------------------ 入口
HANDLERS = {".txt": do_txt, ".md": do_txt, ".srt": do_srt, ".lrc": do_lrc,
            ".docx": do_docx, ".pdf": do_pdf, ".epub": do_epub}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in HANDLERS


def _require_text(texts, src: Path):
    """一段可翻译的文字都没有时，直接报错——绝不产出空白文件。"""
    if not any((t or "").strip() for t in texts):
        raise ValueError(f"《{src.name}》里没有可翻译的文字："
                         "文件是空的、只有图片，或是扫描件（需先 OCR 再翻译）")


def looks_empty(path: Path) -> bool:
    """上传时的轻量检查：0 字节、空文档、扫描件一眼识破，不必等到跑完才发现。"""
    try:
        if path.stat().st_size == 0:
            return True
        ext = path.suffix.lower()
        if ext in (".txt", ".md"):
            return not read_text_file(path).strip()
        if ext == ".srt":
            blocks = parse_srt(read_text_file(path))
            return not any(join_lines(b["lines"]).strip() for b in blocks)
        if ext == ".lrc":
            return not any(LRC_TIME.search(l) for l in read_text_file(path).split("\n"))
        if ext == ".docx":
            if not _HAS_DOCX:
                return False
            doc = docx.Document(str(path))
            return not any(p.text.strip() for p in _docx_paragraph_slots(doc))
        if ext == ".pdf":
            if path.stat().st_size > 60 * 1024 * 1024:   # 超大 PDF 不预检，交给翻译阶段
                return False
            paras, _, _ = extract_pdf_paragraphs(path)
            return not any(p.strip() for p in paras)
        if ext == ".epub":
            if path.stat().st_size > 200 * 1024 * 1024:
                return False
            return not any(p.strip() for p in epub_text(path).split("\n"))
    except Exception:
        return False        # 判断本身失败时不拦截，交给翻译阶段报错
    return False


# 目录导入时没必要进去翻的目录（系统垃圾、工程目录、回收站）
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "recovery", "recycler",
    "node_modules", "__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode",
}
SKIP_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}


def scan_dir(root, recursive=True, limit=400):
    """把本地文件夹扫成一份可直接排队的清单。

    和上传那条路不同：这里拿到的是磁盘上的真实路径，几百个文件不用一个个
    搬进 uploads 再翻，也就能保留「源文件同目录」这种输出方式。
    只认能识别的格式，跳过 Office 锁文件、系统垃圾和 0 字节文件，
    并把各类跳过数量报回去，好让 UI 把话说清楚。
    """
    res = {"ok": False, "path": "", "root": "", "count": 0, "files": [],
           "unsupported": 0, "empty": 0, "truncated": False,
           "recursive": bool(recursive), "error": ""}
    if not root:
        res["error"] = "没有指定文件夹"
        return res
    p = Path(str(root)).expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass
    res["path"] = str(p)
    if not p.exists():
        res["error"] = "文件夹不存在，可能已被移动或删除"
        return res
    if not p.is_dir():
        res["error"] = "这是个文件，不是文件夹；单个文件请用「选择文件」"
        return res
    res["root"] = p.name or p.drive
    files = []
    stack = [(p, "")]
    while stack and not res["truncated"]:
        cur, rel = stack.pop()
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name.lower())
        except OSError:
            continue                      # 没权限的子目录跳过，不影响其余
        subdirs = []
        for e in entries:
            low = e.name.lower()
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if not recursive or low.startswith(".") or low in SKIP_DIRS:
                    continue
                subdirs.append((Path(e.path), (rel + "/" if rel else "") + e.name))
                continue
            if low.startswith("~$") or low.startswith("._") or low in SKIP_FILES:
                continue                  # Office 锁文件 / macOS 资源分叉 / 系统缩略图
            if not is_supported(Path(e.name)):
                res["unsupported"] += 1
                continue
            try:
                size = e.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            if size == 0:
                res["empty"] += 1
                continue
            if len(files) >= limit:
                res["truncated"] = True
                break
            files.append({
                "path": e.path,
                "name": e.name,
                "rel": (rel + "/" if rel else "") + e.name,
                "dir": rel,
                "size": size,
                "ext": Path(e.name).suffix.lower(),
            })
        stack.extend(reversed(subdirs))
    if stack:
        res["truncated"] = True
    res["files"] = files
    res["count"] = len(files)
    if not files:
        res["error"] = ("这个文件夹里没有能翻译的文件（支持 txt / md / docx / pdf / epub / srt / lrc）"
                        if not (res["unsupported"] or res["empty"])
                        else "没有可翻译的文件：只有不支持的格式或空文件")
        return res
    res["ok"] = True
    return res


def _output_text(path: Path) -> str:
    """读回产物里的文字，用于兜底判断是不是空白文件。"""
    try:
        ext = path.suffix.lower()
        if not path.exists():
            return ""
        if ext == ".docx" and _HAS_DOCX:
            doc = docx.Document(str(path))
            parts = [p.text for p in doc.paragraphs]
            for t in doc.tables:
                for row in t.rows:
                    for c in row.cells:
                        parts += [p.text for p in c.paragraphs]
            return "\n".join(parts)
        if ext == ".pdf":
            return "pdf"
        if ext == ".epub":
            return epub_text(path)
        return read_text_file(path)
    except Exception:
        return ""


def process(path: Path, settings, engine, report=None, cancel=None):
    h = HANDLERS.get(path.suffix.lower())
    if not h:
        raise ValueError(f"不支持的格式：{path.suffix}")
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.stat().st_size == 0:
        raise ValueError(f"《{path.name}》是空文件（0 字节），没有任何内容可翻译")

    report = report or (lambda **k: None)
    tr = Translator(engine, settings, report, cancel or (lambda: False))
    outs = h(path, settings, engine, report, cancel or (lambda: False), tr)

    if tr.canceled:
        # 用户中途点了停止：把已经写出来的部分保留下来，未翻到的段落是原文
        keep = [p for p in outs if p and Path(p).exists() and _output_text(p).strip()]
        if tr.done == 0:
            for p in keep:
                try:
                    p.unlink()
                except Exception:
                    pass
            raise Cancelled([])          # 一段都没翻完，不留半成品
        raise Cancelled(keep)

    # 兜底：产物里一个字都没有 → 删掉空白文件并报错，避免用户拿到空文档
    if outs and not any(_output_text(p).strip() for p in outs):
        for p in outs:
            try:
                p.unlink()
            except Exception:
                pass
        raise ValueError(f"《{path.name}》翻译结果为空（模型没返回内容），已删除空白文件。"
                         "请确认源文件有文字，或重试一次")
    return outs
