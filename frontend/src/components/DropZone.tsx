import React, { useRef, useState } from "react";
import { fmtBytes } from "../api";
import { Badge, Icon } from "./ui";

export async function entriesToFiles(items: DataTransferItemList): Promise<File[]> {
  const out: File[] = [];
  const walk = async (entry: any): Promise<void> => {
    if (!entry) return;
    if (entry.isFile) {
      await new Promise<void>((res) => entry.file((f: File) => { out.push(f); res(); }, () => res()));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch: any[] = [];
      do {
        batch = await new Promise<any[]>((res) => reader.readEntries(res, () => res([])));
        for (const e of batch) await walk(e);
      } while (batch.length);
    }
  };
  const entries = Array.from(items).map((i) => (i as any).webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return [];
  for (const e of entries) await walk(e);
  return out;
}

export interface Chip {
  key: string; name: string; pages?: number; size?: number; status?: "uploading" | "ready" | "error" | "processing";
  progress?: number; flags?: string[]; error?: string; extra?: React.ReactNode;
}

export function DropZone({
  index, title, subtitle, icon, required, optional, chips, onFiles, onRemove, onReorder, busy, footer, emptyHint,
}: {
  index: number; title: string; subtitle: string; icon: string; required?: boolean; optional?: boolean; chips: Chip[];
  onFiles: (files: File[]) => void; onRemove?: (key: string) => void; onReorder?: (from: number, to: number) => void;
  busy?: boolean; footer?: React.ReactNode; emptyHint?: string;
}) {
  const [over, setOver] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const depth = useRef(0);

  const accept = (files: File[]) => {
    const pdfs = files.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length) onFiles(pdfs);
  };

  return (
    <div
      className={`dz ${over ? "dz-over" : ""} ${chips.length ? "dz-has" : ""}`}
      onDragEnter={(e) => { e.preventDefault(); if (dragIdx === null) { depth.current++; setOver(true); } }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => { depth.current--; if (depth.current <= 0) { setOver(false); depth.current = 0; } }}
      onDrop={async (e) => {
        e.preventDefault(); depth.current = 0; setOver(false);
        if (dragIdx !== null) return;
        const viaEntries = await entriesToFiles(e.dataTransfer.items);
        accept(viaEntries.length ? viaEntries : Array.from(e.dataTransfer.files));
      }}
    >
      <div className="dz-head">
        <span className="dz-num">{index}</span>
        <div className="dz-titles">
          <h4>{title} {required && <Badge tone="brand">Required</Badge>}{optional && <Badge>Optional</Badge>}</h4>
          <p>{subtitle}</p>
        </div>
        <Icon name={icon} size={22} className="dz-icon" />
      </div>

      <div
        className="dz-target" role="button" tabIndex={0} aria-label={`Add files to ${title}`}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      >
        {busy ? <span className="spinner spinner-brand" /> : <Icon name="upload" size={20} />}
        <span><b>Drop PDFs or a folder</b> or <u>browse</u></span>
        <button type="button" className="link-btn" onClick={(e) => { e.stopPropagation(); folder.current?.click(); }}>folder…</button>
      </div>
      <input ref={input} type="file" accept="application/pdf,.pdf" multiple hidden
        onChange={(e) => { accept(Array.from(e.target.files || [])); e.target.value = ""; }} />
      <input ref={folder} type="file" multiple hidden {...({ webkitdirectory: "", directory: "" } as any)}
        onChange={(e) => { accept(Array.from(e.target.files || [])); e.target.value = ""; }} />

      {chips.length > 0 ? (
        <ul className="chips">
          {chips.map((c, i) => (
            <li key={c.key} className={`chip chip-${c.status || "ready"} ${dragIdx === i ? "chip-drag" : ""}`}
              draggable={!!onReorder}
              onDragStart={(e) => { setDragIdx(i); e.dataTransfer.effectAllowed = "move"; }}
              onDragEnd={() => setDragIdx(null)}
              onDragOver={(e) => { if (dragIdx !== null && dragIdx !== i && onReorder) { e.preventDefault(); onReorder(dragIdx, i); setDragIdx(i); } }}>
              {onReorder && <Icon name="grip" size={14} className="chip-grip" />}
              <Icon name="file" size={16} className="chip-file" />
              <div className="chip-main">
                <span className="chip-name mono" title={c.name}>{c.name}</span>
                <span className="chip-meta">
                  {c.status === "uploading" ? "Uploading…" : c.error ? <span className="t-bad">{c.error}</span> : (
                    <>
                      {c.pages !== undefined && <>{c.pages} pp</>}{c.size !== undefined && <> · {fmtBytes(c.size)}</>}
                      {c.flags?.includes("ocr_needed") && <> · <span className="t-warn">scanned - OCR will run</span></>}
                      {c.flags?.includes("has_annotations") && <> · has markups</>}
                    </>
                  )}
                  {c.extra}
                </span>
                {c.status === "processing" && <span className="chip-bar"><span style={{ width: `${Math.round((c.progress || 0) * 100)}%` }} /></span>}
              </div>
              {onRemove && <button className="icon-btn" onClick={() => onRemove(c.key)} aria-label={`Remove ${c.name}`}><Icon name="x" size={15} /></button>}
            </li>
          ))}
        </ul>
      ) : emptyHint ? <p className="dz-empty">{emptyHint}</p> : null}
      {footer}
    </div>
  );
}
