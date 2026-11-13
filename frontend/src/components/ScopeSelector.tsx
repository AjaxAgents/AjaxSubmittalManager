import React, { useEffect, useMemo, useState } from "react";
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
const LEAF = ["spec_section", "drawing_sheet", "amendment", "document"];
const natural = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });

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

type View = "included" | "suggested" | "all" | "spec_section" | "drawing_sheet" | "amendment" | "document";

/**
 * Project information the review may read. One left-aligned list with views (included, suggested, by type);
 * search covers everything, and "Select all" / "Deselect all" act on whatever the list is showing.
 */
export function ScopeSelector({
  tree, items, suggestions = [], onChange, mode, onMode, designated, loading, source, onReset,
}: {
  tree: TreeNode[]; items: ScopeItem[]; suggestions?: ScopeItem[]; onChange: (items: ScopeItem[]) => void; mode: string; onMode: (m: string) => void;
  designated: string; loading?: boolean; source?: string; onReset?: () => void;
}) {
  const [q, setQ] = useState("");
  const [view, setView] = useState<View>("included");
  const [editing, setEditing] = useState<string | null>(null);
  const [warn, setWarn] = useState("");
  const flat = useMemo(() => flatten(tree), [tree]);
  const byId = useMemo(() => Object.fromEntries(flat.map((f) => [f.id, f])), [flat]);
  const leaves = useMemo(() => flat.filter((f) => LEAF.includes(f.kind)).sort((a, b) => natural(a.number || a.label, b.number || b.label)), [flat]);
  const sel = useMemo(() => new Set(items.map((i) => i.id)), [items]);
  const suggestedIds = useMemo(() => suggestions.map((s) => s.id).filter((id) => byId[id]), [suggestions, byId]);
  const reasonOf = useMemo(() => Object.fromEntries([...suggestions, ...items].map((i) => [i.id, i.reason || ""])), [suggestions, items]);
  const counts = useMemo(() => leaves.reduce<Record<string, number>>((c, l) => { c[l.kind] = (c[l.kind] || 0) + 1; return c; }, {}), [leaves]);

  // typing searches everything; clearing the box goes back to what is included
  useEffect(() => { if (q.trim() && (view === "included" || view === "suggested")) setView("all"); if (!q.trim() && view === "all") setView("included"); }, [q]);

  const pagesOf = (i: ScopeItem) => i.pages?.length || byId[i.id]?.pages || 0;
  const totalPages = items.reduce((s, i) => s + pagesOf(i), 0);
  const ql = q.trim().toLowerCase();
  const match = (n: Flat) => !ql || `${n.number || ""} ${n.title || ""} ${n.label}`.toLowerCase().includes(ql);

  const shown: Flat[] = useMemo(() => {
    let rows: Flat[];
    if (view === "included") {
      rows = items.map((i) => byId[i.id]).filter(Boolean);
      rows.sort((a, b) => (a.id === designated ? -1 : b.id === designated ? 1 : natural(a.number || a.label, b.number || b.label)));
    } else if (view === "suggested") rows = suggestedIds.map((id) => byId[id]);
    else if (view === "all") rows = leaves;
    else rows = leaves.filter((l) => l.kind === view);
    return rows.filter(match);
  }, [view, items, suggestedIds, leaves, ql, byId, designated]);

  const withAmendments = (ids: string[]): ScopeItem[] => {
    const add: ScopeItem[] = [];
    const have = new Set(sel);
    for (const id of ids) {
      if (have.has(id)) continue;
      add.push({ id, reason: reasonOf[id] || "Added by reviewer" }); have.add(id);
      for (const am of byId[id]?.amended_by || []) if (!have.has(am) && byId[am]) { add.push({ id: am, reason: `Amends ${byId[id].number || byId[id].label}` }); have.add(am); }
    }
    return add;
  };
  const toggle = (n: Flat) => {
    if (n.id === designated) return;
    if (sel.has(n.id)) {
      if (n.amended_by?.length) setWarn(`${n.number || n.label} is modified by ${n.amended_by.join(", ").replace(/AM:/g, "")}. The amendment stays included.`);
      onChange(items.filter((i) => i.id !== n.id));
      if (editing === n.id) setEditing(null);
    } else { onChange([...items, ...withAmendments([n.id])]); setWarn(""); }
  };
  const selectable = shown.filter((n) => n.id !== designated);
  const nOn = selectable.filter((n) => sel.has(n.id)).length;
  const selectAll = () => { onChange([...items, ...withAmendments(selectable.map((n) => n.id))]); setWarn(""); };
  const deselectAll = () => { const drop = new Set(selectable.map((n) => n.id)); onChange(items.filter((i) => !drop.has(i.id))); setEditing(null); };

  const views: [View, string, number][] = [
    ["included", "Included", items.length], ["suggested", "Suggested", suggestedIds.filter((id) => !sel.has(id)).length],
    ["spec_section", "Specifications", counts.spec_section || 0], ["drawing_sheet", "Drawings", counts.drawing_sheet || 0],
    ["amendment", "Amendments", counts.amendment || 0], ["document", "Documents", counts.document || 0],
  ];
  const label = view === "all" ? `Matches for "${q.trim()}"` : views.find(([v]) => v === view)?.[1] || "";
  const kindLabel: Record<string, string> = { spec_section: "Spec", drawing_sheet: "Sheet", amendment: "Amendment", document: "Document", spec_part: "Part", spec_article: "Article" };
  const editingNode = editing ? byId[editing] : null;
  const editingItem = editing ? items.find((i) => i.id === editing) : null;

  return (
    <div className="scope2">
      <div className="scope2-bar">
        <div className="search scope2-search">
          <Icon name="search" size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder='Search all project information: number, title or keyword ("concrete", "S-301")' aria-label="Search project information" />
          {q && <button className="icon-btn" aria-label="Clear search" onClick={() => setQ("")}><Icon name="x" size={14} /></button>}
        </div>
        <div className="seg" role="radiogroup" aria-label="Reference scope">
          {["flexible", "strict"].map((m) => (
            <button key={m} role="radio" aria-checked={mode === m} className={mode === m ? "on" : ""} onClick={() => onMode(m)}
              title={m === "flexible" ? "Included items, plus up to 20 extra pages the reviewer asks for (each one logged)" : "Included items only; anything else becomes a Human Review item"}>
              {m === "flexible" ? "Flexible" : "Strict"}
            </button>
          ))}
        </div>
      </div>

      <div className="pills" role="radiogroup" aria-label="Show">
        {views.map(([v, l, n]) => (
          <button key={v} role="radio" aria-checked={view === v} className={`pill ${view === v ? "on" : ""}`} onClick={() => setView(v)} disabled={v !== "included" && v !== "suggested" && !n}>
            {l}<span className="pill-n">{n}</span>
          </button>
        ))}
      </div>

      <div className="scope2-head">
        <span><b>{label}</b> <span className="muted">{shown.length} item{shown.length === 1 ? "" : "s"}{view !== "included" && shown.length ? `, ${nOn} included` : ""}</span></span>
        <span className="grow" />
        {selectable.length > 0 && nOn < selectable.length && <Button size="sm" variant="ghost" icon="check" onClick={selectAll}>{view === "suggested" ? "Include all" : "Select all"}{selectable.length - nOn > 1 ? ` (${selectable.length - nOn})` : ""}</Button>}
        {nOn > 0 && <Button size="sm" variant="ghost" icon="x" onClick={deselectAll}>{view === "included" && !ql ? "Remove all" : "Deselect all"}</Button>}
      </div>

      {loading && <p className="muted small"><span className="spinner spinner-brand" /> Finding related references in the project library…</p>}
      {warn && <p className="scope-warn"><Icon name="alert" size={15} /> {warn}</p>}

      <div className="scope2-list" role="list">
        {shown.map((n) => {
          const on = sel.has(n.id);
          const lock = n.id === designated;
          const item = items.find((i) => i.id === n.id);
          const reason = reasonOf[n.id];
          return (
            <div key={n.id} role="listitem" className={`srow ${on ? "on" : ""} ${editing === n.id ? "editing" : ""}`}>
              <label className="srow-main">
                <input type="checkbox" checked={on} disabled={lock} onChange={() => toggle(n)} aria-label={`${on ? "Remove" : "Include"} ${n.number || ""} ${n.title || n.label}`} />
                <span className="srow-num">{lock ? <Icon name="lock" size={12} /> : null}{n.number || ""}</span>
                <span className="srow-title">
                  <span className="srow-t">{n.title || n.label}</span>
                  {(reason || view === "all") && <span className="srow-why">{view === "all" || view === "included" ? <span className="srow-kind">{kindLabel[n.kind] || ""}</span> : null}{reason}</span>}
                </span>
              </label>
              {!!n.amended_by?.length && <Badge tone="warn" title={`Amended by ${n.amended_by.join(", ")}`}>amended</Badge>}
              {on && n.page_start ? (
                <button className="srow-pp" onClick={() => setEditing(editing === n.id ? null : n.id)} title="Choose which pages to include">
                  {item ? pagesOf(item) : n.pages} pp{item?.pages?.length ? "*" : ""}
                </button>
              ) : <span className="srow-pp muted">{n.pages ? `${n.pages} pp` : ""}</span>}
            </div>
          );
        })}
        {!shown.length && !loading && (
          <p className="empty small">
            {view === "included" ? "Nothing included yet. Search above or open Suggested." : view === "suggested" ? "No other suggestions for this submittal." : ql ? `Nothing matches "${q.trim()}".` : "Nothing here."}
          </p>
        )}
      </div>

      {editingNode && editingItem && editingNode.page_start && (
        <PageTrim node={editingNode} item={editingItem} onSave={(pages) => { onChange(items.map((x) => x.id === editingItem.id ? { ...x, pages } : x)); }} onClose={() => setEditing(null)} />
      )}

      <p className="scope2-foot muted small">
        Included: <b>{items.length}</b> item{items.length === 1 ? "" : "s"}, <b>{totalPages}</b> pages.
        {source && <> {source === "preset" ? "Saved references for this section." : "Suggested from the project library."}</>}
        {onReset && <> <button className="link-btn" onClick={onReset}>Suggest again</button></>}
      </p>
    </div>
  );
}

function PageTrim({ node, item, onSave, onClose }: { node: Flat; item: ScopeItem; onSave: (p: number[] | null) => void; onClose: () => void }) {
  const lo = node.page_start || 1, hi = node.page_end || lo;
  const all = Array.from({ length: Math.min(hi - lo + 1, 40) }, (_, k) => lo + k);
  const current = new Set(item.pages || all);
  const [txt, setTxt] = useState("");
  const set = (p: number[]) => onSave(p.length === all.length ? null : p);
  return (
    <div className="trim">
      <div className="trim-head">
        <b>{node.number} {node.title || node.label}</b><span className="muted"> source pages {lo}-{hi}. Untick pages to leave them out.</span>
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
