"""Builds official-style PDF reports: combined, plagiarism-only, AI-only."""
import io
import hashlib
import datetime as dt
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.barcode.qr import QrCodeWidget

NAVY, TEAL, GREY = colors.HexColor("#0B2A4A"), colors.HexColor("#0E7C86"), colors.HexColor("#5B6770")
PALETTE = ["#0E7C86", "#C0392B", "#8E44AD", "#D68910", "#2874A6", "#239B56", "#B03A6F", "#6C7A89"]
TITLES = {"combined": "Plagiarism and AI Content Report", "plag": "Plagiarism Detection Report",
          "ai": "AI Content Detection Report"}

S = {
    "h": ParagraphStyle("h", fontName="Helvetica-BoldOblique", fontSize=11, textColor=NAVY, spaceBefore=10, spaceAfter=4),
    "b": ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=13, textColor=colors.HexColor("#222")),
    "s": ParagraphStyle("s", fontName="Helvetica", fontSize=7.5, leading=10, textColor=GREY),
    "t": ParagraphStyle("t", fontName="Times-Roman", fontSize=10, leading=14.5),
    "c": ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=11),
}


def grade(p):
    if p <= 10:
        return "A", "Satisfactory (0-10%)", colors.HexColor("#1E8449")
    if p <= 40:
        return "B", "Upgrade (11-40%)", colors.HexColor("#2874A6")
    if p <= 60:
        return "C", "Poor (41-60%)", colors.HexColor("#D68910")
    return "D", "Unacceptable (61-100%)", colors.HexColor("#C0392B")


class Numbered(canvas.Canvas):
    def __init__(self, *a, meta=None, **k):
        super().__init__(*a, **k)
        self._pages, self.meta = [], meta or {}

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._pages)
        for st in self._pages:
            self.__dict__.update(st)
            w, h = A4
            self.setFillColor(NAVY)
            self.setFont("Helvetica-Bold", 14)
            self.drawString(20 * mm, h - 14 * mm, "OriginCheck")
            self.setFont("Helvetica", 8)
            self.setFillColor(GREY)
            self.drawRightString(w - 20 * mm, h - 14 * mm, self.meta["title"])
            self.setStrokeColor(TEAL)
            self.setLineWidth(1.6)
            self.line(20 * mm, h - 17 * mm, w - 20 * mm, h - 17 * mm)
            self.setLineWidth(0.4)
            self.setStrokeColor(colors.lightgrey)
            self.line(20 * mm, 14 * mm, w - 20 * mm, 14 * mm)
            self.drawString(20 * mm, 9 * mm, f"Report ID: {self.meta['rid']}")
            self.drawRightString(w - 20 * mm, 9 * mm, f"Page {self._pageNumber} of {n}")
            super().showPage()
        super().save()


def bar(pct, col, w=440):
    d = Drawing(w, 12)
    d.add(Rect(0, 2, w, 8, fillColor=colors.HexColor("#E3E7EB"), strokeColor=None))
    d.add(Rect(0, 2, max(2, w * pct / 100), 8, fillColor=col, strokeColor=None))
    return d


def pie(title, items):
    d = Drawing(250, 118)
    d.add(String(0, 106, title, fontName="Helvetica-Oblique", fontSize=9, fillColor=NAVY))
    items = [(l, v, c) for l, v, c in items if v > 0.001]
    if not items:
        d.add(String(0, 50, "No data", fontSize=9))
        return d
    p = Pie()
    p.x, p.y, p.width, p.height = 4, 4, 90, 90
    p.data = [v for _, v, _ in items]
    p.labels = None
    for i, (_, _, c) in enumerate(items):
        p.slices[i].fillColor, p.slices[i].strokeColor = c, colors.white
    d.add(p)
    y = 84
    for l, v, c in items:
        d.add(Rect(105, y, 8, 8, fillColor=c, strokeColor=None))
        d.add(String(118, y, f"{l}: {v:.2f}%", fontSize=7.5))
        y -= 13
    return d


def qr(data, size=80):
    w = QrCodeWidget(data)
    b = w.getBounds()
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(w)
    return d


def kv(rows, cw=(120, 330)):
    t = Table([[Paragraph(f"<b>{a}</b>", S["c"]), Paragraph(escape(str(b)), S["c"])] for a, b in rows], colWidths=cw)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDD")),
                           ("TEXTCOLOR", (0, 0), (0, -1), NAVY)]))
    return t


