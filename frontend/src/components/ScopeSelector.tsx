import React, { useEffect, useMemo, useRef, useState } from "react";
import type { ScopeItem, TreeNode } from "../api";
import { Badge, Button, Icon } from "./ui";

type Flat = TreeNode & { depth: number; path: string[]; parentIds: string[] };

function flatten(nodes: TreeNode[], depth = 0, path: string[] = [], parents: string[] = []): Flat[] {
  const out: Flat[] = [];
  for (const n of nodes) {
    out.push({ ...n, depth, path, parentIds: parents });
    out.push(...flatten(n.children || [], depth + 1, [...path, n.label], [...parents, n.id]));
  }
  return out;
}
const selectable = (k: string) => ["spec_section", "spec_part", "spec_article", "drawing_sheet", "amendment", "document"].includes(k);

function parsePages(s: string, lo: number, hi: number): number[] | null {
  const out = new Set<number>();
  for (const part of s.split(",").map((x) => x.trim()).filter(Boolean)) {
    const m = part.match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (m) { for (let i = +m[1]; i <= +m[2]; i++) if (i >= lo && i <= hi) out.add(i); }
    else if (/^\d+$/.test(part) && +part >= lo && +part <= hi) out.add(+part);
    else return null;
  }
  return out.size ? Array.from(out).sort((a, b) => a - b) : null;
}

