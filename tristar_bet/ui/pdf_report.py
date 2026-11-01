"""PDF summary report: information/result tables and overlay figures, no point data.

The caller (MainWindow) supplies already-computed content, so the PDF shows the
same fits and settings as the screen. Rendering uses only Qt and pyqtgraph,
which the application already bundles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import html
import math
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

SAMPLES_PER_TABLE = 4
PDF_DPI = 300
BODY_PT = 7.5  # matches Micromeritics report text (about 7.5 pt); headings 1-1.5 pt larger
BRAND_NAME = "DragonScience"
WEBSITE = "www.dragonscience.top"
LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "dragonscience-logo.png"
FIGURE_WIDTH_FRACTION = 0.70  # figure width relative to the text width; two parameter+figure blocks per page


@dataclass
class Series:
    label: str
    color: str
    x: list[float]
    y: list[float]
    style: str = "line"  # "line", "dash", "points", "fit" (thin line, no markers, not in legend)


@dataclass
class Figure:
    title: str
    x_label: str
    y_label: str
    series: list[Series] = field(default_factory=list)
    log_x: bool = False
    note: str = ""
    params: "Table | None" = None  # method parameters printed above the figure


@dataclass
class Table:
    title: str
    row_labels: list[str]
    columns: list[tuple[str, list[str]]]  # (sample label, values aligned with row_labels)
    note: str = ""


@dataclass
class Report:
    title: str
    subtitle: str
    tables: list[Table]
    figures: list[Figure]
    footnote: str = ""


def _finite_pairs(xs, ys, positive_x=False):
    out_x, out_y = [], []
    for x, y in zip(xs, ys):
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y) and (x > 0 or not positive_x):
            out_x.append(x)
            out_y.append(y)
    return out_x, out_y


def render_figure(figure: Figure, width: int = 1100, height: int = 620) -> QtGui.QImage | None:
    """Draw one overlay chart offscreen; None when no series has data."""
    widget = pg.PlotWidget(background="w")
    widget.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)  # lay out at real size, never visible
    widget.resize(width, height)
    plot = widget.getPlotItem()
    tick_font = QtGui.QFont("Microsoft YaHei")
    tick_font.setPixelSize(13)
    plot.setTitle(figure.title, color="#111111", size="17px")
    for axis_name, label in (("bottom", figure.x_label), ("left", figure.y_label)):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen("#333333"))
        axis.setTextPen(pg.mkPen("#222222"))
        axis.setLabel(label, color="#222222", **{"font-size": "15px"})
        axis.enableAutoSIPrefix(False)
        axis.setStyle(tickFont=tick_font)
    plot.showGrid(x=True, y=True, alpha=0.25)
    legend = plot.addLegend(offset=(-10, 10), labelTextColor="#111111", labelTextSize="13px")
    legend.setBrush(pg.mkBrush(255, 255, 255, 220))
    drawn = 0
    for series in figure.series:
        xs, ys = _finite_pairs(series.x, series.y, positive_x=figure.log_x)
        if not xs:
            continue
        color = QtGui.QColor(series.color)
        name = None if series.style == "fit" or not series.label else series.label
        if series.style == "points":
            item = pg.ScatterPlotItem(xs, ys, size=7, pen=pg.mkPen(color), brush=pg.mkBrush(color), name=name)
        else:
            style = QtCore.Qt.DashLine if series.style == "dash" else QtCore.Qt.SolidLine
            pen_width = 1.4 if series.style == "fit" else 2.2
            item = pg.PlotDataItem(xs, ys, pen=pg.mkPen(color, width=pen_width, style=style), name=name,
                                   symbol="o" if series.style == "line" and len(xs) <= 60 else None,
                                   symbolSize=5, symbolBrush=color, symbolPen=color)
        plot.addItem(item)
        drawn += series.style != "fit"
    if not drawn:
        widget.deleteLater()
        return None
    if figure.log_x:
        plot.setLogMode(x=True, y=False)
    widget.show()
    plot.autoRange()
    QtWidgets.QApplication.processEvents()
    image = widget.grab().toImage()
    image.setDevicePixelRatio(1.0)
    if (image.width(), image.height()) != (width, height):  # high-DPI screens grab at a larger scale
        image = image.scaled(width, height, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    widget.close()
    widget.deleteLater()
    return image


def _table_chunks(table: Table, padding: int = 8) -> list[str]:
    """HTML for each group of samples; the first carries title and note, later ones '(续)'."""
    chunks = []
    for start in range(0, max(len(table.columns), 1), SAMPLES_PER_TABLE):
        chunk = table.columns[start:start + SAMPLES_PER_TABLE]
        title = table.title if start == 0 else f"{table.title}（续）"
        parts = [f"<h3>{html.escape(title)}</h3>"]
        if table.note and start == 0:
            parts.append(f"<p class='note'>{html.escape(table.note)}</p>")
        head = "".join(f"<th>{html.escape(label)}</th>" for label, _values in chunk)
        rows = []
        for index, row_label in enumerate(table.row_labels):
            cells = "".join(f"<td>{html.escape(str(values[index]) if index < len(values) else '')}</td>"
                            for _label, values in chunk)
            rows.append(f"<tr><td class='key'>{html.escape(row_label)}</td>{cells}</tr>")
        parts.append(f"<table cellspacing='0' cellpadding='{padding}'><tr><th class='key'>项目</th>{head}</tr>{''.join(rows)}</table>")
        chunks.append("".join(parts))
    return chunks


def _table_html(table: Table, padding: int = 8) -> str:
    return "<br/>".join(_table_chunks(table, padding))


def _body_font(points: float, bold: bool = False) -> QtGui.QFont:
    font = QtGui.QFont("Microsoft YaHei")
    font.setPointSizeF(points)
    font.setBold(bold)
    return font


def _text_document(writer, width: float, mm: float, html_body: str):
    document = QtGui.QTextDocument()
    document.documentLayout().setPaintDevice(writer)
    document.setDocumentMargin(0)
    document.setTextWidth(width)  # one block at its natural height; pages are laid out by write_pdf
    document.setDefaultFont(_body_font(BODY_PT))
    gap = int(2.5 * mm)
    document.setDefaultStyleSheet(
        f"h3{{font-size:{BODY_PT + 1}pt;font-weight:bold;color:#0f2a44;margin-top:{gap}px;margin-bottom:{gap // 3}px;}}"
        f"p{{margin-top:0px;margin-bottom:{gap // 3}px;}} p.note{{color:#555555;font-size:{BODY_PT - 0.5}pt;}}"
        "table{border-collapse:collapse;width:100%;} th{background-color:#e8eef4;border:1px solid #9aa9b8;font-weight:bold;}"
        "td{border:1px solid #c3ccd5;} td.key,th.key{background-color:#f4f6f8;font-weight:bold;}"
    )
    document.setHtml("<html><body>" + html_body + "</body></html>")
    return document


def write_pdf(report: Report, path: str | Path) -> int:
    """Write the report; returns the number of figures drawn.

    Laid out directly on the PDF device (its own resolution and page size), so
    font sizes do not follow the Windows display-scaling setting. Every page
    carries the DragonScience header (logo, title, website) and a page footer.
    Text sizes follow the Micromeritics report style (about 7.5 pt). Each
    figure is preceded by a table of the method parameters used for it.
    """
    writer = QtGui.QPdfWriter(str(path))
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    writer.setPageMargins(QtCore.QMarginsF(15, 10, 15, 10), QtGui.QPageLayout.Millimeter)
    writer.setResolution(PDF_DPI)
    writer.setTitle(report.title)
    writer.setCreator(f"{BRAND_NAME} {WEBSITE}")
    page_width, page_height = writer.width(), writer.height()
    mm = PDF_DPI / 25.4
    header = int(20 * mm)
    footer = int(6 * mm)
    content_height = page_height - header - footer

    padding = int(0.3 * mm)
    spacing = 3 * mm
    figure_width = page_width * FIGURE_WIDTH_FRACTION
    note_height = int(4 * mm)

    # Every item is an unbreakable block: a table with its heading, or a figure
    # with its parameter table. A block that does not fit moves to the next page.
    blocks = []  # dicts: text (QTextDocument) / image / note / height / new_page
    def text_block(markup, new_page=False):
        document = _text_document(writer, page_width, mm, markup)
        blocks.append({"text": document, "height": document.size().height(), "new_page": new_page})

    text_block(f"<p class='note' align='center'>{html.escape(report.subtitle)}</p>")
    for table in report.tables:
        for chunk in _table_chunks(table, padding):
            text_block(chunk)
    if report.footnote:
        text_block(f"<p class='note'>{html.escape(report.footnote)}</p>")
    figures_start = True
    drawn = 0
    for figure in report.figures:
        image = render_figure(figure)
        if image is None:
            continue
        params = None
        if figure.params is not None and figure.params.columns:
            params = _text_document(writer, page_width, mm, _table_html(figure.params, padding=padding))
        image_height = figure_width * image.height() / image.width()
        params_height = params.size().height() + 1.5 * mm if params else 0
        blocks.append({"text": params, "image": image, "image_height": image_height, "note": figure.note,
                       "params_height": params_height, "new_page": figures_start,
                       "height": params_height + image_height + (note_height if figure.note else 0)})
        figures_start = False  # figures begin on a fresh page, as in the vendor report
        drawn += 1

    page, y = 0, 0.0
    for block in blocks:
        if block["new_page"] and y > 0 or y > 0 and y + block["height"] > content_height:
            page, y = page + 1, 0.0
        block["page"], block["top"] = page, y
        y += block["height"] + spacing
    total = page + 1
    logo = QtGui.QImage(str(LOGO_PATH)) if LOGO_PATH.exists() else QtGui.QImage()
    painter = QtGui.QPainter(writer)
    try:
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        def frame(index):
            # Header: DragonScience logo centred, report title, website; thin rule under it.
            logo_height = 7.5 * mm
            if not logo.isNull():
                logo_width = logo_height * logo.width() / logo.height()
                painter.drawImage(QtCore.QRectF((page_width - logo_width) / 2, 0, logo_width, logo_height), logo)
            painter.setPen(QtGui.QColor("#0f2a44"))
            painter.setFont(_body_font(BODY_PT + 1.5, bold=True))
            painter.drawText(QtCore.QRectF(0, logo_height + 1.0 * mm, page_width, 4.5 * mm),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, report.title)
            painter.setFont(_body_font(BODY_PT - 0.5))
            painter.setPen(QtGui.QColor("#2a6f97"))
            painter.drawText(QtCore.QRectF(0, logo_height + 5.2 * mm, page_width, 3.5 * mm),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, WEBSITE)
            painter.setPen(QtGui.QPen(QtGui.QColor("#9aa9b8"), max(1, int(0.2 * mm))))
            painter.drawLine(QtCore.QPointF(0, header - 1.2 * mm), QtCore.QPointF(page_width, header - 1.2 * mm))
            # Footer: website left, page number right.
            painter.setFont(_body_font(BODY_PT - 0.5))
            painter.setPen(QtGui.QColor("#666666"))
            footer_rect = QtCore.QRectF(0, page_height - footer, page_width, footer)
            painter.drawText(footer_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom, WEBSITE)
            painter.drawText(footer_rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom, f"{index + 1} / {total}")

        current = -1
        for block in blocks:
            if block["page"] != current:
                if current >= 0:
                    writer.newPage()
                current = block["page"]
                frame(current)
            y = header + block["top"]
            text = block.get("text")
            if text is not None:
                painter.save()
                painter.translate(0, y)
                text.drawContents(painter, QtCore.QRectF(0, 0, page_width, text.size().height()))
                painter.restore()
            if "image" in block:
                y += block["params_height"]
                target = QtCore.QRectF((page_width - figure_width) / 2, y, figure_width, block["image_height"])
                painter.drawImage(target, block["image"])
                if block["note"]:
                    painter.setFont(_body_font(BODY_PT - 0.5))
                    painter.setPen(QtGui.QColor("#555555"))
                    painter.drawText(QtCore.QRectF(target.left(), target.bottom(), figure_width, note_height),
                                     QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, block["note"])
    finally:
        painter.end()
    return drawn


def report_subtitle(sample_count: int, version: str) -> str:
    return f"生成时间：{datetime.now():%Y-%m-%d %H:%M}    样品数：{sample_count}    软件版本：{version}"


def fit_line(slope, intercept, xs) -> tuple[list[float], list[float]]:
    try:
        slope, intercept = float(slope), float(intercept)
    except (TypeError, ValueError):
        return [], []
    xs = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not xs or not (math.isfinite(slope) and math.isfinite(intercept)):
        return [], []
    grid = list(np.linspace(min(xs), max(xs), 50))
    return grid, [slope * x + intercept for x in grid]
