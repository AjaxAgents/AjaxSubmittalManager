import React, { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./ui";

export interface Opt { value: string; label: string; sub?: string; group?: string }

function useOutside(ref: React.RefObject<HTMLElement>, on: boolean, close: () => void) {
  useEffect(() => {
    if (!on) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) close(); };
    const k = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [on]);
}

/** Text input with a filtered suggestion list. Free text is allowed; picking an option calls onPick. */
export function Combobox({ value, onChange, onPick, options, placeholder, mono, ariaLabel, emptyText, onCreate }: {
  value: string; onChange: (v: string) => void; onPick: (o: Opt) => void; options: Opt[]; placeholder?: string; mono?: boolean;
  ariaLabel: string; emptyText?: string; onCreate?: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  const q = value.trim().toLowerCase();
  const list = useMemo(() => {
    const f = q ? options.filter((o) => `${o.value} ${o.label} ${o.sub || ""}`.toLowerCase().includes(q)) : options;
    return f.slice(0, 80);
  }, [q, options]);
  const exact = options.some((o) => o.label.toLowerCase() === q || o.value.toLowerCase() === q);
  let lastGroup = "";
  return (
    <div className="combo" ref={ref}>
      <input className={`input ${mono ? "mono" : ""}`} value={value} placeholder={placeholder} aria-label={ariaLabel}
        role="combobox" aria-expanded={open} aria-autocomplete="list"
        onChange={(e) => { onChange(e.target.value); setOpen(true); setHi(0); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { setOpen(true); setHi((h) => Math.min(h + 1, list.length - 1)); e.preventDefault(); }
          if (e.key === "ArrowUp") { setHi((h) => Math.max(h - 1, 0)); e.preventDefault(); }
          if (e.key === "Enter" && open && list[hi]) { onPick(list[hi]); setOpen(false); e.preventDefault(); }
        }} />
      <Icon name="down" size={15} className="combo-caret" />
      {open && (list.length > 0 || (onCreate && q && !exact)) && (
        <div className="combo-list" role="listbox">
          {list.map((o, i) => {
            const head = o.group && o.group !== lastGroup ? (lastGroup = o.group, <div className="combo-group" key={`g-${o.group}`}>{o.group}</div>) : null;
            return (
              <React.Fragment key={o.value + i}>
                {head}
                <button type="button" role="option" aria-selected={i === hi} className={`combo-opt ${i === hi ? "hi" : ""}`}
                  onMouseEnter={() => setHi(i)} onClick={() => { onPick(o); setOpen(false); }}>
                  <span className={mono ? "mono" : ""}>{o.label}</span>{o.sub && <span className="combo-sub">{o.sub}</span>}
                </button>
              </React.Fragment>
            );
          })}
          {onCreate && q && !exact && (
            <button type="button" className="combo-opt combo-create" onClick={() => { onCreate(value.trim()); setOpen(false); }}>
              <Icon name="plus" size={14} /> Add "{value.trim()}"
            </button>
          )}
        </div>
      )}
      {open && !list.length && !onCreate && emptyText && <div className="combo-list"><div className="combo-empty">{emptyText}</div></div>}
    </div>
  );
}

/** Compact dropdown that picks one or several library files. */
export function FilePicker({ options, selected, onChange, multiple, placeholder, emptyText, footer }: {
  options: Opt[]; selected: string[]; onChange: (v: string[]) => void; multiple?: boolean; placeholder: string; emptyText: string;
  footer?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  const chosen = options.filter((o) => selected.includes(o.value));
  const label = chosen.length === 0 ? placeholder : chosen.length === 1 ? chosen[0].label : `${chosen.length} files`;
  return (
    <div className="fpick" ref={ref}>
      <button type="button" className={`fpick-btn ${chosen.length ? "has" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="fpick-label">{label}</span><Icon name="down" size={15} />
      </button>
      {open && (
        <div className="combo-list fpick-list">
          {!options.length && <div className="combo-empty">{emptyText}</div>}
          {!multiple && options.length > 0 && (
            <button type="button" className={`combo-opt ${!selected.length ? "hi" : ""}`} onClick={() => { onChange([]); setOpen(false); }}>
              <span>{placeholder}</span>
            </button>
          )}
          {options.map((o) => {
            const on = selected.includes(o.value);
            return (
              <button type="button" key={o.value} className={`combo-opt ${on ? "on" : ""}`} onClick={() => {
                if (multiple) onChange(on ? selected.filter((x) => x !== o.value) : [...selected, o.value]);
                else { onChange([o.value]); setOpen(false); }
              }}>
                <span className={`tick ${on ? "on" : ""}`}>{on && <Icon name="check" size={12} />}</span>
                <span className="mono small">{o.label}</span>{o.sub && <span className="combo-sub">{o.sub}</span>}
              </button>
            );
          })}
          {footer && <div className="fpick-foot">{footer}</div>}
        </div>
      )}
    </div>
  );
}

/** Topbar project switcher: a button that opens a type-to-filter list of projects. */
export function ProjectSwitcher({ projects, current, onPick }: {
  projects: { id: string; name: string; number?: string; ae_firm?: string }[]; current: string | null; onPick: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, open, () => setOpen(false));
  useEffect(() => { if (open) { setQ(""); setHi(0); } }, [open]);
  const cur = projects.find((p) => p.id === current);
  const ql = q.trim().toLowerCase();
  const list = ql ? projects.filter((p) => `${p.number || ""} ${p.name} ${p.ae_firm || ""}`.toLowerCase().includes(ql)) : projects;
  const rows: (null | typeof projects[number])[] = ql ? list : [null, ...list];
  const pick = (id: string | null) => { onPick(id); setOpen(false); };
  return (
    <div className="pswitch" ref={ref}>
      <button type="button" className="pswitch-btn" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="listbox" aria-label="Switch project">
        <Icon name="building" size={16} />
        <span className="pswitch-cur">{cur ? <>{cur.number && <span className="pswitch-num">{cur.number}</span>}{cur.name}</> : "All projects"}</span>
        <Icon name="down" size={14} />
      </button>
      {open && (
        <div className="pswitch-pop">
          <div className="pswitch-search">
            <Icon name="search" size={15} />
            <input autoFocus value={q} placeholder="Type a project name or number" aria-label="Find a project" role="combobox" aria-expanded="true"
              onChange={(e) => { setQ(e.target.value); setHi(0); }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { setHi((h) => Math.min(h + 1, rows.length - 1)); e.preventDefault(); }
                if (e.key === "ArrowUp") { setHi((h) => Math.max(h - 1, 0)); e.preventDefault(); }
                if (e.key === "Enter" && rows[hi] !== undefined) { pick(rows[hi]?.id ?? null); e.preventDefault(); }
              }} />
          </div>
          <div className="pswitch-list" role="listbox">
            {rows.map((p, i) => (
              <button key={p?.id || "all"} type="button" role="option" aria-selected={i === hi}
                className={`pswitch-opt ${i === hi ? "hi" : ""} ${(p?.id ?? null) === current ? "cur" : ""}`}
                onMouseEnter={() => setHi(i)} onClick={() => pick(p?.id ?? null)}>
                {p ? <><span className="pswitch-num mono">{p.number || "-"}</span><span className="pswitch-name">{p.name}</span></>
                  : <span className="pswitch-name muted">All projects</span>}
              </button>
            ))}
            {ql && !list.length && <div className="combo-empty">No project matches "{q}"</div>}
          </div>
        </div>
      )}
    </div>
  );
}

export function SortSelect({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="sort-sel"><span>Sort by</span>
      <select className="input input-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
