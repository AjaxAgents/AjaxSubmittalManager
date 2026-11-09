import React, { useEffect, useState } from "react";
import { api, CoverField, LibraryFile } from "../api";
import { Button, Icon, Modal, useToast } from "./ui";

/** Map each box on a company cover page to a piece of submittal data (fixed rules, no AI). */
export function CoverMapper({ file, onClose }: { file: LibraryFile; onClose: () => void }) {
  const toast = useToast();
  const [fields, setFields] = useState<CoverField[]>([]);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [includeGen, setIncludeGen] = useState(false);
  const [img, setImg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.cover(file.id).then((c) => { setFields(c.fields); setTokens(c.tokens); setMapping(c.mapping); setIncludeGen(c.include_generated); setErr(c.error); })
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
  const kindOf = (v: string) => v.startsWith("text:") ? "@custom" : v;

  return (
    <Modal title={`Cover page: ${file.filename}`} onClose={onClose} wide
      footer={<><label className="toggle small grow"><input type="checkbox" checked={includeGen} onChange={(e) => setIncludeGen(e.target.checked)} /><span className="toggle-ui" />
        Also add the standard Ajax transmittal page after it</label>
        <Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>Save mapping</Button></>}>
      <div className="mapper">
        <div className="mapper-preview">{img ? <img src={img} alt="Filled preview with sample values" /> : <span className="spinner spinner-brand" />}<p className="muted tiny">Preview with sample values</p></div>
        <div className="mapper-fields">
          {err && <p className="t-bad small">{err}</p>}
          <p className="muted small">Each box is filled by a fixed rule. You can also type tokens such as <code>{"{ProjectName}"}</code> or <code>{"{SubmittalNo}"}</code> straight into the template's boxes or text, and they are replaced automatically.</p>
          {!fields.length && !err && (
            <p className="note-warn"><Icon name="alert" size={15} /> No fillable fields or text boxes were found. Add form fields or text boxes in Bluebeam (or type {"{Tokens}"} on the page), then upload the template again. Until then it is inserted as-is.</p>
          )}
          {fields.map((f) => (
            <div className="maprow" key={f.key}>
              <div className="maprow-name">
                <b>{f.kind === "field" ? f.name : (f.current ? `"${f.current.slice(0, 40)}"` : "Empty text box")}</b>
                <span className="muted tiny">{f.kind === "field" ? "Form field" : "Text box"}, page {f.page}</span>
              </div>
              <select className="input input-sm" value={kindOf(val(f))} onChange={(e) => setMapping({ ...mapping, [f.key]: e.target.value === "@custom" ? "text:" : e.target.value })}>
                <option value="">Leave as is</option>
                <option value="@template">Replace the {"{Tokens}"} it contains</option>
                <optgroup label="Fill with">{Object.entries(tokens).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</optgroup>
                <option value="@custom">Custom text with tokens...</option>
              </select>
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
