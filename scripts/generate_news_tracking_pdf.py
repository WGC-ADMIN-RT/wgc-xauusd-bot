#!/usr/bin/env python
"""Generate a short member-facing PDF: what USD news WGC tracks for XAUUSD."""
from __future__ import annotations

import os
from datetime import datetime

from fpdf import FPDF

OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "WGC-USD-News-Tracking.pdf",
)


class GuidePDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 8, "Wings Gold Club - USD News for XAUUSD", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, "What we track from Forex Factory (red & orange USD only)", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, f"WGC XAUUSD Bot  |  Generated {datetime.now().strftime('%d %b %Y')}", align="C")

    def section(self, title: str) -> None:
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.cell(self.epw, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)

    def bullets(self, items: list[str]) -> None:
        self.set_font("Helvetica", "", 9)
        for item in items:
            self.set_x(self.l_margin)
            self.multi_cell(self.epw, 4.5, f"  -  {item}")
        self.ln(1)


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pdf = GuidePDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()

    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        pdf.epw, 4.5,
        "Daily outlook, pre-news alerts, and post-release breakdowns use Forex Factory "
        "as the source. Only USD events with red (high) or orange (medium) impact are "
        "considered. A second filter keeps releases that matter for gold traders.",
    )
    pdf.ln(2)

    pdf.section("Tracked")
    pdf.bullets([
        "Inflation: CPI, PPI, PCE, Core PCE",
        "Jobs: NFP, ADP, unemployment rate, jobless claims, JOLTS",
        "Growth: GDP, retail sales, trade balance",
        "PMI / ISM: manufacturing & services PMI, Flash PMI, regional Fed surveys",
        "Industry: durable goods, factory orders, industrial production",
        "Sentiment: consumer confidence, UoM / Michigan sentiment",
        "Housing: starts, permits, new / existing / pending home sales",
        "Fed & rates: FOMC, rate decisions, minutes, Fed Chair Powell, FOMC press",
        "President Trump speeches (FF orange/red)",
        "Flash Manufacturing + Flash Services PMI at same time (Composite PMI dropped)",
    ])

    pdf.section("Dropped (even if FF shows red/orange)")
    pdf.bullets([
        "Other political speeches (e.g. Biden, Treasury officials)",
        "Routine Fed member speeches (non-Chair)",
        "Energy: crude oil, natural gas, gasoline inventories, rig counts",
        "Treasury note/bond/bill auctions",
        "MBA mortgage data, Fed stress tests, beige book, balance sheet / M2",
        "Current account, minor inventories, consumer credit, vehicle sales",
        "Agricultural reports (WASDE, crops)",
        "Any other USD event not on the gold-relevant list above",
    ])

    pdf.section("Automation schedule (SGT)")
    pdf.bullets([
        "12:00 PM - USD news outlook (next 24 hours)",
        "1 hour & 15 minutes before each tracked release - warnings",
        "After release - actual vs forecast breakdown + XAUUSD read",
        "2:30 PM - XAUUSD intraday plan (separate from news calendar)",
    ])

    pdf.section("Risk note")
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        pdf.epw, 4.5,
        "High-impact news can widen spreads, cause slippage, and fake breakouts. "
        "Signals pause near major releases. This list follows Forex Factory folders "
        "plus WGC gold-trader relevance rules.",
    )

    pdf.output(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