export function ScopeSelector({
  tree, items, onChange, mode, onMode, designated, loading, source, onReset, footer,
}: {
  tree: TreeNode[]; items: ScopeItem[]; onChange: (items: ScopeItem[]) => void; mode: string; onMode: (m: string) => void;
  designated: string; loading?: boolean; source?: string; onReset?: () => void; footer?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["GROUP:SPECS", "GROUP:DWGS", "GROUP:AMEND", "GROUP:DOCS"]));
  useEffect(() => {
    if (designated) setExpanded((s) => new Set([...Array.from(s), `DIV:${designated.slice(2, 4)}`]));
  }, [designated]);
  const [editing, setEditing] = useState<string | null>(null);
  const [warn, setWarn] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  const flat = useMemo(() => flatten(tree), [tree]);
  const byId = useMemo(() => Object.fromEntries(flat.map((f) => [f.id, f])), [flat]);
  const sel = useMemo(() => new Set(items.map((i) => i.id)), [items]);

  useEffect(() => {
    const h = (e: MouseEvent) => { if (open && panel.current && !panel.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const covered = (f: Flat) => f.parentIds.some((p) => sel.has(p));
  const toggle = (f: Flat) => {
    if (f.id === designated) return;
    if (sel.has(f.id)) {
      if (f.amended_by?.length) setWarn(`${f.number || f.label} is modified by ${f.amended_by.join(", ").replace(/AM:/g, "")} - the amendment may change the requirement.`);
      onChange(items.filter((i) => i.id !== f.id));
    } else {
      const add: ScopeItem[] = [{ id: f.id, reason: "Added by reviewer" }];
      for (const am of f.amended_by || []) if (!sel.has(am) && byId[am]) add.push({ id: am, reason: `Amends ${f.number}` });
      onChange([...items.filter((i) => !(byId[i.id]?.parentIds || []).includes(f.id)), ...add]);
      setWarn("");
    }
  };

  const ql = q.trim().toLowerCase();
  const visible = flat.filter((f) => {
    if (ql) return selectable(f.kind) && `${f.label} ${f.number || ""}`.toLowerCase().includes(ql);
    return f.parentIds.every((p) => expanded.has(p));
  });

  const total = items.reduce((s, i) => s + (i.pages?.length || byId[i.id]?.pages || 0), 0);
  const editingNode = editing ? byId[editing] : null;
  const editingItem = editing ? items.find((i) => i.id === editing) : null;

  return (
    <div className="scope">
      <div className="scope-bar">
        <div className="scope-pick" ref={panel}>
          <button className={`scope-trigger ${open ? "is-open" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
            <Icon name="search" size={16} />
            <span>{items.length ? `Add or remove references (${items.length} selected, ${total} pages)` : "Search spec sections, articles, drawing sheets..."}</span>
            <Icon name="chevron" size={16} className={open ? "rot90" : ""} />
          </button>
          {open && (
            <div className="scope-panel">
              <div className="scope-search">
                <Icon name="search" size={15} />
                <input autoFocus placeholder='Filter by number, title or keyword ("closer", "A-501")' value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              <div className="scope-tree">
                {!tree.length && <p className="muted pad">The project library is empty. Add specs and drawings in zone 2.</p>}
                {visible.map((f) => {
                  const hasKids = f.children?.length > 0;
                  const isOpen = expanded.has(f.id) || !!ql;
                  const can = selectable(f.kind);
                  const implied = covered(f);
                  return (
                    <div key={f.id} className={`tn tn-${f.kind}`} style={{ paddingLeft: 8 + (ql ? 0 : f.depth * 16) }}>
                      {hasKids && !ql ? (
                        <button className="tn-caret" onClick={() => setExpanded((s) => { const n = new Set(s); n.has(f.id) ? n.delete(f.id) : n.add(f.id); return n; })} aria-label="Expand">
                          <Icon name="chevron" size={14} className={isOpen ? "rot90" : ""} />
                        </button>
                      ) : <span className="tn-caret" />}
                      {can ? (
                        <label className="tn-label">
                          <input type="checkbox" checked={sel.has(f.id) || implied} disabled={implied || f.id === designated} onChange={() => toggle(f)} />
                          <span className={f.kind === "spec_section" || f.kind === "drawing_sheet" ? "mono-num" : ""}>{f.label}</span>
                          {f.id === designated && <Icon name="lock" size={13} className="muted" />}
                          {!!f.amended_by?.length && <Badge tone="warn" title={`Amended by ${f.amended_by.join(", ")}`}>amended</Badge>}
                          {f.pages ? <span className="tn-pages">{f.pages} pp</span> : null}
                        </label>
                      ) : <span className="tn-group">{f.label}</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
        <div className="seg" role="radiogroup" aria-label="Scope mode">
          {["flexible", "strict"].map((m) => (
            <button key={m} role="radio" aria-checked={mode === m} className={mode === m ? "on" : ""} onClick={() => onMode(m)}
              title={m === "flexible" ? "Selected items, plus up to 20 extra pages agents request (each logged)" : "Selected items only; anything else becomes a Human Review item"}>
              {m === "flexible" ? "Flexible" : "Strict"}
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="muted small"><span className="spinner spinner-brand" /> Suggesting reference scope from the catalog…</p>}
      {warn && <p className="scope-warn"><Icon name="alert" size={15} /> {warn}</p>}

      <div className="scope-chips">
        {items.map((i) => {
          const n = byId[i.id];
          const pages = i.pages?.length || n?.pages || 0;
          return (
            <span key={i.id} className={`schip ${i.id === designated ? "schip-lock" : ""} ${editing === i.id ? "schip-active" : ""}`} title={i.reason || ""}>
              <button className="schip-main" onClick={() => setEditing(editing === i.id ? null : i.id)}>
                {i.id === designated && <Icon name="lock" size={12} />}
                <span className="mono-num">{n ? (n.kind === "spec_article" || n.kind === "spec_part" ? `${n.number}` : n.number || n.label) : i.id.split(":").slice(1).join(":")}</span>
                <span className="schip-title">{n?.title || ""}</span>
                <span className="schip-pages">{pages}pp{i.pages?.length ? "*" : ""}</span>
              </button>
              {i.id !== designated && <button className="schip-x" aria-label="Remove" onClick={() => n ? toggle(n) : onChange(items.filter((x) => x.id !== i.id))}><Icon name="x" size={12} /></button>}
            </span>
          );
        })}
        {source && <span className="scope-src">{source === "preset" ? "Saved references for this section" : "Suggested from the project catalog"}{onReset && source === "preset" && <> · <button className="link-btn" onClick={onReset}>Reset to suggested</button></>}</span>}
      </div>

      {editingNode && editingItem && editingNode.page_start && (
        <PageTrim node={editingNode} item={editingItem} onSave={(pages) => { onChange(items.map((x) => x.id === editingItem.id ? { ...x, pages } : x)); }} onClose={() => setEditing(null)} />
      )}
      {footer}
    </div>
  );
}

function PageTrim({ node, item, onSave, onClose }: { node: Flat; item: ScopeItem; onSave: (p: number[] | null) => void; onClose: () => void }) {
  const lo = node.page_start || 1, hi = node.page_end || lo;
  const all = Array.from({ length: Math.min(hi - lo + 1, 40) }, (_, k) => lo + k);
  const current = new Set(item.pages || all);
  const [txt, setTxt] = useState(item.pages ? "" : "");
  const set = (p: number[]) => onSave(p.length === all.length ? null : p);
  return (
    <div className="trim">
      <div className="trim-head">
        <b>{node.label}</b><span className="muted"> · source pages {lo}-{hi}. Untick pages to exclude them.</span>
        <span className="grow" />
        <input className="input input-sm mono" placeholder="e.g. 4-9, 12" value={txt} onChange={(e) => setTxt(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { const p = parsePages(txt, lo, hi); if (p) set(p); } }} />
        <Button size="sm" onClick={() => { const p = parsePages(txt, lo, hi); if (p) set(p); }}>Apply range</Button>
        <Button size="sm" variant="ghost" onClick={() => onSave(null)}>All pages</Button>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" size={15} /></button>
      </div>
      {(node as any).file_id && (
        <div className="trim-thumbs">
          {all.map((p) => (
            <button key={p} className={`thumb ${current.has(p) ? "on" : "off"}`}
              onClick={() => { const n = new Set(current); n.has(p) ? n.delete(p) : n.add(p); set(Array.from(n).sort((a, b) => a - b)); }}>
              <img loading="lazy" src={`/api/library/${(node as any).file_id}/pages/${p}.png?dpi=30`} alt={`Page ${p}`} />
              <span>{current.has(p) ? <Icon name="check" size={12} /> : null} p.{p}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
