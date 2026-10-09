"""PDF and Excel timetables (plan Section 19, Phase 4) - pure logic, no database, no web.

  class_pdf / class_xlsx       one class, the whole week
  teacher_pdf / teacher_xlsx   one teacher, the whole week
  master_pdf / master_xlsx     the whole school: one page / sheet per weekday, classes down the side, periods across

Every export has the college / school / class header, the term, the published version and the print date; the lunch break
and the short breaks are drawn as their own rows; combined sessions and parallel (elective) lectures are labelled.
The layouts are print-friendly: A4 landscape, light colours, fits one page wide.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

from ..validation import DAY_NAMES
from . import timing as timing_svc
from .viewdata import ViewData
from .views import IST

BRAND = colors.HexColor("#4f46e5")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#556079")
LINE = colors.HexColor("#cdd3e3")
SOFT = colors.HexColor("#f4f6fb")
LUNCH_FILL = colors.HexColor("#fff4d6")
BREAK_FILL = colors.HexColor("#f1f3f9")
FIXED_FILL = colors.HexColor("#e7e5e4")
EXTRA_FILL = colors.HexColor("#dbeafe")


def safe(text: Any) -> str:
    """The built-in PDF fonts know Western European letters only: anything else becomes a plain stand-in."""
    s = str(text if text is not None else "").replace("∥", "|").replace("→", "->").replace("…", "...")
    return s.encode("cp1252", "replace").decode("cp1252")


def file_stem(*parts: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_")[:90] or "timetable"


def hue_fill(key: str, light: float = 0.9) -> tuple[float, float, float]:
    h = int(hashlib.md5(key.encode()).hexdigest()[:6], 16) % 360 / 360
    import colorsys

    return colorsys.hls_to_rgb(h, light, 0.6)


def hex_of(rgb: tuple[float, float, float]) -> str:
    return "".join(f"{int(round(c * 255)):02X}" for c in rgb)


def cell_rgb(cell: dict[str, Any]) -> tuple[float, float, float]:
    if cell["kind"] == "extra":
        return (0.86, 0.92, 0.996)
    if cell["kind"] == "fixed":
        return (0.906, 0.898, 0.894)
    first = cell["items"][0]
    return hue_fill(first.get("code") or first["title"])


# ---------------------------------------------------------------------------------------------------- shared layout
@dataclass
class Header:
    college: str
    school: str
    title: str
    subtitle: str
    meta: str


@dataclass
class RowSpec:
    kind: str            # 'period' | 'break' | 'lunch'
    label: str
    time: str
    period: int | None = None


def row_specs(data: ViewData) -> list[RowSpec]:
    """The rows of a day: every period, the short breaks between them and the lunch break."""
    grid = data.grid
    out: list[RowSpec] = []
    lunch_done = False
    for i, p in enumerate(grid.periods):
        out.append(RowSpec("period", f"Period {p.number}", f"{timing_svc.to_hm(p.start)}-{timing_svc.to_hm(p.end)}", p.number))
        nxt = grid.periods[i + 1] if i + 1 < len(grid.periods) else None
        if nxt is None:
            break
        if grid.lunch and not lunch_done and p.end <= grid.lunch[0] and grid.lunch[1] <= nxt.start:
            out.append(RowSpec("lunch", "LUNCH", f"{timing_svc.to_hm(grid.lunch[0])}-{timing_svc.to_hm(grid.lunch[1])}"))
            lunch_done = True
        elif nxt.start - p.end > 0:
            out.append(RowSpec("break", "break", f"{nxt.start - p.end} min"))
    return out


_TITLES = ("dr.", "prof.", "mr.", "ms.", "mrs.", "shri", "smt.")


def short_name(name: str | None, limit: int = 20) -> str:
    """'Dr. Papri Das' -> 'P. Das' when space is tight (master sheet)."""
    if not name:
        return ""
    if len(name) <= limit:
        return name
    n = name.replace("External Faculty", "Ext. Faculty")
    if len(n) <= limit:
        return n
    words = [w for w in n.split() if w.lower() not in _TITLES]
    if len(words) >= 2 and not words[-1].startswith("("):
        return " ".join(w[0] + "." for w in words[:-1]) + " " + words[-1]
    return n


def _lab_tag(title: str, is_lab: bool) -> str:
    return title + (" (Lab)" if is_lab and "lab" not in title.lower() else "")


def cell_lines(cell: dict[str, Any], mode: str, me: str | None = None) -> list[str]:
    """The text of a cell. mode 'class': subject + teacher.  mode 'teacher': subject + class.  mode 'master': subject + teacher."""
    items = cell["items"]
    lines: list[str] = []
    if mode == "teacher":
        for it in items:
            lines.append(_lab_tag(it["title"], it["is_lab"]))
        classes = " + ".join(c["label"] for c in cell["classes"])
        lines.append(classes)
        if cell.get("combined") and len(cell["classes"]) > 1:
            lines.append("Combined session")
        if cell.get("parallel_with"):
            lines.append("Parallel with: " + ", ".join(cell["parallel_with"]))
        others = [
            name for it in items for tid, name in ((it["teacher_id"], it["teacher_name"]), (it["co_teacher_id"], it["co_teacher_name"]))
            if name and tid != me
        ]
        if others:
            lines.append("With: " + ", ".join(dict.fromkeys(others)))
        return lines
    compact = mode == "master"
    if len(items) > 1:
        lines.append("Parallel electives" if compact else "PARALLEL (elective group)")
        for it in items:
            t = (short_name(it["teacher_name"]) if compact else it["teacher_name"]) or "no teacher"
            lines.append(f"{_lab_tag(it['title'], it['is_lab'])} - {t}")
    else:
        it = items[0]
        lines.append(_lab_tag(it["title"], it["is_lab"]))
        names = [(short_name(n) if compact else n) for n in (it["teacher_name"], it["co_teacher_name"]) if n]
        if names:
            lines.append(" / ".join(names))
    if len(cell["classes"]) > 1:
        lines.append("Combined session: " + " + ".join(c["label"] for c in cell["classes"]))
    elif cell.get("combined_with"):
        lines.append("Combined with " + cell["combined_with"])
    return lines


def grid_matrix(data: ViewData, cells: list[dict[str, Any]], days: list[int]) -> tuple[list[RowSpec], dict[tuple[int, int], tuple[dict[str, Any], int]]]:
    """(rows, placed) where placed[(row_index, day)] = (cell, rows covered).  Periods that fall under a block are not listed again."""
    specs = row_specs(data)
    index_of = {r.period: i for i, r in enumerate(specs) if r.period}
    placed: dict[tuple[int, int], tuple[dict[str, Any], int]] = {}
    for c in cells:
        if c["kind"] == "extra" or c["day"] not in days:
            continue
        a = index_of.get(c["start_period"])
        b = index_of.get(c["start_period"] + c["length"] - 1)
        if a is None or b is None:
            continue
        placed[(a, c["day"])] = (c, b - a + 1)
    return specs, placed


def now_text() -> str:
    return datetime.now(IST).strftime("%d %b %Y, %H:%M")


def published_line(data: ViewData, sem_ids: set | None = None) -> str:
    pubs = [p for s, p in data.pubs.items() if sem_ids is None or s in sem_ids]
    if not pubs:
        return "Not published yet"
    last = max(pubs, key=lambda p: p["published_at"])
    when = last["published_at"].astimezone(IST).strftime("%d %b %Y")
    v = f"version {last['version']}" if len(pubs) == 1 else f"latest version {last['version']}"
    return f"Published {when} ({v})"


def legend(data: ViewData) -> str:
    g = data.grid
    bits = [f"Lectures are {g.periods[0].end - g.periods[0].start} min" if g.periods else "", f"short break {g.break_minutes} min"]
    if g.lunch:
        bits.append(f"lunch {timing_svc.to_hm(g.lunch[0])}-{timing_svc.to_hm(g.lunch[1])}")
    return ", ".join(b for b in bits if b)


def extras_rows(data: ViewData, today: date, *, sem_id=None, teacher_id=None) -> list[list[str]]:
    out: list[list[str]] = []
    seen: set = set()
    for x in sorted(data.extras, key=lambda x: (x.day_date, x.start)):
        if x.day_date < today or (sem_id and x.semester_id != sem_id) or (teacher_id and teacher_id not in x.teachers):
            continue
        if (x.day_date, x.key) in seen:
            continue
        seen.add((x.day_date, x.key))
        names = " / ".join(data.subjects[y.subject_id].name for y in data.extras if y.key == x.key and y.day_date == x.day_date and y.subject_id in data.subjects)
        out.append([
            x.day_date.strftime("%a %d %b %Y"), timing_svc.time_range(data.grid, x.start, x.length) or "", names or "Extra lecture",
            data.sems[x.semester_id].label if x.semester_id in data.sems else "", x.reason or "",
        ])
    return out


# ---------------------------------------------------------------------------------------------------- PDF
def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=8, leading=9.6, textColor=INK)
    return {
        "college": ParagraphStyle("college", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=BRAND),
        "school": ParagraphStyle("school", parent=base, fontSize=10.5, leading=13, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=17),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8.5, textColor=MUTED, leading=11),
        "cell": ParagraphStyle("cell", parent=base, fontSize=7.8, leading=9.4, alignment=1),
        "cellb": ParagraphStyle("cellb", parent=base, fontName="Helvetica-Bold", fontSize=8.2, leading=9.8, alignment=1),
        "head": ParagraphStyle("head", parent=base, fontName="Helvetica-Bold", fontSize=9, textColor=colors.white, alignment=1),
        "time": ParagraphStyle("time", parent=base, fontSize=7.5, leading=9, alignment=1, textColor=MUTED),
        "timeb": ParagraphStyle("timeb", parent=base, fontName="Helvetica-Bold", fontSize=8.5, leading=10, alignment=1),
        "small": ParagraphStyle("small", parent=base, fontSize=7.4, leading=9, textColor=MUTED),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=8, spaceAfter=3),
    }


def _wrap_count(text: str, font: str, size: float, width: float) -> int:
    total = 0
    for para in text.split(chr(10)):
        n, cur = 1, ""
        for w in para.split():
            trial = (cur + " " + w).strip()
            if stringWidth(trial, font, size) <= width:
                cur = trial
            else:
                n += 1 if cur else 0
                cur = w
        total += n
    return total


def _cell_flow(lines: list[str], st: dict[str, ParagraphStyle], width: float | None = None, height: float | None = None) -> list[Paragraph]:
    """The paragraphs of a cell; with a width and height the font shrinks (down to 5.4 pt) until the text fits the cell."""
    size = 7.8
    if width and height:
        for size in (8.2, 7.6, 7.0, 6.4, 5.8, 5.4):
            need = sum(_wrap_count(safe(t), "Helvetica-Bold" if i == 0 else "Helvetica", size, width) for i, t in enumerate(lines)) * size * 1.18
            if need <= height:
                break
    base = ParagraphStyle("fit", parent=st["cell"], fontSize=size, leading=size * 1.18)
    bold = ParagraphStyle("fitb", parent=st["cellb"], fontSize=size + 0.3, leading=(size + 0.3) * 1.18)
    out = [Paragraph(escape(safe(lines[0])), bold)]
    out += [Paragraph(escape(safe(x)), base) for x in lines[1:]]
    return out


def _doc(buf: io.BytesIO, header: Header, footer_left: str) -> tuple[BaseDocTemplate, ParagraphStyle]:
    doc = BaseDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=11 * mm, rightMargin=11 * mm, topMargin=10 * mm, bottomMargin=13 * mm,
        title=safe(header.title), author="TimeTablePro", subject=safe(f"{header.college} - {header.school}"),
    )

    def decorate(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.4)
        canvas.setFillColor(MUTED)
        canvas.drawString(11 * mm, 7 * mm, safe(footer_left))
        canvas.drawRightString(landscape(A4)[0] - 11 * mm, 7 * mm, f"Page {d.page}")
        canvas.setStrokeColor(LINE)
        canvas.line(11 * mm, 10 * mm, landscape(A4)[0] - 11 * mm, 10 * mm)
        canvas.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=decorate)])
    return doc, ParagraphStyle("x")


def _header_flow(h: Header, st: dict[str, ParagraphStyle], width: float) -> list[Any]:
    left = [Paragraph(escape(safe(h.college)), st["college"]), Paragraph(escape(safe(h.school)), st["school"])]
    right = [Paragraph(escape(safe(h.title)), ParagraphStyle("r1", parent=st["title"], alignment=2)),
             Paragraph(escape(safe(h.subtitle)), ParagraphStyle("r2", parent=st["meta"], alignment=2)),
             Paragraph(escape(safe(h.meta)), ParagraphStyle("r3", parent=st["meta"], alignment=2))]
    t = Table([[left, right]], colWidths=[width * 0.42, width * 0.58])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, BRAND),
                           ("BOTTOMPADDING", (0, 0), (-1, 0), 5), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return [t, Spacer(1, 4 * mm)]


def _week_table(data: ViewData, cells: list[dict[str, Any]], mode: str, width: float, st: dict[str, ParagraphStyle], height: float, me: str | None = None) -> Table:
    days = list(range(1, min(6, data.days_per_week) + 1))
    specs, placed = grid_matrix(data, cells, days)
    n_periods = sum(1 for r in specs if r.kind == "period")
    fixed_h = sum(3.4 * mm if r.kind == "break" else 7 * mm for r in specs if r.kind != "period")
    row_h = max(13 * mm, min(23 * mm, (height - 9 * mm - fixed_h) / max(1, n_periods)))
    time_w = 25 * mm
    col_w = (width - time_w) / len(days)
    head = [Paragraph("Time", st["head"])] + [Paragraph(DAY_NAMES[d], st["head"]) for d in days]
    rows: list[list[Any]] = [head]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]
    heights = [8 * mm]
    covered: set[tuple[int, int]] = set()
    for i, r in enumerate(specs):
        ri = i + 1
        if r.kind == "period":
            row = [[Paragraph(f"<b>P{r.period}</b>", st["timeb"]), Paragraph(r.time, st["time"])]]
            heights.append(row_h)
        elif r.kind == "lunch":
            row = [[Paragraph(f"<b>LUNCH</b>  {r.time}", st["time"])]]
            heights.append(7 * mm)
        else:
            row = [[Paragraph(f"break {r.time}", ParagraphStyle("b", parent=st["time"], fontSize=6.2, leading=7))]]
            heights.append(3.4 * mm)
        for d_i, d in enumerate(days):
            ci = d_i + 1
            if (i, d) in covered:
                row.append("")
                continue
            if r.kind == "lunch":
                row.append(Paragraph("lunch break", ParagraphStyle("l", parent=st["time"], textColor=colors.HexColor("#92400e"))))
            elif r.kind == "break":
                row.append("")
            elif (i, d) in placed:
                cell, span = placed[(i, d)]
                span_h = sum(heights[ri: ri + span])
                row.append(_cell_flow(cell_lines(cell, mode, me), st, col_w - 5, span_h - 5))
                rgb = cell_rgb(cell)
                style.append(("BACKGROUND", (ci, ri), (ci, ri + span - 1), colors.Color(*rgb)))
                if span > 1:
                    style.append(("SPAN", (ci, ri), (ci, ri + span - 1)))
                    for k in range(i + 1, i + span):
                        covered.add((k, d))
            else:
                row.append("")
        if r.kind == "lunch":
            style.append(("BACKGROUND", (0, ri), (-1, ri), LUNCH_FILL))
        elif r.kind == "break":
            style.append(("BACKGROUND", (0, ri), (-1, ri), BREAK_FILL))
        else:
            style.append(("BACKGROUND", (0, ri), (0, ri), SOFT))
        rows.append(row)
    table = Table(rows, colWidths=[time_w] + [col_w] * len(days), rowHeights=heights, repeatRows=1)
    table.setStyle(TableStyle(style))
    return table


def _extras_table(rows: list[list[str]], st: dict[str, ParagraphStyle], width: float) -> list[Any]:
    if not rows:
        return []
    head = [Paragraph(h, st["head"]) for h in ("Date", "Time", "Subject", "Class", "Reason")]
    body = [[Paragraph(escape(safe(c)), st["cell"]) for c in r] for r in rows[:14]]
    t = Table([head] + body, colWidths=[width * w for w in (0.17, 0.14, 0.3, 0.2, 0.19)])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BRAND), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("BACKGROUND", (0, 1), (-1, -1), colors.Color(*(0.86, 0.92, 0.996)))]))
    return [Paragraph("Extra (compensation) lectures coming up", st["h2"]), t]


def _week_pdf(data: ViewData, header: Header, cells: list[dict[str, Any]], mode: str, extras: list[list[str]], me: str | None = None) -> bytes:
    buf = io.BytesIO()
    doc, _ = _doc(buf, header, f"{header.college} - {header.school} | Printed {now_text()} | {legend(data)}")
    st = _styles()
    flow = _header_flow(header, st, doc.width)
    height = doc.height - 38 * mm - (32 * mm if extras else 0)
    flow.append(_week_table(data, cells, mode, doc.width, st, height, me))
    flow.append(Spacer(1, 2 * mm))
    flow.append(Paragraph(escape(safe("Colours: one colour per subject; grey = locked slot (PE / SOL / fixed); parallel = elective options running together; "
                                      "combined = several classes taught together. " + legend(data) + ".")), st["small"]))
    flow += _extras_table(extras, st, doc.width)
    doc.build(flow)
    return buf.getvalue()


def class_pdf(data: ViewData, sem_id, cells: list[dict[str, Any]], today: date) -> bytes:
    info = data.sems[sem_id]
    pub = data.pubs.get(sem_id)
    header = Header(data.college_name, data.school_name, f"Class Timetable - {info.label}", f"{term_text(data)} | Weekly timetable",
                    published_line(data, {sem_id}) if pub else "Not published yet")
    return _week_pdf(data, header, cells, "class", extras_rows(data, today, sem_id=sem_id))


def teacher_pdf(data: ViewData, teacher_id, cells: list[dict[str, Any]], today: date) -> bytes:
    name = data.person_name(teacher_id) or "Teacher"
    lect = sum(1 for c in cells if c["kind"] != "extra")
    header = Header(data.college_name, data.school_name, f"Teacher Timetable - {name}", f"{term_text(data)} | {lect} lectures a week",
                    published_line(data))
    return _week_pdf(data, header, cells, "teacher", extras_rows(data, today, teacher_id=teacher_id), str(teacher_id))


def term_text(data: ViewData) -> str:
    return f"{data.term['kind'].capitalize()} {data.term['year']}"


def _master_table(data: ViewData, day: int, cells: list[dict[str, Any]], classes: list[Any], width: float, st: dict[str, ParagraphStyle], height: float) -> Table:
    periods = data.grid.periods
    lunch_after = None
    if data.grid.lunch:
        for p, nxt in zip(periods, periods[1:]):
            if p.end <= data.grid.lunch[0] and data.grid.lunch[1] <= nxt.start:
                lunch_after = p.number
    cols: list[tuple[str, int | None]] = []
    for p in periods:
        cols.append(("period", p.number))
        if lunch_after == p.number:
            cols.append(("lunch", None))
    class_w = 34 * mm
    lunch_w = 7 * mm
    n_p = len(periods)
    period_w = (width - class_w - (lunch_w if lunch_after else 0)) / max(1, n_p)
    head = [Paragraph("Class", st["head"])]
    for kind, n in cols:
        if kind == "lunch":
            head.append(Paragraph("L", st["head"]))
        else:
            p = periods[n - 1]  # type: ignore[index]
            head.append(Paragraph(f"P{n}<br/><font size=6.5>{timing_svc.to_hm(p.start)}-{timing_svc.to_hm(p.end)}</font>", st["head"]))
    rows: list[list[Any]] = [head]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]
    by_class: dict[Any, dict[int, dict[str, Any]]] = {}
    for c in cells:
        if c["day"] != day or c["kind"] == "extra":
            continue
        for cl in c["classes"]:
            by_class.setdefault(cl["id"], {})[c["start_period"]] = c
    col_index = {n: i + 1 for i, (k, n) in enumerate(cols) if k == "period"}
    row_h = max(15 * mm, min(30 * mm, (height - 12 * mm) / max(1, len(classes))))
    for ri, cl in enumerate(classes, start=1):
        row: list[Any] = [Paragraph(f"<b>{escape(safe(cl.label))}</b>", ParagraphStyle("cl", parent=st["cell"], alignment=0))]
        row += [""] * len(cols)
        mine = by_class.get(str(cl.id), {})
        rule = data.rule(cl.id, day)
        for n in range(1, n_p + 1):
            ci = col_index[n]
            c = mine.get(n)
            covered = any(s < n < s + x["length"] for s, x in mine.items())
            if c is not None:
                row[ci] = _cell_flow(cell_lines(c, "master"), st, period_w * c["length"] - 5 + (lunch_w if lunch_after and n <= lunch_after < n + c["length"] - 1 else 0), row_h - 4)
                style.append(("BACKGROUND", (ci, ri), (ci + c["length"] - 1 + (1 if lunch_after and n <= lunch_after < n + c["length"] - 1 else 0), ri), colors.Color(*cell_rgb(c))))
                end_col = ci + c["length"] - 1 + (1 if lunch_after and n <= lunch_after < n + c["length"] - 1 else 0)
                if end_col > ci:
                    style.append(("SPAN", (ci, ri), (end_col, ri)))
            elif not covered:
                p = periods[n - 1]
                off = rule.is_off or (rule.end_time is not None and p.end > rule.end_time.hour * 60 + rule.end_time.minute)
                if off:
                    style.append(("BACKGROUND", (ci, ri), (ci, ri), colors.HexColor("#e5e7eb")))
        if lunch_after:
            li = [i for i, (k, _n) in enumerate(cols) if k == "lunch"][0] + 1
            if not any(s <= lunch_after < s + x["length"] - 1 for s, x in mine.items()):
                style.append(("BACKGROUND", (li, ri), (li, ri), LUNCH_FILL))
        rows.append(row)
    widths = [class_w] + [lunch_w if k == "lunch" else period_w for k, _ in cols]
    t = Table(rows, colWidths=widths, rowHeights=[10 * mm] + [row_h] * len(classes), repeatRows=1)
    t.setStyle(TableStyle(style))
    return t


def master_pdf(data: ViewData, cells: list[dict[str, Any]], classes: list[Any], days: list[int], filters_text: str) -> bytes:
    buf = io.BytesIO()
    header_base = Header(data.college_name, data.school_name, "School Master Timetable", f"{term_text(data)}{(' | ' + filters_text) if filters_text else ''}",
                         published_line(data))
    doc, _ = _doc(buf, header_base, f"{data.college_name} - {data.school_name} | Printed {now_text()} | {legend(data)}")
    st = _styles()
    flow: list[Any] = []
    for i, d in enumerate(days):
        if i:
            flow.append(PageBreak())
        h = Header(data.college_name, data.school_name, f"School Master Timetable - {DAY_NAMES[d]}", header_base.subtitle, header_base.meta)
        flow += _header_flow(h, st, doc.width)
        flow.append(_master_table(data, d, cells, classes, doc.width, st, doc.height - 26 * mm))
        flow.append(Spacer(1, 2 * mm))
        flow.append(Paragraph(escape(safe("L = lunch break. Grey = no lectures (day off / class ended). " + legend(data) + ".")), st["small"]))
    if not days:
        flow.append(Paragraph("There is nothing to show for these filters.", st["h2"]))
    doc.build(flow)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------- Excel
THIN = Side(style="thin", color="CDD3E3")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _fill(rgb_hex: str) -> PatternFill:
    return PatternFill("solid", start_color=rgb_hex, end_color=rgb_hex)


def _xl_header(ws, header: Header, width_cols: int) -> int:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width_cols)
    ws.cell(1, 1, header.college).font = Font(bold=True, size=16, color="4F46E5")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width_cols)
    ws.cell(2, 1, header.school).font = Font(size=11, color="556079")
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=width_cols)
    ws.cell(3, 1, header.title).font = Font(bold=True, size=14)
    ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=width_cols)
    ws.cell(4, 1, f"{header.subtitle}   |   {header.meta}   |   Printed {now_text()}").font = Font(size=9, color="556079")
    ws.row_dimensions[1].height = 24
    return 6


def _xl_page(ws, last_row: int, header_row: int) -> None:
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.6
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.oddFooter.left.text = "TimeTablePro"
    ws.oddFooter.right.text = "Page &P of &N"
    ws.print_area = f"A1:{get_column_letter(ws.max_column)}{last_row}"


def _write_week(ws, data: ViewData, header: Header, cells: list[dict[str, Any]], mode: str, me: str | None = None) -> int:
    days = list(range(1, min(6, data.days_per_week) + 1))
    ncols = len(days) + 1
    r0 = _xl_header(ws, header, ncols)
    ws.cell(r0, 1, "Time").font = Font(bold=True, color="FFFFFF")
    for i, d in enumerate(days):
        ws.cell(r0, i + 2, DAY_NAMES[d]).font = Font(bold=True, color="FFFFFF")
    for c in range(1, ncols + 1):
        cell = ws.cell(r0, c)
        cell.fill = _fill("4F46E5")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BOX
    ws.row_dimensions[r0].height = 22
    specs, placed = grid_matrix(data, cells, days)
    row_of = {}
    for i, r in enumerate(specs):
        row = r0 + 1 + i
        row_of[i] = row
        if r.kind == "period":
            ws.cell(row, 1, f"P{r.period}\n{r.time}")
            ws.row_dimensions[row].height = 62
            ws.cell(row, 1).fill = _fill("F4F6FB")
        elif r.kind == "lunch":
            ws.cell(row, 1, f"LUNCH {r.time}")
            ws.row_dimensions[row].height = 18
            for c in range(1, ncols + 1):
                ws.cell(row, c).fill = _fill("FFF4D6")
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
            ws.cell(row, 1).alignment = Alignment(horizontal="center", vertical="center")
        else:
            ws.cell(row, 1, f"break {r.time}")
            ws.row_dimensions[row].height = 11
            for c in range(1, ncols + 1):
                ws.cell(row, c).fill = _fill("F1F3F9")
            ws.cell(row, 1).font = Font(size=7, color="8791A8")
        for c in range(1, ncols + 1):
            ws.cell(row, c).border = BOX
        if r.kind != "lunch":
            ws.cell(row, 1).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if r.kind == "period":
                ws.cell(row, 1).font = Font(bold=True, size=9)
    for (i, d), (cell, span) in placed.items():
        row = row_of[i]
        col = days.index(d) + 2
        xl = ws.cell(row, col, "\n".join(cell_lines(cell, mode, me)))
        xl.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        xl.font = Font(size=9)
        xl.fill = _fill(hex_of(cell_rgb(cell)))
        if span > 1:
            ws.merge_cells(start_row=row, start_column=col, end_row=row_of[i + span - 1], end_column=col)
        for rr in range(row, row_of[i + span - 1] + 1):
            ws.cell(rr, col).border = BOX
            ws.cell(rr, col).fill = _fill(hex_of(cell_rgb(cell)))
    ws.column_dimensions["A"].width = 16
    for i in range(len(days)):
        ws.column_dimensions[get_column_letter(i + 2)].width = 27
    last = r0 + len(specs)
    ws.cell(last + 2, 1, legend(data) + ".  Colours = one per subject; grey = locked slot (PE / SOL / fixed).").font = Font(size=8, italic=True, color="556079")
    _xl_page(ws, last + 2, r0)
    ws.freeze_panes = ws.cell(r0 + 1, 2)
    return last + 4


def _write_extras(ws, start: int, rows: list[list[str]]) -> None:
    if not rows:
        return
    ws.cell(start, 1, "Extra (compensation) lectures coming up").font = Font(bold=True, size=11)
    for i, h in enumerate(("Date", "Time", "Subject", "Class", "Reason")):
        c = ws.cell(start + 1, i + 1, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _fill("4F46E5")
    for r, row in enumerate(rows, start=start + 2):
        for i, v in enumerate(row):
            c = ws.cell(r, i + 1, v)
            c.fill = _fill("DBEAFE")
            c.border = BOX
            c.alignment = Alignment(wrap_text=True, vertical="center")


def _save(wb: Workbook) -> bytes:
    # a name that starts with "=" must stay TEXT: Excel would otherwise run it as a formula
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.data_type == "f":
                    c.data_type = "s"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def class_xlsx(data: ViewData, sem_id, cells: list[dict[str, Any]], today: date) -> bytes:
    info = data.sems[sem_id]
    header = Header(data.college_name, data.school_name, f"Class Timetable - {info.label}", f"{term_text(data)} | Weekly timetable", published_line(data, {sem_id}))
    wb = Workbook()
    ws = wb.active
    ws.title = "Timetable"
    nxt = _write_week(ws, data, header, cells, "class")
    _write_extras(ws, nxt, extras_rows(data, today, sem_id=sem_id))
    sheet = wb.create_sheet("Subjects")
    for i, h in enumerate(("Code", "Subject", "Teacher(s)", "Lectures / week", "Credits", "Semester hours")):
        c = sheet.cell(1, i + 1, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _fill("4F46E5")
    teachers: dict[Any, set] = {}
    weekly: dict[Any, int] = {}
    for r in data.rows_of_class(sem_id):
        if r.subject_id:
            weekly[r.subject_id] = weekly.get(r.subject_id, 0) + r.length
            for t in r.teachers:
                teachers.setdefault(r.subject_id, set()).add(data.person_name(t))
    for n, s in enumerate(sorted((x for x in data.subjects.values() if x.semester_id == sem_id), key=lambda x: x.name.lower()), start=2):
        for i, v in enumerate((s.code, s.name, ", ".join(sorted(x for x in teachers.get(s.id, set()) if x)) or "-", weekly.get(s.id, 0), s.credits, s.hours)):
            sheet.cell(n, i + 1, v)
    for col, w in zip("ABCDEF", (12, 40, 34, 16, 10, 16)):
        sheet.column_dimensions[col].width = w
    return _save(wb)


def teacher_xlsx(data: ViewData, teacher_id, cells: list[dict[str, Any]], today: date) -> bytes:
    name = data.person_name(teacher_id) or "Teacher"
    lect = sum(1 for c in cells if c["kind"] != "extra")
    header = Header(data.college_name, data.school_name, f"Teacher Timetable - {name}", f"{term_text(data)} | {lect} lectures a week", published_line(data))
    wb = Workbook()
    ws = wb.active
    ws.title = "Timetable"
    nxt = _write_week(ws, data, header, cells, "teacher", str(teacher_id))
    _write_extras(ws, nxt, extras_rows(data, today, teacher_id=teacher_id))
    sheet = wb.create_sheet("Lectures")
    for i, h in enumerate(("Day", "Period", "Time", "Subject", "Class", "Note")):
        c = sheet.cell(1, i + 1, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _fill("4F46E5")
    n = 2
    for c in sorted((x for x in cells if x["kind"] != "extra"), key=lambda x: (x["day"], x["start_period"])):
        span = f"{c['start_period']}" if c["length"] == 1 else f"{c['start_period']}-{c['start_period'] + c['length'] - 1}"
        note = "Combined session" if c.get("combined") and len(c["classes"]) > 1 else ("Parallel with " + ", ".join(c["parallel_with"]) if c.get("parallel_with") else "")
        for v_i, v in enumerate((DAY_NAMES[c["day"]], span, c["time"], " / ".join(i["title"] for i in c["items"]), " + ".join(x["label"] for x in c["classes"]), note)):
            sheet.cell(n, v_i + 1, v)
        n += 1
    for col, w in zip("ABCDEF", (12, 9, 14, 38, 38, 36)):
        sheet.column_dimensions[col].width = w
    return _save(wb)


def master_xlsx(data: ViewData, cells: list[dict[str, Any]], classes: list[Any], days: list[int], filters_text: str) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    periods = data.grid.periods
    for d in days:
        ws = wb.create_sheet(DAY_NAMES[d])
        header = Header(data.college_name, data.school_name, f"School Master Timetable - {DAY_NAMES[d]}",
                        f"{term_text(data)}{(' | ' + filters_text) if filters_text else ''}", published_line(data))
        ncols = len(periods) + 1
        r0 = _xl_header(ws, header, ncols)
        ws.cell(r0, 1, "Class")
        for i, p in enumerate(periods):
            ws.cell(r0, i + 2, f"P{p.number}\n{timing_svc.to_hm(p.start)}-{timing_svc.to_hm(p.end)}")
        for c in range(1, ncols + 1):
            h = ws.cell(r0, c)
            h.font = Font(bold=True, color="FFFFFF")
            h.fill = _fill("4F46E5")
            h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            h.border = BOX
        ws.row_dimensions[r0].height = 30
        by_class: dict[str, dict[int, dict[str, Any]]] = {}
        for c in cells:
            if c["day"] == d and c["kind"] != "extra":
                for cl in c["classes"]:
                    by_class.setdefault(cl["id"], {})[c["start_period"]] = c
        for ri, cl in enumerate(classes, start=r0 + 1):
            ws.cell(ri, 1, cl.label).font = Font(bold=True, size=9)
            ws.cell(ri, 1).alignment = Alignment(vertical="center", wrap_text=True)
            ws.row_dimensions[ri].height = 58
            mine = by_class.get(str(cl.id), {})
            rule = data.rule(cl.id, d)
            for c in range(1, ncols + 1):
                ws.cell(ri, c).border = BOX
            for n, p in enumerate(periods, start=1):
                col = n + 1
                cell = mine.get(n)
                if cell is not None:
                    x = ws.cell(ri, col, "\n".join(cell_lines(cell, "master")))
                    x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                    x.font = Font(size=9)
                    fill = _fill(hex_of(cell_rgb(cell)))
                    for k in range(cell["length"]):
                        if col + k <= ncols:
                            ws.cell(ri, col + k).fill = fill
                            ws.cell(ri, col + k).border = BOX
                    if cell["length"] > 1 and col + cell["length"] - 1 <= ncols:
                        ws.merge_cells(start_row=ri, start_column=col, end_row=ri, end_column=col + cell["length"] - 1)
                elif not any(s < n < s + x["length"] for s, x in mine.items()):
                    off = rule.is_off or (rule.end_time is not None and p.end > rule.end_time.hour * 60 + rule.end_time.minute)
                    if off:
                        ws.cell(ri, col).fill = _fill("E5E7EB")
        ws.column_dimensions["A"].width = 24
        for i in range(len(periods)):
            ws.column_dimensions[get_column_letter(i + 2)].width = 24
        last = r0 + len(classes)
        ws.cell(last + 2, 1, legend(data) + ".  Grey = no lectures (day off / class ended).").font = Font(size=8, italic=True, color="556079")
        _xl_page(ws, last + 2, r0)
        ws.freeze_panes = ws.cell(r0 + 1, 2)
    flat = wb.create_sheet("All lectures")
    for i, h in enumerate(("Day", "Period", "Time", "Class", "Subject", "Teacher(s)", "Note")):
        c = flat.cell(1, i + 1, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _fill("4F46E5")
    n = 2
    for c in sorted((x for x in cells if x["kind"] != "extra"), key=lambda x: (x["day"], x["start_period"], x["classes"][0]["label"] if x["classes"] else "")):
        span = f"{c['start_period']}" if c["length"] == 1 else f"{c['start_period']}-{c['start_period'] + c['length'] - 1}"
        teachers = ", ".join(dict.fromkeys(n_ for i in c["items"] for n_ in (i["teacher_name"], i["co_teacher_name"]) if n_))
        note = "Combined session" if c.get("combined") and len(c["classes"]) > 1 else ("Parallel (elective group)" if len(c["items"]) > 1 else "")
        for v_i, v in enumerate((DAY_NAMES[c["day"]], span, c["time"], " + ".join(x["label"] for x in c["classes"]), " / ".join(i["title"] for i in c["items"]), teachers, note)):
            flat.cell(n, v_i + 1, v)
        n += 1
    for col, w in zip("ABCDEFG", (12, 9, 14, 36, 38, 34, 24)):
        flat.column_dimensions[col].width = w
    flat.freeze_panes = "A2"
    if not wb.sheetnames or not days:
        pass
    return _save(wb)


# ---------------------------------------------------------------------------------------------------- substitution sheet
def _status_text(row: dict[str, Any]) -> tuple[str, tuple[float, float, float]]:
    st = row["status"]
    if st == "filled":
        return (f"{row.get('sub_name') or 'Assigned'}" + (f" ({row['mode']})" if row.get("mode") else "")), (0.86, 0.96, 0.88)
    if st == "offering":
        return "Offer sent - waiting", (0.90, 0.92, 0.996)
    if st == "unfilled":
        return "NOT COVERED - assign", (0.996, 0.86, 0.86)
    return "Needs a substitute", (0.996, 0.95, 0.80)


def substitution_sheet_pdf(data: ViewData, d: date, rows: list[dict[str, Any]]) -> bytes:
    """A printable daily substitution sheet: every absent teacher's lecture for the day and who is covering it."""
    buf = io.BytesIO()
    header = Header(data.college_name, data.school_name, "Daily Substitution Sheet", d.strftime("%A, %d %B %Y"), f"{term_text(data)}")
    doc, _ = _doc(buf, header, f"{data.college_name} - {data.school_name} | Printed {now_text()}")
    st = _styles()
    flow: list[Any] = _header_flow(header, st, doc.width)
    if not rows:
        flow.append(Paragraph("No teacher is absent on this day - nothing to substitute.", st["h2"]))
        doc.build(flow)
        return buf.getvalue()
    head = [Paragraph(h, st["head"]) for h in ("Period", "Time", "Class", "Subject", "Absent teacher", "Covered by")]
    table_rows = [head]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(sorted(rows, key=lambda x: (x["start_period"], x.get("absent_name") or "")), start=1):
        classes = ", ".join(sorted(data.sems[s].label for s in (r["semester_ids"] or []) if s in data.sems))
        span = f"P{r['start_period']}" + (f"-{r['start_period'] + r['length'] - 1}" if r["length"] > 1 else "")
        status_text, rgb = _status_text(r)
        cells = [span, timing_svc.time_range(data.grid, r["start_period"], r["length"]) or "", classes, r["label"], r.get("absent_name") or "", status_text]
        table_rows.append([Paragraph(escape(safe(c)), st["cell"]) for c in cells])
        style.append(("BACKGROUND", (5, i), (5, i), colors.Color(*rgb)))
    widths = [doc.width * w for w in (0.08, 0.14, 0.22, 0.24, 0.17, 0.15)]
    t = Table(table_rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(style))
    flow.append(t)
    flow.append(Spacer(1, 3 * mm))
    need = sum(1 for r in rows if r["status"] in ("open", "offering", "unfilled"))
    flow.append(Paragraph(escape(safe(f"{len(rows)} lecture(s) to cover, {len(rows) - need} arranged, {need} still open. Green = covered, yellow = pending, red = not covered.")), st["small"]))
    doc.build(flow)
    return buf.getvalue()
