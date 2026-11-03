# -*- coding: utf-8 -*-
"""翻译引擎：托管 llama.cpp(llama-server) 子进程，封装混元 HY-MT1.5 官方提示词模板。

官方模板（HY-MT1.5 模型卡）：
  中外互译  将以下文本翻译为{目标语},注意只需要输出翻译后的结果,不要额外解释: {原文}
  外外互译  Translate the following segment into {target}, without additional explanation. {src}
  上下文    {context} 参考上面的信息,把下面的文本翻译成{目标语},注意不需要翻译上文,也不要额外解释: {原文}
  术语干预  参考下面的翻译: {src_term} 翻译成 {tgt_term} ...
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import config


class EngineError(RuntimeError):
    """引擎启动 / 推理过程中的可读错误，直接展示给用户。"""


# HY-MT1.5 官方支持语种（来源：模型卡 Supported languages 表）
# code: (中文全称, English 全称, 界面显示名)
LANGS = {
    "zh": ("中文", "Chinese", "中文"),
    "en": ("英语", "English", "English"),
    "ja": ("日语", "Japanese", "日本語"),
    "fr": ("法语", "French", "Français"),
    "pt": ("葡萄牙语", "Portuguese", "Português"),
    "es": ("西班牙语", "Spanish", "Español"),
    "tr": ("土耳其语", "Turkish", "Türkçe"),
    "ru": ("俄语", "Russian", "Русский"),
    "ar": ("阿拉伯语", "Arabic", "العربية"),
    "ko": ("韩语", "Korean", "한국어"),
    "th": ("泰语", "Thai", "ไทย"),
    "it": ("意大利语", "Italian", "Italiano"),
    "de": ("德语", "German", "Deutsch"),
    "vi": ("越南语", "Vietnamese", "Tiếng Việt"),
    "ms": ("马来语", "Malay", "Bahasa Melayu"),
    "id": ("印尼语", "Indonesian", "Bahasa Indonesia"),
    "tl": ("菲律宾语", "Filipino", "Filipino"),
    "hi": ("印地语", "Hindi", "हिन्दी"),
    "zh-Hant": ("繁体中文", "Traditional Chinese", "繁體中文"),
    "pl": ("波兰语", "Polish", "Polski"),
    "cs": ("捷克语", "Czech", "Čeština"),
    "nl": ("荷兰语", "Dutch", "Nederlands"),
    "km": ("高棉语", "Khmer", "ខ្មែរ"),
    "my": ("缅甸语", "Burmese", "မြန်မာ"),
    "fa": ("波斯语", "Persian", "فارسی"),
    "gu": ("古吉拉特语", "Gujarati", "ગુજરાતી"),
    "ur": ("乌尔都语", "Urdu", "اردو"),
    "te": ("泰卢固语", "Telugu", "తెలుగు"),
    "mr": ("马拉地语", "Marathi", "मराठी"),
    "he": ("希伯来语", "Hebrew", "עברית"),
    "bn": ("孟加拉语", "Bengali", "বাংলা"),
    "ta": ("泰米尔语", "Tamil", "தமிழ்"),
    "uk": ("乌克兰语", "Ukrainian", "Українська"),
    "bo": ("藏语", "Tibetan", "བོད་སྐད"),
    "kk": ("哈萨克语", "Kazakh", "Қазақша"),
    "mn": ("蒙古语", "Mongolian", "Монгол"),
    "ug": ("维吾尔语", "Uyghur", "ئۇيغۇرچە"),
    "yue": ("粤语", "Cantonese", "粵語"),
}
# 这些语种与中文互译时用中文指令模板更自然
CN_FAMILY = {"zh", "zh-Hant", "yue", "bo", "ug", "kk", "mn"}

CN_NAME = {k: v[0] for k, v in LANGS.items()}
EN_NAME = {k: v[1] for k, v in LANGS.items()}
LANG_LABEL = {k: v[2] for k, v in LANGS.items()}

BOS = "<|startoftext|>"
EOS_TOKENS = ["<|eos|>", "<|extra_0|>", "<|startoftext|>", "<|extra_4|>"]

# 本地回环请求必须绕过系统代理（有些机器环境里带 http_proxy，会把 127.0.0.1 也代理走）
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

KANA = re.compile(r"[\u3040-\u30ff]")
CJK = re.compile(r"[\u4e00-\u9fff]")

# 按书写系统识别（互不重叠，命中比例最高者胜出）
_SCRIPTS = [
    ("ja", re.compile(r"[\u3040-\u30ff]")),
    ("ko", re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")),
    ("ru", re.compile(r"[\u0400-\u04ff]")),
    ("ar", re.compile(r"[\u0600-\u06ff]")),
    ("he", re.compile(r"[\u0590-\u05ff]")),
    ("th", re.compile(r"[\u0e00-\u0e7f]")),
    ("hi", re.compile(r"[\u0900-\u097f]")),      # 印地语 / 马拉地语
    ("bn", re.compile(r"[\u0980-\u09ff]")),
    ("gu", re.compile(r"[\u0a80-\u0aff]")),
    ("ta", re.compile(r"[\u0b80-\u0bff]")),
    ("te", re.compile(r"[\u0c00-\u0c7f]")),
    ("bo", re.compile(r"[\u0f00-\u0fff]")),
    ("my", re.compile(r"[\u1000-\u109f]")),
    ("km", re.compile(r"[\u1780-\u17ff]")),
]

# 拉丁字母语言：高频功能词 + 独有变音符号（浅层识别足够用于选模板）
_LATIN_STOP = {
    "fr": set("le la les des est une pour dans que qui pas avec sur aux nous vous sont plus cette".split()),
    "es": set("el la los las que una para por con del son como muy pero este esta cuando".split()),
    "pt": set("os as que em uma para por com não são como mais mas muito este esta quando".split()),
    "it": set("il la gli che una per con non si del della sono come più anche questo questa".split()),
    "de": set("der die das und ist nicht eine von mit auf für sich dem den werden auch wie".split()),
    "nl": set("het een van en is niet dat met voor zijn wordt ook maar om aan bij deze".split()),
    "tr": set("bir ve için değil bu ile olarak daha çok var sonra gibi ama kadar".split()),
    "pl": set("nie jest się które oraz przez dla tego jako także będzie".split()),
    "cs": set("není který jsou pro se že jako ale při už takto také bude".split()),
    "vi": set("và của không được trong những cho một các với này người khi để nhưng cũng".split()),
    "id": set("yang dan untuk dengan tidak ini itu dari dalam akan juga pada adalah saya kami kita".split()),
    "tl": set("ang mga hindi para ng ay na ako siya ito kung may dahil naman".split()),
    "en": set("the and of to is in that for with it this are be on as at by from not have you".split()),
}
# 判定顺序：英文放最后，平票时优先非英文（避免把其他语言吞成英语）
_LATIN_ORDER = ["vi", "tr", "pl", "cs", "de", "fr", "es", "pt", "it", "nl", "id", "tl", "en"]

# 只放「别的语言不会有」的字符，避免误判
_LATIN_MARKS = {
    "vi": "ăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ",
    "tr": "ğışİ",
    "pl": "ąćęłńśźż",
    "cs": "řůěň",
    "de": "ß",
    "es": "ñ¿¡",
    "pt": "ãõ",
    "fr": "œæ",
}

_WORD_RE = re.compile(r"[a-zà-öø-ÿğışçñā-ž']+")

# 繁体独有字形（简体不出现），用于区分 zh / zh-Hant
_TRAD_ONLY = set("們這個來說時國學對會後開樣點現麼為與將義應該產業務區見讓從處理點"
                 "爾華萬與東車馬長門問題經濟發實現聽讀寫語體變")


def _latin_scores(low: str):
    """按 IDF 加权打分：越「独家」的功能词权重越高。"""
    toks = set(_WORD_RE.findall(low))
    scores = {}
    for code in _LATIN_ORDER:
        words = _LATIN_STOP.get(code, set())
        s = 0.0
        for t in toks:
            if t in words:
                df = sum(1 for v in _LATIN_STOP.values() if t in v)
                s += 1.0 / (df ** 0.5)      # 越独家权重越高，但不至于把短句全判 0
        marks = _LATIN_MARKS.get(code, "")
        if marks:
            hit = sum(1 for c in marks if c in low)
            if hit:
                # 越南语声调符号密集且独家，权重最高
                s += 2.0 if code == "vi" else min(1.5, 1.0 + 0.25 * (hit - 1))
        scores[code] = s
    return scores


def detect_lang(text: str) -> str:
    """语种识别：书写系统优先，拉丁语系用 IDF 加权功能词兜底，识别不出按英语处理。"""
    sample = text[:4000]
    if not sample.strip():
        return "en"
    total = max(1, len(re.sub(r"\s", "", sample)))

    best, best_n = None, 0
    for code, pat in _SCRIPTS:
        n = len(pat.findall(sample))
        if n > best_n:
            best, best_n = code, n
    if best_n >= max(3, total * 0.04):
        if best == "ru" and re.search(r"[іїєґІЇЄҐ]", sample):
            return "uk"                       # 带 і/ї/є/ґ 的西里尔文即乌克兰语
        if best == "hi" and re.search(r"[ळ]", sample):
            return "mr"                       # 带 ळ 的天城文按马拉地语
        return best

    cjk = len(CJK.findall(sample))
    if cjk and cjk / total > 0.15:
        trad = sum(1 for ch in sample if ch in _TRAD_ONLY)
        if trad >= 2 and trad / cjk >= 0.02:
            return "zh-Hant"
        return "zh"                            # 简体 / 无法区分时按中文

    low = sample.lower()
    scores = _latin_scores(low)
    pick = max(_LATIN_ORDER, key=lambda c: scores.get(c, 0.0))
    return pick if scores.get(pick, 0.0) >= 1.0 else "en"


# ---------------------------------------------------------------- 文本切块
_SENT_END = "。！？；…!?;\n"


def split_long(text: str, limit: int):
    """把超长段落按句子切到 limit 字符以内；不丢字符、不改顺序。"""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in _SENT_END and len(buf) >= limit * 0.55:
            out.append(buf.strip())
            buf = ""
        elif len(buf) >= limit * 1.35:
            # 句子过长：优先在空格/逗号处断开
            cut = max(buf.rfind("，"), buf.rfind(","), buf.rfind(" "), buf.rfind("、"))
            if cut > limit * 0.4:
                out.append(buf[: cut + 1].strip())
                buf = buf[cut + 1 :]
            else:
                out.append(buf.strip())
                buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [x for x in out if x]


def chunk_paragraphs(texts, limit: int):
    """返回 [(text, [原段落索引...]), ...]，把碎片拼成不超过 limit 的块。"""
    chunks, cur, idxs = [], [], []
    for i, t in enumerate(texts):
        t = (t or "").strip()
        if not t:
            continue
        for piece in split_long(t, limit):
            if cur and sum(len(c) for c in cur) + len(piece) > limit:
                chunks.append(("\n".join(cur), idxs))
                cur, idxs = [], []
            cur.append(piece)
            idxs.append(i)
    if cur:
        chunks.append(("\n".join(cur), idxs))
    return chunks


# ---------------------------------------------------------------- 提示词
def build_head(src: str, tgt: str, glossary=None, context=None) -> str:
    # 只要有一侧属于中文语族，就用中文指令模板（官方「中外互译」模板）
    use_cn = (tgt in CN_FAMILY) or (src in CN_FAMILY)
    tgt_cn = CN_NAME.get(tgt, tgt)
    tgt_en = EN_NAME.get(tgt, tgt)
    if use_cn:
        head = f"将以下文本翻译为{tgt_cn},注意只需要输出翻译后的结果,不要额外解释:"
    else:
        head = f"Translate the following segment into {tgt_en}, without additional explanation."

    if context:
        ctx = context.strip()
        if len(ctx) > 1200:
            ctx = ctx[-1200:]
        if use_cn:
            head = (f"{ctx}\n参考上面的信息,把下面的文本翻译成{tgt_cn},"
                    f"注意不需要翻译上文,也不要额外解释:")
        else:
            head = (f"{ctx}\nReferring to the information above, translate the following text into "
                    f"{tgt_en}, without additional explanation:")

    if glossary:
        lines = "\n".join(f"{a} 翻译成 {b}" for a, b in glossary[:120] if a and b)
        if lines:
            head = f"参考下面的翻译:\n{lines}\n{head}"
    return head


def build_prompt(text: str, src: str, tgt: str, glossary=None, context=None) -> str:
    return build_head(src, tgt, glossary, context) + "\n\n" + text


def clean_output(text: str, prompt_head: str = "") -> str:
    if not text:
        return ""
    for tok in EOS_TOKENS:
        text = text.replace(tok, "")
    text = text.strip()
    # 去掉模型偶发的引导语 / 引号包裹
    text = re.sub(r"^(译文|翻译|翻译结果|Translation|Translated text)\s*[:：]\s*", "", text, flags=re.I)
    if len(text) > 1 and text[0] in "\"“'「『" and text[-1] in "\"”'」』":
        text = text[1:-1].strip()
    if prompt_head and text.startswith(prompt_head):
        text = text[len(prompt_head):].strip()
    return text.strip()


# ---------------------------------------------------------------- 引擎
class Engine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.proc = None
        self._fallback = None          # CUDA 加载失败时自动改用 vulkan
        self.port = int(cfg.get("server_port", 18080))
        self.base = f"http://127.0.0.1:{self.port}"
        self.state = "stopped"          # stopped | loading | ready | error
        self.message = ""
        self.started_at = 0.0
        self.last_tps = 0.0
        self.log = deque(maxlen=400)
        self._lock = threading.RLock()
        self._logfile = config.LOGS / "llama-server.log"
        self._marker = config.LOGS / "engine_backend.txt"
        self._gpu_cache = (0.0, {})     # (时间戳, 采样结果)，避免每次请求都拉起 nvidia-smi

    # ---------- 后端 ----------
    def backend(self) -> str:
        """实际使用的后端：cuda / vulkan / cpu（CUDA 启动失败时会自动回落到 vulkan）。"""
        fb = getattr(self, "_fallback", None)
        d = config.runtime_dir(fb or str(self.cfg.get("backend", "auto")))
        return "cuda" if d == config.RUNTIME_CUDA else ("vulkan" if (d / "ggml-vulkan.dll").exists() else "cpu")

    def runtime_dir(self) -> Path:
        fb = getattr(self, "_fallback", None)
        return config.runtime_dir(fb or str(self.cfg.get("backend", "auto")))

    # ---------- 日志 ----------
    def _say(self, msg: str):
        line = time.strftime("%H:%M:%S ") + msg
        self.log.append(line)

    def tail(self, n=40):
        return list(self.log)[-n:]

    # ---------- 生命周期 ----------
    def exe(self) -> Path:
        d = self.runtime_dir()
        for name in ("llama-server.exe", "llama-server"):
            p = d / name
            if p.exists():
                return p
        raise EngineError(f"未找到 llama-server.exe，请检查 {d} 目录")

    def model_path(self) -> Path:
        p = Path(self.cfg.get("model_path", ""))
        if not p.exists():
            raise EngineError(f"模型文件不存在：{p}")
        return p

    def alive(self) -> bool:
        try:
            with LOCAL_OPENER.open(self.base + "/health", timeout=2) as r:
                data = json.loads(r.read().decode("utf-8", "ignore") or "{}")
            return data.get("status") in ("ok", "loading")
        except Exception:
            return False

    def _build_args(self, exe, model, safe=False):
        ctx = int(self.cfg.get("ctx", 8192))
        args = [
            str(exe), "-m", str(model),
            "--host", "127.0.0.1", "--port", str(self.port),
            "-c", str(ctx), "-ngl", str(int(self.cfg.get("ngl", 99))),
            "-t", str(int(self.cfg.get("threads", 8))),
            "--jinja", "-np", "1", "--no-warmup",
            "-b", "1024", "-ub", "512",
        ]
        if not safe:
            # Vulkan 后端才需要挑显卡：避免把层分到 AMD/Intel 核显，速度会掉一个数量级
            if self.backend() == "vulkan" and (self.runtime_dir() / "ggml-vulkan.dll").exists():
                args += ["-dev", "Vulkan0", "-mg", "0"]
            args += ["-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0"]
        return args

    def _port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.25):
                return True
        except Exception:
            return False

    def _kill_foreign(self):
        """杀掉占用本端口的残留 llama-server（例如上次以别的后端启动的）。"""
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                 timeout=10, creationflags=0x08000000 if os.name == "nt" else 0).stdout
        except Exception:
            out = ""
        pids = set()
        for line in out.splitlines():
            if "LISTENING" in line and f":{self.port} " in line + " ":
                tok = line.split()
                if tok and tok[-1].isdigit():
                    pids.add(tok[-1])
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=10,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            except Exception:
                pass
        if pids:
            self._say(f"已结束残留推理服务进程 {', '.join(sorted(pids))}")
            time.sleep(1.5)

    def _adopt(self) -> bool:
        """上一次 UI 退出时 llama-server 可能还活着：直接接管，避免二次占用端口。"""
        if self.proc and self.proc.poll() is None:
            return False
        if not self.alive():
            return False
        # 后端被切换过（例如 Vulkan → CUDA）时不能沿用旧进程，否则白改设置
        want = self.backend()
        try:
            lines = self._marker.read_text(encoding="utf-8").strip().splitlines()
            have = lines[0].strip() if lines else ""
        except Exception:
            have = ""
        if have and have != want:
            self._say(f"运行中的服务是 {have} 后端，与当前设置 {want} 不一致，重启推理服务")
            self._kill_foreign()
            return False
        self.state, self.message = "ready", f"已接管运行中的推理服务（{have or want}）"
        self._say("检测到已在运行的 llama-server，直接复用（无需重新加载模型）")
        return True

    def start(self, wait=True):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                if self.state == "ready" and self.alive():
                    return
                if wait:
                    self._wait_ready()
                return
            if self._adopt():
                return
            exe, model = self.exe(), self.model_path()
            self.state, self.message = "loading", "正在加载模型到显存…"
            args = self._build_args(exe, model)
            self._say(f"启动推理服务（{self.backend()}）：" + " ".join(args[3:]))
            try:
                self._spawn(args)
                if wait:
                    self._wait_ready(timeout=240)
                return
            except EngineError as e:
                last = str(e)
                self._kill()
                # 1) 某些参数不被当前运行时支持（换机器/换版本）→ 退到最保守的启动方式
                try:
                    self._say("首次启动失败，改用兼容参数重试")
                    args = self._build_args(exe, model, safe=True)
                    self._spawn(args)
                    if wait:
                        self._wait_ready(timeout=240)
                    return
                except EngineError as e2:
                    last = str(e2)
                    self._kill()
                # 2) CUDA 吃显存更多；其它程序占用过多时自动回落 Vulkan，保证能用
                if (self.backend() == "cuda" and str(self.cfg.get("backend", "auto")) == "auto"
                        and (config.RUNTIME / "llama-server.exe").exists()):
                    self._say("CUDA 启动失败（可能显存不足），自动回落到 Vulkan 后端")
                    self._fallback = "vulkan"
                    exe = self.exe()
                    self.state, self.message = "loading", "CUDA 不可用，正在用 Vulkan 重新加载…"
                    args = self._build_args(exe, model)
                    self._spawn(args)
                    if wait:
                        self._wait_ready(timeout=240)
                    return
                raise EngineError(last)

    def _spawn(self, args):
        creation = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
        logf = open(self._logfile, "w", encoding="utf-8", errors="ignore")
        self.proc = subprocess.Popen(
            args, cwd=str(self.runtime_dir()), stdout=logf, stderr=subprocess.STDOUT,
            creationflags=creation, env={**os.environ, "LLAMA_LOG_LEVEL": "32"},
        )
        self.started_at = time.time()
        try:
            self._marker.write_text(f"{self.backend()}\n{self.port}\n", encoding="utf-8")
        except Exception:
            pass

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=10)
            except Exception:
                pass
        self.proc = None

    def boot(self):
        """异步加载：立即返回，状态由后台线程推进（供 UI 显示进度）。"""
        if self.state == "ready" and self.alive():
            return
        if getattr(self, "_booting", False):
            return
        self._booting = True

        def _run():
            try:
                self.start(wait=True)
            except Exception as e:
                self.state = "error"
                self.message = str(e)
            finally:
                self._booting = False

        threading.Thread(target=_run, daemon=True).start()

    def _wait_ready(self, timeout=300):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc and self.proc.poll() is not None:
                self.state = "error"
                self.message = "推理服务启动失败：" + self._last_log_line()
                self._say(self.message)
                raise EngineError(self.message)
            try:
                with LOCAL_OPENER.open(self.base + "/health", timeout=2) as r:
                    if json.loads(r.read().decode("utf-8", "ignore") or "{}").get("status") == "ok":
                        self.state, self.message = "ready", "就绪"
                        self._say(f"模型就绪，用时 {time.time() - t0:.1f}s")
                        return True
            except Exception:
                pass
            time.sleep(0.8)
        self.state = "error"
        self.message = "模型加载超时（可能是显存不足），可尝试把「上下文」调小到 4096"
        raise EngineError(self.message)

    def _last_log_line(self) -> str:
        try:
            txt = self._logfile.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
            return " | ".join(txt[-3:])[:300]
        except Exception:
            return "未知错误"

    def stop(self):
        with self._lock:
            self._kill()
            self.state, self.message = "stopped", "已停止"
            self._say("推理服务已停止")

    # ---------- HTTP ----------
    def _post(self, path: str, payload: dict, timeout=600) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with LOCAL_OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))

    def _chat(self, prompt: str, max_tokens: int, temperature: float):
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "top_p": float(self.cfg.get("top_p", 0.6)),
            "max_tokens": max_tokens,
            "repeat_penalty": float(self.cfg.get("repeat_penalty", 1.05)),
            "stream": False,
            "cache_prompt": True,
        }
        r = self._post("/v1/chat/completions", body)
        ch = (r.get("choices") or [{}])[0]
        txt = (ch.get("message") or {}).get("content") or ""
        usage = r.get("usage") or {}
        return txt, usage.get("completion_tokens", 0)

    def _raw(self, prompt: str, max_tokens: int, temperature: float):
        body = {
            "prompt": BOS + prompt + "<|extra_0|>",
            "temperature": temperature,
            "top_p": float(self.cfg.get("top_p", 0.6)),
            "n_predict": max_tokens,
            "repeat_penalty": float(self.cfg.get("repeat_penalty", 1.05)),
            "stop": EOS_TOKENS,
            "cache_prompt": True,
            "add_special": False,
        }
        r = self._post("/completion", body)
        return r.get("content", "") or "", (r.get("tokens_predicted") or 0)

    # ---------- 翻译 ----------
    def translate(self, text: str, src="auto", tgt="zh", glossary=None,
                  context=None, cancel=None, retry=True) -> dict:
        if self.state != "ready":
            self.start()
        text = text.strip()
        if not text:
            return {"text": "", "tokens": 0, "seconds": 0.0}
        if src in (None, "auto"):
            src = detect_lang(text)
        if src == tgt:
            return {"text": text, "tokens": 0, "seconds": 0.0, "same": True}

        prompt = build_prompt(text, src, tgt, glossary, context)
        head = build_head(src, tgt, glossary, context)
        budget = max(256, min(4096, int(len(text) * 2.2) + 96))
        t0 = time.time()
        used_raw = False
        try:
            out, toks = self._chat(prompt, budget, float(self.cfg.get("temperature", 0.1)))
        except urllib.error.URLError as e:
            raise EngineError(f"请求推理服务失败：{e}")
        cleaned = clean_output(out, head)
        # 聊天模板输出异常时回退到原始补全
        if not cleaned or "extra_0" in out or "startoftext" in out:
            used_raw = True
            out, toks = self._raw(prompt, budget, float(self.cfg.get("temperature", 0.1)))
            cleaned = clean_output(out, "") or clean_output(out, head)

        # 疑似漏译（输出与原文几乎一致）→ 关闭上下文重试一次
        if retry and cleaned and cleaned.strip() == text.strip() and len(text) > 30:
            p2 = build_prompt(text, src, tgt, glossary, None)
            out2, toks2 = self._chat(p2, budget, 0.0)
            c2 = clean_output(out2, "")
            if c2 and c2.strip() != text.strip():
                cleaned, toks = c2, toks + toks2

        secs = max(0.001, time.time() - t0)
        if toks:
            self.last_tps = toks / secs
        return {"text": cleaned.strip(), "tokens": toks, "seconds": secs, "raw": used_raw}

    def translate_stream(self, text: str, src="auto", tgt="zh"):
        """试译框用：流式产出 token 片段。"""
        if self.state != "ready":
            self.start()
        if src in (None, "auto"):
            src = detect_lang(text)
        prompt = build_prompt(text, src, tgt, None, None)
        body = json.dumps({
            "prompt": BOS + prompt + "<|extra_0|>",
            "temperature": float(self.cfg.get("temperature", 0.1)),
            "n_predict": 1024, "stop": EOS_TOKENS, "stream": True, "add_special": False,
        }).encode("utf-8")
        req = urllib.request.Request(self.base + "/completion", data=body,
                                     headers={"Content-Type": "application/json"})
        with LOCAL_OPENER.open(req, timeout=600) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload in ("[DONE]", ""):
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                piece = obj.get("content") or ""
                if piece:
                    yield piece

    # ---------- 状态 ----------
    GPU_FIELDS = ("memory.used,memory.total,utilization.gpu,power.draw,power.limit,"
                  "clocks.current.graphics,temperature.gpu")

    @staticmethod
    def _num(raw):
        """nvidia-smi 在驱动不支持某些项时会返回 [N/A] / Not Supported。"""
        try:
            return float(str(raw).strip())
        except Exception:
            return None

    def gpu_info(self, fresh=False):
        """显存 / 负载 / 功耗 / 频率 / 温度，1.2 秒内复用同一份采样。"""
        now = time.time()
        ts, cached = self._gpu_cache
        if cached and not fresh and now - ts < 1.2:
            return dict(cached)
        info = {"vram_used": None, "vram_total": None, "gpu_util": None,
                "gpu_power": None, "gpu_power_limit": None,
                "gpu_clock": None, "gpu_temp": None}
        try:
            out = subprocess.run(
                ["nvidia-smi", f"--query-gpu={self.GPU_FIELDS}",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=6,
                creationflags=0x08000000 if os.name == "nt" else 0)
            vals = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
            if len(vals) >= 7:
                mem, tot, util, pw, pwl, clk, temp = vals[:7]
                info.update({
                    "vram_used": self._num(mem), "vram_total": self._num(tot),
                    "gpu_util": self._num(util), "gpu_power": self._num(pw),
                    "gpu_power_limit": self._num(pwl), "gpu_clock": self._num(clk),
                    "gpu_temp": self._num(temp),
                })
            for k in ("vram_used", "vram_total", "gpu_util"):
                if info[k] is not None:
                    info[k] = int(info[k])
        except Exception:
            pass
        self._gpu_cache = (now, dict(info))
        return info

    def active_backend(self) -> str:
        """正在运行的推理服务实际用的后端（以启动时写的标记为准，避免显示与事实不符）。"""
        if self.state == "ready":
            try:
                lines = self._marker.read_text(encoding="utf-8").strip().splitlines()
                if lines and lines[0].strip():
                    return lines[0].strip()
            except Exception:
                pass
        return self.backend()

    def status(self):
        # UI 重启后旧进程还在时自动接管，界面上直接显示「已就绪」
        if self.state == "stopped" and not (self.proc and self.proc.poll() is None):
            if self._port_open():
                self._adopt()
        info = {
            "state": self.state,
            "message": self.message,
            "model": Path(self.cfg.get("model_path", "")).name,
            "ctx": self.cfg.get("ctx"),
            "ngl": self.cfg.get("ngl"),
            "backend": self.active_backend(),
            "port": self.port,
            "last_tps": round(self.last_tps, 1),
            "uptime": round(time.time() - self.started_at) if self.started_at and self.state == "ready" else 0,
            "log": list(self.log)[-12:],
        }
        info.update(self.gpu_info())
        return info


    def apply_config(self, cfg: dict):
        """配置热更新：后端被切换（cuda ↔ vulkan）时先停掉旧进程，下次启动走新后端。"""
        old_dir, old_fb = self.runtime_dir(), self._fallback
        self.cfg = cfg
        self._fallback = None
        if self.runtime_dir() != old_dir or old_fb:
            if self.proc and self.proc.poll() is None:
                self._say("后端已切换，停止旧的推理服务以便按新后端重新加载")
                self._kill()
                self.state, self.message = "stopped", "后端已切换，将按新设置重新加载"


_engine = None
_engine_lock = threading.Lock()


def get_engine(cfg=None) -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine(cfg or config.load())
        elif cfg:
            _engine.apply_config(cfg)
        return _engine
