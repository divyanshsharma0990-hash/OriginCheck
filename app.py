"""OrigynAI - plagiarism + AI content checker (Streamlit)."""
import os
import hashlib
import pandas as pd
import streamlit as st

import auth
import engine
import report

st.set_page_config(page_title="OrigynAI ", page_icon="🛡️", layout="wide")
for k in ("CORE_API_KEY", "CONTACT_EMAIL"):
    try:
        os.environ.setdefault(k, st.secrets[k])
    except Exception:
        pass

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stApp {font-family:'Inter',sans-serif; font-size:16px;}
.banner {background:linear-gradient(120deg,#0B2A4A,#0E7C86); color:#fff; padding:22px 28px; border-radius:14px; margin-bottom:18px;}
.banner h1 {margin:0; font-size:28px; font-weight:700; color:#fff;}
.banner p {margin:4px 0 0; opacity:.9; font-size:15px;}
div[data-testid="stMetric"] {background:#fff; border:1px solid #E1E6EB; border-radius:12px; padding:14px 18px;}
.stButton>button, .stDownloadButton>button {background:#0E7C86; color:#fff; border:0; border-radius:10px; font-weight:600; padding:.55rem 1.2rem;}
.stButton>button:hover, .stDownloadButton>button:hover {background:#0B2A4A; color:#fff;}
.stDownloadButton>button:disabled {background:#C9D1D8; color:#fff;}
section[data-testid="stSidebar"] {background:#0B2A4A;}
section[data-testid="stSidebar"] * {color:#fff !important;}
.note {font-size:13px; color:#5B6770;}
</style>""", unsafe_allow_html=True)

st.markdown('<div class="banner"><h1>🛡️ OrigynAI</h1><p>Plagiarism and AI content checker for theses and research papers</p></div>',
            unsafe_allow_html=True)

ss = st.session_state
ss.setdefault("user", None)
ss.setdefault("fails", 0)

# ---------------- login ----------------
if not ss.user:
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        with st.form("login"):
            st.subheader("Sign in")
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            go = st.form_submit_button("Sign in")
        if go:
            if ss.fails >= 5:
                st.error("Too many failed attempts. Please refresh the page and try again later.")
            else:
                ok, name, left = auth.login(u, p)
                if ok:
                    ss.user, ss.days_left, ss.fails = name, left, 0
                    st.rerun()
                ss.fails += 1
                st.error("Invalid username or password, or the account has expired.")
        with st.expander("Need access? Request a 30-day account"):
            want = st.text_input("Choose a username", key="want")
            if want.strip():
                st.markdown(f"[✉️ Open the pre-filled email]({auth.access_request_link(want.strip())})")
                st.caption("Send the email. You will receive your password from the administrator.")
    st.stop()

# ---------------- sidebar ----------------
with st.sidebar:
    st.markdown(f"### 👤 {ss.user}")
    if ss.days_left:
        st.caption(f"Account valid for {ss.days_left} more day(s)")
    if st.button("Sign out"):
        ss.clear()
        st.rerun()
    st.markdown("---")
    st.caption("Similarity is measured against open scholarly sources only. AI detection is an estimate. "
               "Use both as signals for review.")

# ---------------- upload + options ----------------
tab_f, tab_t = st.tabs(["Upload file", "Paste text"])
with tab_f:
    f = st.file_uploader("Upload a Word document or text file (.docx or .txt, up to 100 MB)", type=["docx", "txt"])
with tab_t:
    pasted = st.text_area("Paste your blog post or article", height=220)

if f:
    src_name, fid = f.name, f"{f.name}-{f.size}"
elif pasted.strip():
    src_name, fid = "Pasted text", "p-" + hashlib.md5(pasted.encode()).hexdigest()
else:
    st.info("Upload a file or paste your text to begin.")
    st.stop()

if ss.get("fid") != fid:
    with st.spinner("Reading text..."):
        if f and f.name.lower().endswith(".docx"):
            ss.doc = engine.parse_docx(f)
        else:
            ss.doc = engine.parse_text(f.getvalue().decode("utf-8", "ignore") if f else pasted)
        ss.fid, ss.res = fid, None
doc = ss.doc
if not doc["paras"]:
    st.error("Not enough checkable text found (headings, references and tiny fragments are skipped). Try a longer text.")
    st.stop()

c1, c2 = st.columns(2)
author = c1.text_input("Author name", value=doc["author"], key=f"a_{fid}")
title = c2.text_input("Document title", value=doc["title"] or os.path.splitext(src_name)[0], key=f"t_{fid}")
o1, o2, o3 = st.columns(3)
do_plag = o1.checkbox("Plagiarism check", True)
do_ai = o2.checkbox("AI content check", True)
mode = o3.radio("Scan mode", ["Quick", "Deep"], horizontal=True)
words_n = sum(p["n"] for p in doc["paras"])
st.markdown(f'<p class="note">{len(doc["paras"])} paragraphs ({words_n:,} words) will be checked. '
            f'Quick is faster; Deep searches more sentences per paragraph.</p>', unsafe_allow_html=True)

if st.button("Run check", disabled=not (do_plag or do_ai)):
    bar = st.progress(0.0, text="Starting...")
    try:
        summ = engine.run(doc, do_plag, do_ai, mode, lambda x, msg: bar.progress(min(x, 1.0), text=msg))
        bar.progress(0.97, text="Building reports...")
        m = {"author": author, "title": title, "user": ss.user, "mode": mode, "core": bool(os.getenv("CORE_API_KEY")), "web": engine.web_available()}
        pdfs = {}
        if do_plag and do_ai:
            pdfs["combined"] = report.build_pdf("combined", doc, summ, m)
        if do_plag:
            pdfs["plag"] = report.build_pdf("plag", doc, summ, m)
        if do_ai:
            pdfs["ai"] = report.build_pdf("ai", doc, summ, m)
        ss.res = {"summ": summ, "pdfs": pdfs}
        bar.progress(1.0, text="Finished")
    except Exception as e:
        bar.empty()
        st.error(f"Something went wrong: {e}")

# ---------------- results ----------------
res = ss.get("res")
if res:
    s, pdfs = res["summ"], res["pdfs"]
    st.subheader("Results")
    if s["searches"] and s["search_errors"] / s["searches"] > 0.5:
        st.warning("Many online searches failed (network or rate limit), so similarity may be understated. Try again later.")
    cols = st.columns(4)
    if "plag" in s:
        cols[0].metric("Similarity", f"{s['plag']}%", f"Grade {report.grade(s['plag'])[0]}", delta_color="off")
        cols[1].metric("Matched sources", len(s["sources"]))
    if "ai" in s:
        cols[2].metric("Likely AI-written", f"{s['ai']}%", f"Grade {report.grade(s['ai'])[0]}", delta_color="off")
        cols[3].metric("Flagged paragraphs", sum(1 for p in doc["paras"] if p["ai"] >= 0.5))

    st.subheader("Download reports (PDF)")
    d1, d2, d3 = st.columns(3)
    d1.download_button("📄 Combined report", pdfs.get("combined", b""), "OriginCheck_Combined_Report.pdf",
                       "application/pdf", disabled="combined" not in pdfs, use_container_width=True)
    d2.download_button("🔍 Plagiarism report", pdfs.get("plag", b""), "OriginCheck_Plagiarism_Report.pdf",
                       "application/pdf", disabled="plag" not in pdfs, use_container_width=True)
    d3.download_button("🤖 AI content report", pdfs.get("ai", b""), "OriginCheck_AI_Report.pdf",
                       "application/pdf", disabled="ai" not in pdfs, use_container_width=True)

    t1, t2 = st.tabs(["Matched sources", "Paragraph scores"])
    with t1:
        if "sources" in s and s["sources"]:
            st.dataframe(pd.DataFrame(s["sources"])[["id", "title", "provider", "pct", "url"]].rename(
                columns={"id": "#", "pct": "% of document"}), use_container_width=True, hide_index=True)
        else:
            st.write("No significant matches found." if "sources" in s else "Plagiarism check was not run.")
    with t2:
        st.dataframe(pd.DataFrame([{"Para": i, "Words": p["n"],
                                    "Similarity %": None if p["sim"] is None else round(p["sim"] * 100),
                                    "AI %": None if p["ai"] is None else round(p["ai"] * 100),
                                    "Starts with": p["text"][:90]} for i, p in enumerate(doc["paras"], 1)]),
                     use_container_width=True, hide_index=True)
