import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./ui";

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const MAX_PAGES = 120;

/** Reads a page range of a library PDF as rendered pages, with zoom and a full-screen pop-up. */
export function PageViewer({ fileId, start, end, title, sub, pdfUrl, fullUrl, onClose, children }: {
  fileId: string; start: number; end: number; title: React.ReactNode; sub?: React.ReactNode; pdfUrl?: string; fullUrl?: string;
  onClose?: () => void; children?: React.ReactNode;
}) {
  const [full, setFull] = useState(false);
  const [zi, setZi] = useState(2);
  const [limit, setLimit] = useState(MAX_PAGES);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => { setLimit(MAX_PAGES); scroller.current?.scrollTo({ top: 0 }); }, [fileId, start, end]);
  useEffect(() => {
    if (!full) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", k);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", k); document.body.style.overflow = ""; };
  }, [full]);

  const zoom = ZOOMS[zi];
  const dpi = Math.min(150, Math.round((full ? 110 : 80) * zoom));
  const px = Math.round((full ? 2600 : 1600) * Math.max(1, zoom)); // lets large drawing sheets stay legible when zoomed
  const total = Math.max(1, end - start + 1);
  const pages = Array.from({ length: Math.min(total, limit) }, (_, i) => start + i);

  const body = (
    <div className={`pv ${full ? "pv-full" : ""}`} role={full ? "dialog" : undefined} aria-modal={full || undefined} aria-label={full ? "Document viewer" : undefined}>
      <header className="pv-head">
        <div className="pv-title"><b>{title}</b>{sub && <span className="muted small">{sub}</span>}</div>
        <div className="pv-tools">
          <span className="muted tiny">{total} page{total === 1 ? "" : "s"}</span>
          <button className="icon-btn" onClick={() => setZi(Math.max(0, zi - 1))} disabled={zi === 0} aria-label="Zoom out" title="Zoom out"><Icon name="zoomout" size={16} /></button>
          <button className="pv-zoom" onClick={() => setZi(2)} title="Reset zoom">{Math.round(zoom * 100)}%</button>
          <button className="icon-btn" onClick={() => setZi(Math.min(ZOOMS.length - 1, zi + 1))} disabled={zi === ZOOMS.length - 1} aria-label="Zoom in" title="Zoom in"><Icon name="zoomin" size={16} /></button>
          {pdfUrl && <a className="icon-btn" href={pdfUrl} target="_blank" rel="noreferrer" aria-label="Open as PDF in a new tab" title="Open as PDF in a new tab"><Icon name="external" size={16} /></a>}
          <button className="icon-btn" onClick={() => setFull(!full)} aria-label={full ? "Exit full screen" : "Full screen"} title={full ? "Exit full screen (Esc)" : "Full screen"}>
            <Icon name={full ? "shrink" : "expand"} size={16} /></button>
          {onClose && !full && <button className="icon-btn" onClick={onClose} aria-label="Close viewer" title="Close"><Icon name="x" size={16} /></button>}
        </div>
      </header>
      {children && <div className="pv-info">{children}</div>}
      <div className="pv-pages" ref={scroller}>
        <div className="pv-stack" style={{ width: full ? `min(${zoom * 100}%, ${Math.round(zoom * 1000)}px)` : `${zoom * 100}%` }}>
          {pages.map((p) => (
            <figure key={`${fileId}-${p}`} className="pv-page">
              <img loading="lazy" src={`/api/library/${fileId}/pages/${p}.png?dpi=${dpi}&px=${px}`} alt={`Page ${p}`} />
              <figcaption>Page {p}</figcaption>
            </figure>
          ))}
          {total > limit && (
            <div className="pv-more">
              Showing {limit} of {total} pages. <button className="link-btn" onClick={() => setLimit(limit + MAX_PAGES)}>Show more</button>
              {fullUrl && <> or <a className="link-btn" href={fullUrl} target="_blank" rel="noreferrer">open the whole file</a></>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
  if (!full) return body;
  return (
    <>
      <div className="pv-placeholder muted small">Open in full screen. Press Esc to return.</div>
      {createPortal(<div className="pv-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setFull(false)}>{body}</div>, document.body)}
    </>
  );
}
