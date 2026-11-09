"""Build the Bluebeam compatibility gate PDF (every markup type, upright + rotated page).

Usage: python scripts/make_bluebeam_gate.py [out.pdf]
Then follow docs/BLUEBEAM_GATE.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from asm.pdf.gate import build_gate_pdf  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "bluebeam_gate.pdf")
inv = build_gate_pdf(out)
print(f"Wrote {out} with {len(inv)} annotations:")
for a in inv:
    print(f"  p{a['page']} {a['type']:<10} {a['subject']:<14} NM={a['nm']:<8} layer={'internal' if a['oc'] else 'A/E'}")
