import React, { useEffect, useRef, useState } from "react";
import { api, CoverField, LibraryFile } from "../api";
import { Button, Icon, Modal, useToast } from "./ui";

/** Map each box on a company cover page to a piece of submittal data (fixed rules, no AI). */
export function CoverMapper({ file, onClose }: { file: LibraryFile; onClose: () => void }) {
  const toast = useToast();
  const [fields, setFields] = useState<CoverField[]>([]);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [groups, setGroups] = useState<[string, string[]][]>([]);
  const [neededDays, setNeededDays] = useState(14);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [includeGen, setIncludeGen] = useState(false);
  const [img, setImg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [pageSize, setPageSize] = useState<number[]>([612, 792]);
  const [active, setActive] = useState<string | null>(null);
  const rowsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.cover(file.id).then((c) => { setFields(c.fields); setTokens(c.tokens); setGroups(c.token_groups || []); if (c.needed_days) setNeededDays(c.needed_days); setMapping(c.mapping); setIncludeGen(c.include_generated); setErr(c.error); if (c.page_size?.length === 2) setPageSize(c.page_size); })
      .catch((e) => setErr(e.message));
  }, [file.id]);

  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const r = await fetch(api.coverPreviewUrl(file.id), { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mapping, params: {} }) });
        if (r.ok) { const b = await r.blob(); setImg((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(b); }); }
      } catch { /* preview is optional */ }
    }, 350);
    return () => clearTimeout(t);
  }, [JSON.stringify(mapping)]);

  const save = async () => {
    setBusy(true);
    try { await api.saveCover(file.id, mapping, includeGen); toast("Cover page mapping saved", "ok"); onClose(); }
    catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };

  const val = (f: CoverField) => mapping[f.key] ?? "";
  const pick = (key: string) => {
    setActive(key);
    const row = rowsRef.current?.querySelector<HTMLElement>(`[data-key="${CSS.escape(key)}"]`);
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    row?.querySelector<HTMLElement>("select")?.focus({ preventScroll: true });
  };
  const box = (f: CoverField): React.CSSProperties => {
    const [w, h] = pageSize; const [x0, y0, x1, y1] = f.rect;
    return { left: `${(x0 / w) * 100}%`, top: `${(y0 / h) * 100}%`, width: `${((x1 - x0) / w) * 100}%`, height: `${((y1 - y0) / h) * 100}%` };
  };
  const activeField = fields.find((f) => f.key === active);
  // stored mapping -> dropdown value ("RequiredDate+10" is the "Date needed by" choice with its own day count)
  const kindOf = (v: string) => v.startsWith("text:") ? "@custom" : /^RequiredDate\+\d+$/.test(v) ? "RequiredDate" : v;
  const daysOf = (v: string) => { const m = v.match(/^RequiredDate\+(\d+)$/); return m ? +m[1] : neededDays; };
  const choose = (f: CoverField, v: string) => setMapping({ ...mapping, [f.key]: v === "@custom" ? "text:" : v === "RequiredDate" ? `RequiredDate+${neededDays}` : v });
  const grouped = groups.length ? groups : [["Fill with", Object.keys(tokens)] as [string, string[]]];
  const inGroups = new Set(grouped.flatMap(([, ks]) => ks));

  return (
    <Modal title={`Cover page: ${file.filename}`} onClose={onClose} wide
      footer={<><label className="toggle small grow"><input type="checkbox" checked={includeGen} onChange={(e) => setIncludeGen(e.target.checked)} /><span className="toggle-ui" />
        Also add the standard Ajax transmittal page after it</label>
        <Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>Save mapping</Button></>}>
      <div className="mapper">
        <div className="mapper-preview">
          {img ? (
            <div className="mapper-sheet">
              <img src={img} alt="Filled preview with sample values" />
              {fields.filter((f) => f.page === 1 && f.rect?.length === 4).map((f) => (
                <button key={f.key} type="button" className={`mapper-box ${active === f.key ? "on" : ""}`} style={box(f)}
                  onClick={() => pick(f.key)} aria-label={`Select ${f.kind === "field" ? f.name : "text box"}`}
                  title={f.kind === "field" ? f.name : f.current || "Text box"}>
                  <span className="mapper-num">{fields.indexOf(f) + 1}</span>
                </button>
              ))}
            </div>
          ) : <span className="spinner spinner-brand" />}
          <p className="muted tiny">{activeField && activeField.page !== 1 ? `This box is on page ${activeField.page}; the preview shows page 1.` : "Preview with sample values. Click a box to edit it."}</p>
        </div>
        <div className="mapper-fields" ref={rowsRef}>
          {err && <p className="t-bad small">{err}</p>}
          <p className="muted small">Each box is filled by a fixed rule. You can also type tokens such as <code>{"{ProjectName}"}</code> or <code>{"{SubmittalNo}"}</code> straight into the template's boxes or text, and they are replaced automatically.</p>
          {!fields.length && !err && (
            <p className="note-warn"><Icon name="alert" size={15} /> No fillable fields or text boxes were found. Add form fields or text boxes in Bluebeam (or type {"{Tokens}"} on the page), then upload the template again. Until then it is inserted as-is.</p>
          )}
          {fields.map((f, i) => (
            <div className={`maprow ${active === f.key ? "on" : ""}`} key={f.key} data-key={f.key}
              onMouseEnter={() => setActive(f.key)} onFocusCapture={() => setActive(f.key)} onClick={() => setActive(f.key)}>
              <div className="maprow-name">
                <b><span className="mapper-num">{i + 1}</span>{f.kind === "field" ? f.name : (f.current ? `"${f.current.slice(0, 40)}"` : "Empty text box")}</b>
                <span className="muted tiny">{f.kind === "field" ? "Form field" : "Text box"}, page {f.page}</span>
              </div>
              <select className="input input-sm" value={kindOf(val(f))} onChange={(e) => choose(f, e.target.value)} aria-label={`Fill ${f.kind === "field" ? f.name : "text box"} with`}>
                <option value="">Leave as is</option>
                <option value="@template">Replace the {"{Tokens}"} it contains</option>
                {grouped.map(([g, ks]) => (
                  <optgroup key={g} label={g}>{ks.filter((k) => tokens[k]).map((k) => <option key={k} value={k}>{tokens[k]}</option>)}</optgroup>
                ))}
                {Object.keys(tokens).some((k) => !inGroups.has(k)) && <optgroup label="Other">{Object.keys(tokens).filter((k) => !inGroups.has(k)).map((k) => <option key={k} value={k}>{tokens[k]}</option>)}</optgroup>}
                <option value="@custom">Custom text with tokens...</option>
              </select>
              {kindOf(val(f)) === "RequiredDate" && (
                <label className="maprow-days">Today +
                  <input className="input input-sm" type="number" min={0} max={365} value={daysOf(val(f))}
                    onChange={(e) => setMapping({ ...mapping, [f.key]: `RequiredDate+${Math.max(0, Math.min(365, parseInt(e.target.value || "0", 10) || 0))}` })} />
                  days <span className="muted tiny">(a date entered on the review form wins)</span>
                </label>
              )}
              {val(f).startsWith("text:") && (
                <input className="input input-sm mono" placeholder="{SpecSection} {SpecTitle}" value={val(f).slice(5)} onChange={(e) => setMapping({ ...mapping, [f.key]: `text:${e.target.value}` })} />
              )}
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
