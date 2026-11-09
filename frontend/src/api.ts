// Thin typed client for the local ASM backend.

export interface Project {
  id: string; name: string; number: string; ae_firm: string; contractor: string; owner: string; address: string;
  settings: Record<string, unknown>; created_at: number; updated_at: number;
  library: { files: number; ready: number; processing: number; errors: number; sections: number; sheets: number; amendments: number };
}
export interface LibraryFile {
  id: string; project_id: string; kind: string; filename: string; pages: number; size: number; status: string;
  progress: number; error: string; notes: string; uploaded_at: number; version: number; duplicate?: boolean;
}
export interface Upload { id: string; filename: string; pages: number; size: number; flags: string[]; sha256: string }
export interface TreeNode {
  id: string; kind: string; label: string; number?: string; title?: string; pages?: number; page_start?: number;
  page_end?: number; amended_by?: string[]; tokens?: number; children: TreeNode[];
}
export interface ScopeItem { id: string; reason?: string; locked?: boolean; pages?: number[] | null }
export interface Settings {
  api_key_set: boolean; api_key_hint: string; mock: boolean; stages: Record<string, string>; usd_cap: number;
  scope_mode: string; page_budget: number; web_search: boolean; output_dir: string; key_location?: string; review_mode?: string;
}
export interface Health { ok: boolean; version: string; api_key_set: boolean; mock: boolean; ocr: string; data_dir: string }
export interface Job {
  id: string; project_id: string; status: string; stage: string; params: any; result: any; cost: number; error: string;
  created_at: number; updated_at: number; parent_job: string; calls?: any[];
}
export interface Estimate {
  submittal_pages: number; library_pages: number; est_input_tokens: number; est_cost_low: number; est_cost_high: number;
  usd_cap: number; mock: boolean;
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const b = await res.json(); msg = b.detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}
const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  health: () => fetch("/api/health").then(j<Health>),
  settings: () => fetch("/api/settings").then(j<Settings>),
  saveSettings: (s: Partial<Settings> & { api_key?: string; stage_models?: Record<string, string> }) =>
    fetch("/api/settings", json("PUT", s)).then(j<Settings>),
  projects: () => fetch("/api/projects").then(j<Project[]>),
  createProject: (p: Partial<Project>) => fetch("/api/projects", json("POST", p)).then(j<Project>),
  updateProject: (id: string, p: Partial<Project>) => fetch(`/api/projects/${id}`, json("PATCH", p)).then(j<Project>),
  deleteProject: (id: string) => fetch(`/api/projects/${id}`, json("DELETE")).then(j<{ ok: boolean }>),
  library: (pid: string) => fetch(`/api/projects/${pid}/library`).then(j<{ files: LibraryFile[]; summary: Project["library"] }>),
  uploadLibrary: (pid: string, files: File[], kind = "auto") => {
    const fd = new FormData(); files.forEach((f) => fd.append("files", f)); fd.append("kind", kind);
    return fetch(`/api/projects/${pid}/library`, { method: "POST", body: fd })
      .then(j<{ files: LibraryFile[]; errors: { filename: string; error: string }[] }>);
  },
  setFileKind: (fid: string, kind: string) => fetch(`/api/library/${fid}`, json("PATCH", { kind })).then(j<LibraryFile>),
  deleteFile: (fid: string) => fetch(`/api/library/${fid}`, json("DELETE")).then(j<{ ok: boolean }>),
  tree: (pid: string) => fetch(`/api/projects/${pid}/catalog/tree`).then(j<TreeNode[]>),
  sections: (pid: string) => fetch(`/api/projects/${pid}/catalog/sections`).then(j<{ number: string; title: string; pages: number }[]>),
  catalogItem: (pid: string, id: string) => fetch(`/api/projects/${pid}/catalog/item?id=${encodeURIComponent(id)}`).then(j<any>),
  upload: (files: File[]) => {
    const fd = new FormData(); files.forEach((f) => fd.append("files", f));
    return fetch("/api/uploads", { method: "POST", body: fd }).then(j<{ files: Upload[]; errors: { filename: string; error: string }[] }>);
  },
  suggestScope: (pid: string, body: { spec_section: string; upload_ids: string[]; instructions: string; reset?: boolean }) =>
    fetch(`/api/projects/${pid}/scope/suggest`, json("POST", body))
      .then(j<{ items: ScopeItem[]; mode: string; source: string; section: string; section_title: string; found: boolean }>),
  estimate: (pid: string, body: { upload_ids: string[]; scope_items: ScopeItem[]; review_mode?: string }) =>
    fetch(`/api/projects/${pid}/estimate`, json("POST", body)).then(j<Estimate>),
  namingPreview: (pid: string, params: any, example_upload_ids: string[]) =>
    fetch(`/api/projects/${pid}/naming/preview`, json("POST", { params, example_upload_ids }))
      .then(j<{ name: string; pattern: string; source: string; note: string }>),
  createJob: (pid: string, body: any) => fetch(`/api/projects/${pid}/jobs`, json("POST", body)).then(j<Job>),
  jobs: (pid: string) => fetch(`/api/projects/${pid}/jobs`).then(j<Job[]>),
  job: (id: string) => fetch(`/api/jobs/${id}`).then(j<Job>),
  cancel: (id: string) => fetch(`/api/jobs/${id}/cancel`, json("POST")).then(j<{ ok: boolean }>),
  rerun: (id: string, notes: string) => fetch(`/api/jobs/${id}/rerun`, json("POST", { notes })).then(j<Job>),
  txt: (id: string) => fetch(`/api/jobs/${id}/txt`).then((r) => r.text()),
  openFolder: (id: string) => fetch(`/api/jobs/${id}/open-folder`, json("POST")).then(j<{ ok: boolean; folder: string; error?: string }>),
};

export const fmtBytes = (n: number) =>
  n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
export const fmtTime = (t: number) => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