def big_stats(cells):
    big = ParagraphStyle("big", fontName="Helvetica-Bold", fontSize=26, leading=32, alignment=1)
    lab = ParagraphStyle("lab", fontName="Helvetica", fontSize=8, leading=11, alignment=1, textColor=GREY)
    t = Table([[[Paragraph(f'<font color="{c}">{v}</font>', big), Paragraph(l, lab)] for v, l, c in cells]],
              colWidths=[150] * len(cells))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCD")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    return t


def hexc(c):
    return c.hexval().replace("0x", "#")


def build_pdf(kind, doc, summ, m):
    """kind: combined | plag | ai.  m: dict(author,title,user,mode,provider_note)."""
    now = dt.datetime.now()
    body = "".join(p["text"] for p in doc["paras"])
    rid = "OC-" + now.strftime("%Y%m%d") + "-" + hashlib.sha1((body + str(now)).encode()).hexdigest()[:6].upper()
    meta = {"title": TITLES[kind], "rid": rid}
    plag, ai = kind in ("combined", "plag"), kind in ("combined", "ai")
    ex, total_all = doc["excluded"], summ["total_words"] + sum(doc["excluded"].values())
    el = []

    # ---- page 1: submission + result information ----
    el += [Paragraph("Submission Information", S["h"]),
           kv([("Author Name", m["author"] or "-"), ("Title", m["title"] or "-"), ("Report ID", rid),
               ("Submitted by", m["user"]), ("Submission Date", now.strftime("%Y-%m-%d %H:%M:%S")),
               ("Total Words", f"{total_all:,} (checked: {summ['total_words']:,}; approx. {total_all // 300} pages)"),
               ("Scan mode", m["mode"]), ("Document type", "Thesis, paper, blog or article")]),
           Paragraph("Result Information", S["h"])]
    if plag:
        g = grade(summ["plag"])
        el += [Paragraph(f"Similarity <b>{summ['plag']}%</b> &nbsp; Grade <b>{g[0]}</b> - {g[1]}", S["b"]), bar(summ["plag"], g[2])]
    if ai:
        g = grade(summ["ai"])
        el += [Spacer(1, 4), Paragraph(f"Likely AI-written <b>{summ['ai']}%</b> &nbsp; Grade <b>{g[0]}</b> - {g[1]}", S["b"]),
               bar(summ["ai"], g[2])]
    el.append(Spacer(1, 8))
    charts = []
    if plag:
        pc = {"OpenAlex": "#0E7C86", "Crossref": "#D68910", "CORE": "#8E44AD", "Web": "#2874A6"}
        charts.append(pie("Sources type", [(k, v, colors.HexColor(pc[k])) for k, v in summ["by_provider"].items()]))
    if ai:
        charts.append(pie("AI likelihood (share of words)", [(k, v, colors.HexColor(c)) for (k, v), c in
                                                             zip(summ["ai_bins"].items(), ["#C0392B", "#D68910", "#1E8449"])]))
    rc = [("Checked", 100 * summ["total_words"] / max(1, total_all), "#0E7C86")] + \
         [(k, 100 * v / max(1, total_all), c) for (k, v), c in zip(ex.items(), ["#8E44AD", "#D68910", "#2874A6", "#6C7A89"])]
    charts.append(pie("Report content", [(l, v, colors.HexColor(c)) for l, v, c in rc]))
    qrcell = Table([[qr(f"OriginCheck|{rid}|{now:%Y-%m-%d}|{summ.get('plag', '-')}|{summ.get('ai', '-')}", 70),
                     Paragraph("<b>Report integrity code.</b> Scan to read this report's ID, date and scores, so a printed "
                               "copy can be compared with the original file.", S["s"])]], colWidths=[78, 165])
    cells = charts + [qrcell]
    for i in range(0, len(cells), 2):
        row = cells[i:i + 2]
        el.append(Table([row], colWidths=[250] * len(row), hAlign="LEFT"))
    db = [("Language", "English"), ("Quotes / References", "Excluded"), ("Very short lines / headings", "Excluded or merged"),
          ("Scholarly databases", "OpenAlex, Crossref" + (", CORE" if m["core"] else "")),
          ("Student papers / private archives", "Not searched"), ("Internet / web", "Searched (top results)" if m.get("web") else "Not searched")]
    el += [Paragraph("Exclusion and Database Selection", S["h"]), kv(db, (170, 280))]

    # ---- plagiarism summary ----
    if plag:
        g = grade(summ["plag"])
        el += [PageBreak(), Paragraph("OriginCheck Similarity Report", S["h"]),
               big_stats([(f"{summ['plag']}", "SIMILARITY %", hexc(g[2])), (len(summ["sources"]), "MATCHED SOURCES", "#222222"),
                          (g[0], "GRADE", hexc(g[2]))]),
               Spacer(1, 4), Paragraph("Grades: A Satisfactory (0-10%), B Upgrade (11-40%), C Poor (41-60%), D Unacceptable (61-100%)", S["s"]),
               Spacer(1, 6)]
        rows = [[Paragraph("<b>#</b>", S["c"]), Paragraph("<b>Matched source</b>", S["c"]), Paragraph("<b>%</b>", S["c"]),
                 Paragraph("<b>Source type</b>", S["c"])]]
        for d in summ["sources"][:60]:
            pc = "&lt;1" if d["pct"] < 1 else f"{d['pct']:.0f}"
            rows.append([Paragraph(f'<font color="{PALETTE[(d["id"] - 1) % 8]}"><b>{d["id"]}</b></font>', S["c"]),
                         Paragraph(escape(d["title"][:150]) + (f'<br/><font size="7" color="#5B6770">{escape(d["url"][:90])}</font>' if d["url"] else ""), S["c"]),
                         Paragraph(pc, S["c"]), Paragraph(d["provider"], S["c"])])
        if len(rows) == 1:
            rows.append(["", Paragraph("No significant matches found in the selected databases.", S["c"]), "", ""])
        t = Table(rows, colWidths=[24, 320, 30, 76], repeatRows=1)
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCC")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F5"))]))
        el.append(t)

    # ---- AI summary ----
    if ai:
        g = grade(summ["ai"])
        el += [PageBreak(), Paragraph("OriginCheck AI Content Report", S["h"]),
               big_stats([(f"{summ['ai']}", "LIKELY AI-WRITTEN %", hexc(g[2])),
                          (sum(1 for p in doc["paras"] if p["ai"] >= 0.5), "FLAGGED PARAGRAPHS", "#222222"), (g[0], "GRADE", hexc(g[2]))]),
               Spacer(1, 6), Paragraph("Paragraphs flagged by the detector (probability of AI authorship)", S["h"])]
        rows = [[Paragraph(f"<b>{h}</b>", S["c"]) for h in ("Para", "Words", "AI %", "Starts with")]]
        for i, p in enumerate(doc["paras"], 1):
            if p["ai"] >= 0.5:
                rows.append([Paragraph(str(i), S["c"]), Paragraph(str(p["n"]), S["c"]), Paragraph(f"{p['ai'] * 100:.0f}", S["c"]),
                             Paragraph(escape(p["text"][:110]) + "...", S["c"])])
        if len(rows) == 1:
            rows.append(["", "", "", Paragraph("No paragraphs flagged.", S["c"])])
        t = Table(rows[:80], colWidths=[34, 40, 36, 340], repeatRows=1)
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCC")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F5"))]))
        el.append(t)

    # ---- notes + document text ----
    notes = ("<b>How to read this report.</b> Similarity is measured against open scholarly databases and top web search results, and academic "
             "records are matched mostly on titles and abstracts (full text for CORE), so it cannot detect copying from paywalled journals or student archives, and web matching covers only "
             "the top search results. AI detection is a statistical estimate and can wrongly flag formal human writing. Highlighting is at "
             "paragraph level. Use both figures as signals for manual review, not as proof.")
    el += [PageBreak(), Paragraph(notes, S["s"]), Paragraph("Document Text", S["h"])]
    if plag:
        el.append(Paragraph("Highlighted paragraphs are similar to the numbered source in the Matched Sources list.", S["s"]))
    for p in doc["paras"]:
        txt = escape(p["text"])
        if plag and p["sid"]:
            col = PALETTE[(p["sid"] - 1) % 8]
            txt = f'<font backColor="#FFF3B0">{txt}</font> <font backColor="{col}" color="white"><b> {p["sid"]} </b></font>'
        elif ai and p["ai"] >= 0.5:
            txt = f'<font backColor="{"#F5C6C0" if p["ai"] >= 0.7 else "#FBE3B0"}">{txt}</font>'
        if ai:
            c = "#C0392B" if p["ai"] >= 0.7 else "#D68910" if p["ai"] >= 0.5 else "#1E8449"
            txt += f' <font color="{c}" size="7"><b>[AI {p["ai"] * 100:.0f}%]</b></font>'
        el += [Paragraph(txt, S["t"]), Spacer(1, 5)]

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=24 * mm, bottomMargin=20 * mm,
                      title=TITLES[kind], author="OriginCheck").build(el, canvasmaker=lambda *a, **k: Numbered(*a, meta=meta, **k))
    return buf.getvalue()
