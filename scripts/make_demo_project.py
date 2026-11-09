"""Generate a fictional demo project for trying ASM without real project documents.

Creates in <out_dir>:
  Project Manual - Riverside MOB.pdf     (01 33 00, 08 11 13, 08 71 00, 09 91 23)
  Drawings - Riverside MOB.pdf           (A-001, A-501 door schedule, A-502, E-101 on 36x24 sheets)
  Addendum 01.pdf                         (modifies 08 71 00 2.6 and A-501)
  Submittal - Door Hardware Product Data.pdf   (contains deliberate deviations for testing)
  Example - 08 11 13-001-0 Hollow Metal Doors - Product Data.pdf
  Division 01 - 01 33 00 Submittal Procedures.pdf

All names, products and values are invented for testing.

Usage: python scripts/make_demo_project.py data/demo
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

LETTER = (612, 792)
ARCH_D = (2592, 1728)


def spec_pages(doc: pymupdf.Document, number: str, title: str, body: list[str]) -> None:
    """Write a spec section with real text lines; footer '<number> - n'."""
    lines = [f"SECTION {number} - {title.upper()}", ""] + body + ["", f"END OF SECTION {number}"]
    page = None
    y = 0.0
    n = 0

    def new_page():
        nonlocal page, y, n
        n += 1
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 40), "Riverside Medical Office Building   Project No. 2417", fontsize=8, color=(0.4, 0.4, 0.4))
        page.insert_text((240, 760), f"{title.upper()}    {number} - {n}", fontsize=8, color=(0.3, 0.3, 0.3))
        y = 72

    new_page()
    for line in lines:
        if y > 720:
            new_page()
        indent = 72
        if line[:2] in ("A.", "B.", "C.", "D.", "E.", "F.", "G.", "H."):
            indent = 96
        elif line[:2].strip() in ("1.", "2.", "3.", "4.", "5.", "6.") and not line[1:2].isdigit():
            indent = 120
        size = 10
        for chunk in _wrap(line, 88 if indent == 72 else 82):
            page.insert_text((indent, y), chunk, fontsize=size, fontname="helv")
            y += 13.5
        if not line:
            y += 2


def _wrap(s: str, n: int) -> list[str]:
    if not s:
        return [""]
    out, cur = [], ""
    for w in s.split(" "):
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    out.append(cur)
    return out


S013300 = [
    "PART 1 - GENERAL",
    "1.1 SUMMARY",
    "A. Section includes administrative and procedural requirements for submitting Shop Drawings, Product Data, Samples, and other submittals.",
    "1.2 SUBMITTAL FORMATS",
    "A. Submit electronic submittals as PDF files through the Project management website (Procore).",
    "B. File naming: name each file using the format [Section Number] - [Sequence].[Revision] [Section Title] - [Type Abbreviation]. Example: 08 71 00 - 03.0 Door Hardware - PD.",
    "C. Provide a transmittal cover sheet with Project name and number, Architect, Contractor, submittal number and revision, Specification Section number and title, date, and a list of items included.",
    "D. Product Data: mark each copy to show applicable products, models, options, and finishes. Cross out or delete information not applicable to the Project.",
    "E. Deviations: identify deviations from the Contract Documents on a separate deviation summary page and cloud each deviation on the submittal.",
    "F. Contractor's review: indicate Contractor's review and approval with a review stamp, signed and dated, before submittal to the Architect.",
    "G. Include the applicable Specification Section excerpt only when requested by the Architect.",
    "1.3 ARCHITECT'S ACTION",
    "A. Architect will review each submittal and return it within 10 working days.",
    "PART 2 - PRODUCTS (Not Used)",
    "PART 3 - EXECUTION (Not Used)",
]

S081113 = [
    "PART 1 - GENERAL",
    "1.1 SUMMARY",
    "A. Section includes hollow metal doors and frames.",
    "B. Related Requirements: Section 08 71 00 Door Hardware for hardware preparation.",
    "1.2 ACTION SUBMITTALS",
    "A. Product Data for each type of door and frame.",
    "PART 2 - PRODUCTS",
    "2.1 MANUFACTURERS",
    "A. Steelcraft, Curries, or Ceco.",
    "2.2 INTERIOR DOORS",
    "A. SDI A250.8 Level 2, 18 gauge, 1-3/4 inch thick. Doors over 36 inches wide shall be reinforced for heavy-weight hinges.",
    "PART 3 - EXECUTION",
    "3.1 INSTALLATION",
    "A. Install per SDI A250.11.",
]

S087100 = [
    "PART 1 - GENERAL",
    "1.1 SUMMARY",
    "A. Section includes commercial door hardware for swinging doors: hinges, closers, exit devices, electrified strikes, protective plates, and finishes.",
    "1.2 RELATED REQUIREMENTS",
    "A. Section 01 33 00 Submittal Procedures.",
    "B. Section 08 11 13 Hollow Metal Doors and Frames.",
    "C. Section 28 13 00 Access Control for electrified hardware coordination.",
    "1.3 ACTION SUBMITTALS",
    "A. Product Data: manufacturer's catalog cut sheets for each product, with selected model, function, finish, and options clearly indicated.",
    "B. Door hardware schedule organized by hardware set as indicated on Drawing A-501.",
    "C. Wiring diagrams for electrified hardware.",
    "1.4 QUALITY ASSURANCE",
    "A. Fire-rated openings: hardware shall be listed for use on labeled openings per NFPA 80 and UL 10C.",
    "PART 2 - PRODUCTS",
    "2.1 MANUFACTURERS",
    "A. Hinges: Ives, Hager, or Stanley. Closers: LCN or Norton. Exit devices: Von Duprin or Precision. Substitutions per Section 01 25 00 only.",
    "2.2 HINGES",
    "A. Five-knuckle, ball-bearing, ANSI/BHMA A156.1 Grade 1. Size: 4-1/2 x 4-1/2 inches for doors up to 36 inches wide; 5 x 4-1/2 inches heavy weight for doors over 36 inches wide.",
    "B. Non-removable pins at out-swinging exterior doors.",
    "2.3 DOOR CLOSERS",
    "A. Surface-mounted, ANSI/BHMA A156.4 Grade 1, cast-iron body, full rack and pinion, adjustable spring power sizes 1-6.",
    "B. Provide parallel-arm mounting with cushion stop (CUSH) where scheduled.",
    "C. Closers shall be tested to 10,000,000 cycles.",
    "2.4 EXIT DEVICES",
    "A. ANSI/BHMA A156.3 Grade 1, rim type, touchpad style, UL listed for fire exit hardware where used on fire-rated doors.",
    "B. Provide quiet electric latch retraction (QEL) where scheduled as electrified.",
    "2.5 ELECTRIFIED STRIKES",
    "A. ANSI/BHMA A156.31 Grade 1, fail-secure, 12/24 VDC field selectable, for hardware set 4.",
    "2.6 FINISHES",
    "A. Hinges, locksets and trim: BHMA 626 (US26D) satin chromium.",
    "B. Exit devices: BHMA 630 (US32D) satin stainless steel.",
    "C. Closers: BHMA 689 aluminum painted.",
    "2.7 PROTECTIVE PLATES",
    "A. Kick plates: 0.050 inch stainless steel, 10 inches high, 2 inches less than door width, beveled four edges.",
    "PART 3 - EXECUTION",
    "3.1 INSTALLATION",
    "A. Mount hardware at heights recommended by DHI. Install closers on the room side of corridor doors.",
    "3.2 ADJUSTING",
    "A. Adjust closers for opening force not exceeding 5 lbf for interior doors.",
]

S099123 = [
    "PART 1 - GENERAL",
    "1.1 SUMMARY",
    "A. Section includes interior painting of hollow metal doors and frames.",
    "PART 2 - PRODUCTS",
    "2.1 PAINT",
    "A. Waterborne acrylic semi-gloss, MPI #154.",
    "PART 3 - EXECUTION",
    "3.1 APPLICATION",
    "A. Do not paint door hardware.",
]


def make_spec(out: Path) -> None:
    doc = pymupdf.open()
    toc = doc.new_page(width=LETTER[0], height=LETTER[1])
    toc.insert_text((72, 90), "PROJECT MANUAL - TABLE OF CONTENTS", fontsize=13)
    for i, t in enumerate(["01 33 00 Submittal Procedures", "08 11 13 Hollow Metal Doors and Frames",
                           "08 71 00 Door Hardware", "09 91 23 Interior Painting"]):
        toc.insert_text((72, 130 + i * 18), t, fontsize=10)
    spec_pages(doc, "01 33 00", "Submittal Procedures", S013300)
    spec_pages(doc, "08 11 13", "Hollow Metal Doors and Frames", S081113)
    spec_pages(doc, "08 71 00", "Door Hardware", S087100)
    spec_pages(doc, "09 91 23", "Interior Painting", S099123)
    doc.save(out / "Project Manual - Riverside MOB.pdf")


def title_block(page: pymupdf.Page, number: str, title: str) -> None:
    W, H = page.rect.width, page.rect.height
    page.draw_rect(pymupdf.Rect(24, 24, W - 24, H - 24), width=2)
    tb = pymupdf.Rect(W - 400, H - 260, W - 24, H - 24)
    page.draw_rect(tb, width=1.5)
    page.insert_text((tb.x0 + 14, tb.y0 + 30), "RIVERSIDE MEDICAL OFFICE BUILDING", fontsize=14)
    page.insert_text((tb.x0 + 14, tb.y0 + 50), "Project No. 2417   Date: 2026-08-14   Scale: As noted", fontsize=9)
    page.insert_text((tb.x0 + 14, tb.y0 + 110), title.upper(), fontsize=20)
    page.insert_text((tb.x0 + 14, tb.y0 + 200), number, fontsize=48)


def make_drawings(out: Path) -> None:
    doc = pymupdf.open()
    p = doc.new_page(width=ARCH_D[0], height=ARCH_D[1])
    title_block(p, "A-001", "General Notes")
    for i, t in enumerate(["GENERAL NOTES", "1. Contractor shall verify all dimensions in field.",
                           "2. Refer to Project Manual for product requirements.",
                           "3. All doors over 36 inches wide receive heavy weight hinges per 08 71 00."]):
        p.insert_text((80, 120 + i * 30), t, fontsize=16 if i == 0 else 13)

    p = doc.new_page(width=ARCH_D[0], height=ARCH_D[1])
    title_block(p, "A-501", "Door Schedule")
    p.insert_text((80, 110), "DOOR SCHEDULE", fontsize=20)
    hdr = ["DOOR", "WIDTH", "HEIGHT", "TYPE", "FIRE RATING", "HW SET", "REMARKS"]
    rows = [["101", "3'-0\"", "7'-0\"", "HM", "-", "1", ""],
            ["102", "3'-6\"", "7'-0\"", "HM", "90 MIN", "2", "Corridor"],
            ["103", "3'-6\"", "7'-0\"", "HM", "90 MIN", "3", "Stair 1"],
            ["104", "3'-0\"", "7'-0\"", "HM", "-", "4", "Electrified, access control"],
            ["105", "3'-6\"", "7'-0\"", "HM", "-", "4", "Electrified, access control"]]
    xs = [80, 200, 320, 440, 540, 700, 820]
    for x, h in zip(xs, hdr):
        p.insert_text((x, 160), h, fontsize=13)
    for r_i, r in enumerate(rows):
        for x, v in zip(xs, r):
            p.insert_text((x, 195 + r_i * 30), v, fontsize=12)
    p.insert_text((80, 420), "HARDWARE SETS (see Section 08 71 00)", fontsize=16)
    sets = ["SET 1: 3 hinges, passage set, closer (REG arm), wall stop",
            "SET 2: 3 hinges HW, exit device (fire), closer (CUSH arm), kick plate",
            "SET 3: 3 hinges HW, exit device (fire), closer (CUSH arm), kick plate",
            "SET 4: 3 hinges, exit device with QEL, electrified strike, closer, card reader by 28 13 00"]
    for i, s in enumerate(sets):
        p.insert_text((80, 455 + i * 26), s, fontsize=12)

    p = doc.new_page(width=ARCH_D[0], height=ARCH_D[1])
    title_block(p, "A-502", "Door Details")
    p.insert_text((80, 120), "DETAIL 1 - HOLLOW METAL FRAME HEAD", fontsize=16)
    p.insert_text((80, 150), "DETAIL 2 - CLOSER MOUNTING, PARALLEL ARM WITH CUSHION STOP", fontsize=16)
    p.draw_rect(pymupdf.Rect(80, 200, 700, 700), width=1)

    p = doc.new_page(width=ARCH_D[0], height=ARCH_D[1])
    title_block(p, "E-101", "Power Plan Level 1")
    p.insert_text((80, 120), "POWER PLAN - LEVEL 1", fontsize=16)
    p.insert_text((80, 150), "Provide 24 VDC power supply for electrified door hardware at doors 104 and 105. Mag lock at door 104.", fontsize=12)
    doc.save(out / "Drawings - Riverside MOB.pdf")


def make_addendum(out: Path) -> None:
    doc = pymupdf.open()
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    lines = ["ADDENDUM NO. 01", "Riverside Medical Office Building - Project No. 2417", "Dated: September 2, 2026", "",
             "ITEM 1 - SPECIFICATION SECTION 08 71 00 DOOR HARDWARE",
             "Revise Article 2.6.B to read: Exit devices: BHMA 626 (US26D) satin chromium.",
             "", "ITEM 2 - DRAWING A-501 DOOR SCHEDULE", "Door 103: revise width to 3'-0\"."]
    for i, l in enumerate(lines):
        p.insert_text((72, 90 + i * 18), l, fontsize=11 if i else 14)
    doc.save(out / "Addendum 01.pdf")


CSS = "* {font-family: sans-serif;} h1{font-size:18pt;margin:0} h2{font-size:12pt;margin:8px 0 4px 0} td,th{border:1px solid #999;padding:3px 5px;font-size:9pt} table{border-collapse:collapse;width:100%} p{font-size:9.5pt;margin:3px 0}"


def html_page(doc: pymupdf.Document, html: str) -> None:
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_htmlbox(pymupdf.Rect(48, 48, LETTER[0] - 48, LETTER[1] - 48), html, css=CSS)


def make_submittal(out: Path) -> None:
    doc = pymupdf.open()
    html_page(doc, """<h1>NORTHSIDE DOOR SUPPLY</h1><p>Hardware Submittal Transmittal</p>
    <h2>Project: Riverside Medical Office Building</h2>
    <p>Submitted to: Ajax Building Company</p><p>Section 08 71 00 Door Hardware - Product Data</p>
    <p>Date: 2026-11-02</p><h2>Contents</h2>
    <table><tr><th>Item</th><th>Manufacturer</th><th>Model</th></tr>
    <tr><td>Hinges</td><td>Ives</td><td>5BB1 4.5 x 4.5 NRP</td></tr>
    <tr><td>Door closers</td><td>LCN</td><td>4040XP</td></tr>
    <tr><td>Exit devices</td><td>Von Duprin</td><td>99 Series</td></tr>
    <tr><td>Electrified strike</td><td>HES</td><td>1006 Series</td></tr>
    <tr><td>Kick plates</td><td>Ives</td><td>8400</td></tr></table>""")
    html_page(doc, """<h1>IVES 5BB1 HINGES</h1><p>Five knuckle ball bearing hinge, ANSI/BHMA A156.1 Grade 1.</p>
    <table><tr><th>Size</th><th>Gauge</th><th>Weight</th><th>Selected</th></tr>
    <tr><td>4.5 x 4.5</td><td>0.134</td><td>Standard</td><td>X</td></tr>
    <tr><td>5 x 4.5</td><td>0.146</td><td>Heavy (5BB1HW)</td><td></td></tr></table>
    <p>Finish: 626 (US26D) Satin Chromium. NRP option for exterior doors.</p>
    <p>Quantity: 3 per door, all doors 101-105.</p>""")
    html_page(doc, """<h1>LCN 4040XP SERIES DOOR CLOSER</h1>
    <p>Heavy duty surface closer. Certified ANSI/BHMA A156.4 Grade 1. Cast iron body, full rack and pinion.</p>
    <p>Cycle tested to 10,000,000 cycles. Spring power adjustable sizes 1-6.</p>
    <h2>Arm Options</h2><table><tr><th>Arm</th><th>Description</th><th>Selected</th></tr>
    <tr><td>REG</td><td>Regular arm</td><td>Set 1</td></tr><tr><td>CUSH</td><td>Parallel arm, cushion stop</td><td>Sets 2, 3</td></tr>
    <tr><td>EDA</td><td>Extra duty arm</td><td></td></tr><tr><td>SCUSH</td><td>Spring cushion arm</td><td></td></tr></table>
    <h2>Finish</h2><p>689 Aluminum Painted (selected). Optional: 695 Dark Bronze.</p>""")
    html_page(doc, """<h1>VON DUPRIN 99 SERIES EXIT DEVICE</h1>
    <p>Rim exit device, touchpad, ANSI/BHMA A156.3 Grade 1. UL listed fire exit hardware (99-F).</p>
    <p>Option: QEL quiet electric latch retraction, 24 VDC.</p>
    <h2>Finish</h2><p>Selected finish: 630 (US32D) Satin Stainless Steel.</p>
    <p>UL Listing: 3 hour fire rated. Note: UL listing 1.5 hour for 99-F with QEL option.</p>""")
    html_page(doc, """<h1>HES 1006 SERIES ELECTRIC STRIKE</h1>
    <p>ANSI/BHMA A156.31 Grade 1. Field selectable fail-secure / fail-safe. 12/24 VDC.</p>
    <p>For use with rim exit devices: 1006 with 1006-KD kit.</p>
    <p>Hardware set 4, doors 104 and 105.</p>""")
    html_page(doc, """<h1>IVES 8400 KICK PLATE</h1><p>0.050 inch stainless steel, beveled 4 edges.</p>
    <p>Finish 630 (US32D). Size: 2 inches less than door width.</p>""")
    doc.save(out / "Submittal - Door Hardware Product Data.pdf")


def make_example(out: Path) -> None:
    doc = pymupdf.open()
    html_page(doc, """<p style='color:#888'>AJAX BUILDING COMPANY | SUBMITTAL TRANSMITTAL</p>
    <h1>08 11 13 Hollow Metal Doors and Frames</h1><p>Submittal 08 11 13-001-0 &middot; Product Data</p>
    <table><tr><td>Project</td><td>Riverside Medical Office Building #2417</td></tr>
    <tr><td>Architect</td><td>Hale + Moreno Architects</td></tr><tr><td>Contractor</td><td>Ajax Building Company</td></tr>
    <tr><td>Date</td><td>2026-10-01</td></tr></table>
    <h2>Items Included</h2><table><tr><td>1</td><td>Steelcraft L-Series doors</td><td>3</td></tr>
    <tr><td>2</td><td>Steelcraft F-Series frames</td><td>5</td></tr></table>
    <h2>Deviations</h2><p>No deviations.</p>""")
    html_page(doc, "<h1>STEELCRAFT L-SERIES</h1><p>SDI A250.8 Level 2, 18 gauge.</p>")
    doc.save(out / "Example - 08 11 13-001-0 Hollow Metal Doors - Product Data.pdf")


def make_div01(out: Path) -> None:
    doc = pymupdf.open()
    spec_pages(doc, "01 33 00", "Submittal Procedures", S013300)
    doc.save(out / "Division 01 - 01 33 00 Submittal Procedures.pdf")


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    make_spec(out)
    make_drawings(out)
    make_addendum(out)
    make_submittal(out)
    make_example(out)
    make_div01(out)
    print(f"Demo project written to {out.resolve()}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/demo")
