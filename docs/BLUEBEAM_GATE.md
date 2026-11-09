# Bluebeam compatibility gate

Run after any change to `backend/asm/pdf/markup.py` (spec V2 section 8).

1. `make gate` -> `data/bluebeam_gate.pdf` (page 1 upright, page 2 rotated 90 degrees).
2. Open it in Bluebeam Revu and open the **Markups List**.
3. For every markup on both pages confirm:

| Check | Pass |
| --- | --- |
| Appears in the Markups List with Author `Ajax ASM` (or `Ajax ASM (internal)`), the right Subject and Comment | |
| Can be selected, moved and resized | |
| Can be restyled (color, line width) from Properties | |
| Text boxes / callouts: text is editable in place; callout leader moves with the box | |
| Cloud shows as a revision cloud (Polygon with cloudy border) | |
| Internal markups (check mark, amber cloud) sit on layer **ASM Internal Review**, hidden by default, and toggle from the Layers panel | |
| Review stamp text is editable; signature/date are blank | |
| Save, close, reopen: everything above still holds | |

4. Record the result below.

| Date | Revu version | Result | Notes |
| --- | --- | --- | --- |
| | | | |

Expected markup types: Highlight, Underline, StrikeOut, Ink (X-out, check), Square, Circle, Polygon cloud,
Line arrow, FreeText callout, FreeText text box, Text (sticky note), FreeText review stamp.
