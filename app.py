"""OriginCheck - plagiarism + AI content checker (Streamlit)."""
import os
import hashlib
import pandas as pd
import streamlit as st

import auth
import engine
import report


def _to_docx_bytes(text):
    """Turn result text into a Word file (headings, bullets and paragraphs kept)."""
    import io
    import re
    from docx import Document
    doc = Document()
    for line in text.splitlines():
        t = line.rstrip()
        if not t.strip():
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", t)
        if m:
            doc.add_heading(m.group(2).strip(), level=len(m.group(1)))
        elif re.match(r"^\s*[-*\u2022]\s+", t):
            doc.add_paragraph(re.sub(r"^\s*[-*\u2022]\s+", "", t), style="List Bullet")
        elif re.match(r"^\s*\d+[.)]\s+", t):
            doc.add_paragraph(re.sub(r"^\s*\d+[.)]\s+", "", t), style="List Number")
        else:
            doc.add_paragraph(t.strip())
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

st.set_page_config(page_title="OriginCheck", page_icon="🛡️", layout="wide")
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

st.markdown('<div class="banner"><h1>🛡️ OriginCheck</h1><p>Plagiarism and AI content checker for theses, research papers, blogs and articles</p></div>',
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
    st.caption("Similarity is measured against open scholarly sources and top web results. AI detection is an estimate. "
               "Use both as signals for review.")

tab_check, tab_hum = st.tabs(["🔍 Plagiarism & AI check", "✍️ Humanizer & Paraphraser"])

# ---------------- AI humanizer and paraphraser ----------------
with tab_hum:
    st.write(
        "Upload a document or paste text, choose Humanizer or Paraphraser, then select Gemini or Groq. "
        "The selected provider receives your text only when you click the action button."
    )

    edit_mode = st.radio(
        "Choose a tool", ["AI Humanizer", "Paraphraser"], horizontal=True,
        key="content_edit_mode",
        help="Humanizer improves tone and readability. Paraphraser changes wording and sentence structure while preserving meaning.",
    )

    with st.expander("🔑 AI provider and API settings", expanded=False):
        provider = st.selectbox("AI provider", ["Gemini", "Groq"], key="rewrite_provider")
        if provider == "Gemini":
            default_model = "gemini-3.8-flash"
            api_key = st.text_input(
                "Gemini API key", value="", type="password", key="rewrite_gemini_key",
                placeholder="Leave empty to use the app's key (Admin accounts only)",
                help="Your own key from Google AI Studio. Permanent accounts can leave this empty."
            )
            model = st.text_input("Gemini model", value=default_model, key="rewrite_gemini_model")
            st.caption("Get a Gemini key from Google AI Studio. Usage is subject to Google's quota and pricing.")
        else:
            default_model = "openai/gpt-oss-120b"
            api_key = st.text_input(
                "Groq API key", value="", type="password", key="rewrite_groq_key",
                placeholder="Leave empty to use the app's key (Admin accounts only)",
                help="Your own key from the Groq Console. Permanent accounts can leave this empty."
            )
            model = st.text_input("Groq model", value=default_model, key="rewrite_groq_model")
            st.caption("Get a Groq key from the Groq Console. Model availability and usage limits can change.")
        st.caption("API keys are used for the request. Prefer Streamlit Secrets on a deployed app; never commit keys to source control.")

    src_choice = st.radio("Input source", ["Paste text", "Upload file"], horizontal=True, key="rewrite_src")
    file_text, human_input, src_stem = "", "", "content"
    if src_choice == "Upload file":
        uploaded_rewrite_file = st.file_uploader(
            "Upload content (.txt, .md, .docx, or text-based .pdf)",
            type=["txt", "md", "docx", "pdf"],
            key="rewrite_file_upload",
            help="For scanned/image-only PDFs, run OCR first. Maximum recommended input: 30,000 characters.",
        )
        if uploaded_rewrite_file is not None:
            try:
                file_bytes = uploaded_rewrite_file.getvalue()
                suffix = uploaded_rewrite_file.name.lower().rsplit(".", 1)[-1]
                if suffix in ("txt", "md"):
                    try:
                        file_text = file_bytes.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        file_text = file_bytes.decode("utf-8", errors="replace")
                elif suffix == "docx":
                    from docx import Document
                    from io import BytesIO
                    doc = Document(BytesIO(file_bytes))
                    chunks = [para.text for para in doc.paragraphs if para.text.strip()]
                    for table in doc.tables:
                        for row in table.rows:
                            chunks.append(" | ".join(cell.text.strip() for cell in row.cells))
                    file_text = "\n\n".join(chunks)
                else:
                    from pypdf import PdfReader
                    from io import BytesIO
                    pdf = PdfReader(BytesIO(file_bytes))
                    file_text = "\n\n".join(page.extract_text() or "" for page in pdf.pages)
                if not file_text.strip():
                    st.warning("No selectable text was found in that file. For a scanned PDF, use OCR or paste the text instead.")
                else:
                    st.success(f"Loaded {uploaded_rewrite_file.name} ({len(file_text):,} characters). Using the uploaded file.")
            except Exception as exc:
                file_text = ""
                st.error(f"Could not read this file: {exc}")
        else:
            file_text = ""

        if uploaded_rewrite_file is not None:
            src_stem = os.path.splitext(uploaded_rewrite_file.name)[0]
    else:
        human_input = st.text_area(
            "Paste content", height=200,
            placeholder="Paste your blog post, article, or draft here...",
            key="humanizer_input",
        )
    source_text = file_text.strip() if src_choice == "Upload file" else human_input.strip()
    h1, h2 = st.columns(2)
    human_tone = h1.selectbox(
        "Writing style",
        ["Natural and conversational", "Professional and polished", "Simple and easy to understand",
         "Academic and formal", "Friendly and engaging", "Concise and direct"],
        key="humanizer_tone",
    )
    keep_headings = h2.checkbox("Preserve existing headings", value=True, key="humanizer_headings")
    keep_facts = st.checkbox(
        "Preserve facts, figures, citations, and technical meaning", value=True, key="humanizer_facts"
    )
    action_label = "✨ Humanize content" if edit_mode == "AI Humanizer" else "🔁 Paraphrase content"
    action_key = "run_humanizer" if edit_mode == "AI Humanizer" else "run_paraphraser"
    if st.button(action_label, type="primary", key=action_key):
        if not source_text:
            st.warning("Upload a supported file or paste some content first.")
        elif not (api_key or "").strip() and ss.days_left is not None:
            st.warning("Please enter your own API key in the settings above to use this tool.")
        else:
            try:
                import humanizer
                with st.spinner(f"Processing with {provider}..."):
                    rewritten = humanizer.humanize_text(
                        source_text, tone=human_tone, preserve_headings=keep_headings,
                        preserve_facts=keep_facts,
                        task="paraphrase" if edit_mode == "Paraphraser" else "humanize",
                        provider=provider, api_key=api_key.strip() if api_key else None,
                        model=model.strip() if model else None,
                    )
                st.session_state["humanizer_result"] = rewritten
                st.session_state["humanizer_result_editor"] = rewritten
                st.session_state["humanizer_result_mode"] = edit_mode
                st.session_state["humanizer_result_stem"] = src_stem
                st.session_state["humanizer_result_fp"] = hashlib.md5((edit_mode + "||" + source_text).encode()).hexdigest()
                st.success("Paraphrase complete. Review facts and citations before use." if edit_mode == "Paraphraser" else "Rewrite complete. Review facts and citations before publishing.")
            except Exception as exc:
                st.error(str(exc))

    cur_fp = hashlib.md5((edit_mode + "||" + source_text).encode()).hexdigest()
    if st.session_state.get("humanizer_result") and st.session_state.get("humanizer_result_fp") == cur_fp:
        mode_done = st.session_state.get("humanizer_result_mode")
        st.markdown("#### " + ("Paraphrased content" if mode_done == "Paraphraser" else "Humanized content"))
        st.text_area("Review and edit the result", height=300, key="humanizer_result_editor")
        result_text = st.session_state.get("humanizer_result_editor", st.session_state["humanizer_result"])
        base = st.session_state.get("humanizer_result_stem") or "content"
        tag = "paraphrased" if mode_done == "Paraphraser" else "humanized"
        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ Download as Word (.docx)", data=_to_docx_bytes(result_text), file_name=f"{base}_{tag}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="download_humanized_docx", use_container_width=True)
        d2.download_button(
            "⬇️ Download as text (.txt)", data=result_text.encode("utf-8"), file_name=f"{base}_{tag}.txt",
            mime="text/plain", key="download_humanized_txt", use_container_width=True)
    st.caption(
        "These tools improve or rephrase writing; they do not guarantee a particular AI-detector score or make copied material original. "
        "Attribute sources, keep quotations marked, and review facts before publishing."
    )

with tab_check:
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
