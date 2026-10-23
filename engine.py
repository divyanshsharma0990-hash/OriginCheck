"""Checking engine: reads .docx, searches free open sources, runs a local AI detector."""
import os
import re
import html
import time
import requests
from docx import Document

SIM_T, AI_T, DELAY = 0.20, 0.50, 0.6
TARGET, MIN_BLOCK = 60, 15   # short paragraphs are merged into blocks of about 60+ words
STATS = {"searches": 0, "errors": 0}
AI_MODEL = os.getenv("AI_MODEL", "Hello-SimpleAI/chatgpt-detector-roberta")
REF_HEADS = {"references", "bibliography", "works cited"}
_cache, _det = {}, None


def words(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def strip_quotes(t):
    return re.sub(r'["\u201c][^"\u201d]{15,}["\u201d]', " ", t)


def shingles(t, n=5):
    w = words(t)
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def containment(para, src):
    a = shingles(para)
    return len(a & shingles(src)) / len(a) if a else 0.0


def _items(doc):
    """Paragraphs in document order, including text inside tables."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    keep, seen = [], set()
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}p"):
            yield Paragraph(el, doc)
        elif el.tag.endswith("}tbl"):
            for row in Table(el, doc).rows:
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    keep.append(cell._tc)
                    seen.add(id(cell._tc))
                    for p in cell.paragraphs:
                        yield p


def _assemble(items, title="", author=""):
    """items: (text, is_styled_heading). Skips headings/references, merges short paragraphs."""
    ex = {"Quotes": 0, "References": 0, "Headings": 0, "Short paragraphs": 0}
    blocks, buf, in_refs = [], [], False

    def flush():
        if buf:
            blocks.append(" ".join(buf))
            buf.clear()

    for t, styled in items:
        t = t.strip()
        if not t:
            continue
        n = len(words(t))
        if in_refs:
            ex["References"] += n
            continue
        if t.lower().strip(" :.") in REF_HEADS:
            flush()
            in_refs = True
            continue
        if styled or (n <= 8 and not t.endswith((".", "!", "?", ":", ";", '"', "\u201d"))):
            ex["Headings"] += n
            flush()
            continue
        buf.append(t)
        if sum(len(words(x)) for x in buf) >= TARGET:
            flush()
    flush()
    merged = []
    for b in blocks:
        if len(words(b)) < MIN_BLOCK:
            if merged:
                merged[-1] += " " + b
            else:
                ex["Short paragraphs"] += len(words(b))
        else:
            merged.append(b)
    paras = []
    for t in merged:
        clean = strip_quotes(t)
        n, nc = len(words(t)), len(words(clean))
        ex["Quotes"] += n - nc
        paras.append({"text": t, "clean": clean, "n": n, "nc": nc, "sim": None, "src": None, "ai": None, "sid": None})
    return {"paras": paras, "excluded": ex, "title": title, "author": author}


def parse_docx(f):
    doc = Document(f)
    cp = doc.core_properties

    def styled(p):
        st = (p.style.name or "").lower() if p.style is not None else ""
        return st.startswith("heading") or st == "title"

    return _assemble(((p.text, styled(p)) for p in _items(doc)), cp.title or "", cp.author or "")


def parse_text(text):
    return _assemble(((ln, False) for ln in text.splitlines()))


# ---- free sources ----
def _get(url, params=None, headers=None):
    key = (url, tuple(sorted((params or {}).items())))
    if key not in _cache:
        time.sleep(DELAY)
        STATS["searches"] += 1
        try:
            r = requests.get(url, params=params, headers=headers, timeout=25)
            _cache[key] = r.json() if r.status_code == 200 else None
        except Exception:
            _cache[key] = None
        if _cache[key] is None:
            STATS["errors"] += 1
    return _cache[key]


def _abstract(inv):
    pos = {i: w for w, ix in (inv or {}).items() for i in ix}
    return " ".join(pos[i] for i in sorted(pos))


def s_openalex(q):
    pr = {"search": q, "per-page": 5}
    if os.getenv("CONTACT_EMAIL"):
        pr["mailto"] = os.getenv("CONTACT_EMAIL")
    out = []
    for w in (_get("https://api.openalex.org/works", pr) or {}).get("results", []):
        t = w.get("title") or "Untitled"
        out.append({"title": t, "url": w.get("doi") or w.get("id") or "", "provider": "OpenAlex",
                    "text": t + " " + _abstract(w.get("abstract_inverted_index"))})
    return out


def s_crossref(q):
    pr = {"query.bibliographic": q, "rows": 5}
    if os.getenv("CONTACT_EMAIL"):
        pr["mailto"] = os.getenv("CONTACT_EMAIL")
    out = []
    for w in ((_get("https://api.crossref.org/works", pr) or {}).get("message") or {}).get("items", []):
        t = (w.get("title") or ["Untitled"])[0]
        out.append({"title": t, "url": w.get("URL", ""), "provider": "Crossref",
                    "text": t + " " + re.sub(r"<[^>]+>", " ", w.get("abstract") or "")})
    return out


def s_core(q):
    key = os.getenv("CORE_API_KEY")
    if not key:
        return []
    out = []
    data = _get("https://api.core.ac.uk/v3/search/works", {"q": q, "limit": 5}, {"Authorization": "Bearer " + key})
    for w in (data or {}).get("results", []):
        t = w.get("title") or "Untitled"
        out.append({"title": t, "url": w.get("downloadUrl") or "", "provider": "CORE",
                    "text": t + " " + (w.get("abstract") or "") + " " + (w.get("fullText") or "")})
    return out


def _ddgs():
    try:
        from ddgs import DDGS
        return DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
            return DDGS
        except Exception:
            return None


def web_available():
    return _ddgs() is not None


def _page_text(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        raw = r.raw.read(1_500_000, decode_content=True).decode(r.encoding or "utf-8", "ignore")
    except Exception:
        return ""
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def s_web(q):
    D = _ddgs()
    if not D:
        return []
    if ("web", q) in _cache:
        return _cache[("web", q)]
    time.sleep(1.0)
    STATS["searches"] += 1
    out = []
    try:
        res = D().text(q, max_results=4) or []
    except Exception:
        STATS["errors"] += 1
        res = []
    for r in res[:3]:
        url = r.get("href", "")
        out.append({"title": r.get("title") or url, "url": url, "provider": "Web",
                    "text": (r.get("title") or "") + " " + (r.get("body") or "") + " " + _page_text(url)})
    _cache[("web", q)] = out
    return out


def check_para(clean, k):
    sents = sorted([s for s in re.split(r"(?<=[.!?])\s+", clean) if len(s.split()) >= 8],
                   key=lambda s: -len(s.split()))[:k]
    best, src = 0.0, None
    for q in sents:
        q = " ".join(q.split()[:25])
        for fn in (s_openalex, s_crossref, s_core, s_web):
            for c in fn(q):
                sc = containment(clean, c["text"])
                if sc > best:
                    best, src = sc, c
    return best, src


def detect_ai(text):
    global _det
    if _det is None:
        from transformers import pipeline
        _det = pipeline("text-classification", model=AI_MODEL)
    o = _det(text, truncation=True, max_length=512)[0]
    s = float(o["score"])
    return 1 - s if o["label"].lower() in ("human", "real", "label_0") else s


def run(doc, do_plag, do_ai, mode, progress):
    STATS.update(searches=0, errors=0)
    P, n = doc["paras"], max(1, len(doc["paras"]))
    k = 3 if mode == "Deep" else 1
    cut = 0.6 if do_plag and do_ai else 0.95
    if do_plag:
        for i, p in enumerate(P):
            progress(cut * i / n, f"Checking similarity: paragraph {i + 1} of {n}")
            p["sim"], p["src"] = check_para(p["clean"], k)
    if do_ai:
        base = cut if do_plag else 0.02
        for i, p in enumerate(P):
            progress(base + (0.95 - base) * i / n, f"Checking AI content: paragraph {i + 1} of {n}")
            p["ai"] = detect_ai(p["text"])
    return summarize(doc, do_plag, do_ai)


def summarize(doc, do_plag, do_ai):
    P = doc["paras"]
    tot = max(1, sum(p["n"] for p in P))
    totc = max(1, sum(p["nc"] for p in P))
    s = {"total_words": tot, "checked_words": tot, "searches": STATS["searches"], "search_errors": STATS["errors"]}
    if do_plag:
        srcs, mw = {}, 0.0
        for p in P:
            if p["src"] and p["sim"] >= SIM_T:
                m = p["nc"] * p["sim"]
                mw += m
                d = srcs.setdefault(p["src"]["title"], {"title": p["src"]["title"], "url": p["src"]["url"],
                                                         "provider": p["src"]["provider"], "words": 0.0})
                d["words"] += m
        ordered = sorted(srcs.values(), key=lambda d: -d["words"])
        for i, d in enumerate(ordered, 1):
            d["id"], d["pct"] = i, 100 * d["words"] / totc
        ids = {d["title"]: d["id"] for d in ordered}
        for p in P:
            p["sid"] = ids.get(p["src"]["title"]) if p["src"] and p["sim"] >= SIM_T else None
        by_prov = {}
        for d in ordered:
            by_prov[d["provider"]] = by_prov.get(d["provider"], 0) + d["pct"]
        s.update(plag=round(100 * mw / totc, 1), sources=ordered, by_provider=by_prov)
    if do_ai:
        hi = sum(p["n"] for p in P if p["ai"] >= 0.7)
        mid = sum(p["n"] for p in P if AI_T <= p["ai"] < 0.7)
        s.update(ai=round(100 * (hi + mid) / tot, 1),
                 ai_bins={"Likely AI (70%+)": 100 * hi / tot, "Possible AI (50-70%)": 100 * mid / tot,
                          "Likely human (<50%)": 100 * (tot - hi - mid) / tot})
    return s
