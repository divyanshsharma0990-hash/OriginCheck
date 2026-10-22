"""Checking engine: reads .docx, searches free open sources, runs a local AI detector."""
import os
import re
import time
import requests
from docx import Document

MIN_WORDS, SIM_T, AI_T, DELAY = 25, 0.20, 0.50, 0.6
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


def parse_docx(f):
    doc = Document(f)
    cp = doc.core_properties
    paras, ex, in_refs = [], {"Quotes": 0, "References": 0, "Headings": 0, "Short paragraphs": 0}, False
    for p in doc.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        n = len(words(t))
        if in_refs:
            ex["References"] += n
            continue
        if t.lower().strip(" :.") in REF_HEADS:
            in_refs = True
            continue
        st = (p.style.name or "").lower() if p.style is not None else ""
        if st.startswith("heading") or st == "title":
            ex["Headings"] += n
            continue
        if n < MIN_WORDS:
            ex["Short paragraphs"] += n
            continue
        clean = strip_quotes(t)
        nc = len(words(clean))
        ex["Quotes"] += n - nc
        paras.append({"text": t, "clean": clean, "n": n, "nc": nc, "sim": None, "src": None, "ai": None, "sid": None})
    return {"paras": paras, "excluded": ex, "title": cp.title or "", "author": cp.author or ""}


# ---- free sources ----
def _get(url, params=None, headers=None):
    key = (url, tuple(sorted((params or {}).items())))
    if key not in _cache:
        time.sleep(DELAY)
        try:
            r = requests.get(url, params=params, headers=headers, timeout=25)
            _cache[key] = r.json() if r.status_code == 200 else None
        except Exception:
            _cache[key] = None
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


def check_para(clean, k):
    sents = sorted([s for s in re.split(r"(?<=[.!?])\s+", clean) if len(s.split()) >= 8],
                   key=lambda s: -len(s.split()))[:k]
    best, src = 0.0, None
    for q in sents:
        q = " ".join(q.split()[:25])
        for fn in (s_openalex, s_crossref, s_core):
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
    s = {"total_words": tot, "checked_words": tot}
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
