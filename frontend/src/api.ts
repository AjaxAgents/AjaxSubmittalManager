// Typed client for the local ASM backend. All calls send the session cookie (same origin).

export interface User { id: string; username: string; display_name: string; is_admin: boolean; prefs: Record<string, any>; api_key_set: boolean; api_key_hint: string; own_key: boolean }
export interface LibSummary { files: number; ready: number; processing: number; unprocessed: number; errors: number; sections: number; sheets: number; amendments: number; examples: number; div01: number; covers: number }
export interface Project {
  id: string; name: string; number: string; ae_firm: string; contractor: string; owner: string; address: string;
  settings: Record<string, any>; created_at: number; updated_at: number; library: LibSummary; submittals?: number;
}
export interface LibraryFile {
  id: string; project_id: string; kind: string; category: "info" | "example" | "div01" | "cover"; filename: string; pages: number; size: number;
  status: string; progress: number; error: string; notes: string; uploaded_at: number; version: number; division: string;
  meta: Record<string, any>; duplicate?: boolean;
}
export interface Upload { id: string; filename: string; pages: number; size: number; flags: string[]; sha256: string }
export interface TreeNode {
  id: string; kind: string; label: string; number?: string; title?: string; pages?: number; page_start?: number;
  page_end?: number; amended_by?: string[]; tokens?: number; file_id?: string; children: TreeNode[];
}
export interface ScopeItem { id: string; reason?: string; locked?: boolean; pages?: number[] | null }
export interface Settings {
  api_key_set: boolean; api_key_hint: string; own_key: boolean; mock: boolean; stages: Record<string, string>; usd_cap: number;
  scope_mode: string; page_budget: number; web_search: boolean; output_dir: string; review_mode: string; theme: string; user: User;
}
export interface Job {
  id: string; project_id: string; status: string; stage: string; params: any; result: any; cost: number; error: string;
  created_at: number; updated_at: number; parent_job: string; submittal_id?: string; calls?: any[];
}
export interface JobBrief { id: string; status: string; created_at: number; cost: number; label: string; title: string; subcontractor: string; counts: Record<string, number>; extra_hr: number; mode: string }
export interface Estimate { submittal_pages: number; library_pages: number; est_input_tokens: number; est_cost_low: number; est_cost_high: number; est_cost_mid: number; alt_mode_mid: number; usd_cap: number; mock: boolean; by_stage: Record<string, number> }
export interface Sub { id: string; project_id: string; name: string; contact: string; email: string; phone: string; divisions: string[]; spec_sections: string[]; notes: string }
export interface Division { code: string; name: string; enabled: boolean; custom: boolean; sections: { number: string; title: string }[]; subcontractors: { id: string; name: string }[] }
export interface Submittal {
  id: string; project_id: string; spec_section: string; seq: number; revision: string; title: string; type: string; spec_title: string;
  subcontractor_id: string; subcontractor: string; status: string; required_date: string; number: string; division: string; division_name: string;
  created_at: number; updated_at: number; options: any;
  latest: null | { id: string; status: string; counts: Record<string, number>; extra_hr: number; cost: number; pdf_name: string; updated_at: number };
  runs?: { id: string; status: string; cost: number; created_at: number; parent_job: string }[];
}
export interface CoverField { key: string; kind: "field" | "textbox"; name: string; label: string; page: number; rect: number[]; current: string; mapping: string }

export class AuthError extends Error {}

async function j<T>(res: Response): Promise<T> {
  if (res.status === 401 && !res.url.includes("/api/auth/")) {
    window.dispatchEvent(new Event("asm:signed-out"));
    throw new AuthError("Please sign in.");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const b = await res.json(); msg = typeof b.detail === "string" ? b.detail : msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}
const json = (method: string, body?: unknown): RequestInit => ({
  method, credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
});
const get = <T,>(url: string) => fetch(url, { credentials: "same-origin" }).then(j<T>);
const form = (files: File[], extra: Record<string, string> = {}) => {
  const fd = new FormData(); files.forEach((f) => fd.append("files", f)); Object.entries(extra).forEach(([k, v]) => fd.append(k, v)); return fd;
};

export const api = {
  // auth
  status: () => get<{ has_users: boolean; user: User | null; version: string }>("/api/auth/status"),
  register: (b: { username: string; password: string; display_name: string }) => fetch("/api/auth/register", json("POST", b)).then(j<{ user: User; claimed_projects: number }>),
  login: (b: { username: string; password: string; remember: boolean }) => fetch("/api/auth/login", json("POST", b)).then(j<{ user: User }>),
  logout: () => fetch("/api/auth/logout", json("POST")).then(j<{ ok: boolean }>),
  updateMe: (b: { display_name?: string; current_password?: string; new_password?: string; prefs?: any }) => fetch("/api/me", json("PUT", b)).then(j<User>),
  health: () => get<{ ok: boolean; version: string; mock: boolean; ocr: string }>("/api/health"),
  settings: () => get<Settings>("/api/settings"),
  saveSettings: (s: any) => fetch("/api/settings", json("PUT", s)).then(j<Settings>),
  // projects
  projects: () => get<Project[]>("/api/projects"),
  createProject: (p: Partial<Project>) => fetch("/api/projects", json("POST", p)).then(j<Project>),
  updateProject: (id: string, p: Partial<Project>) => fetch(`/api/projects/${id}`, json("PATCH", p)).then(j<Project>),
  deleteProject: (id: string) => fetch(`/api/projects/${id}`, json("DELETE")).then(j<{ ok: boolean }>),
  home: (pid: string) => get<any>(`/api/projects/${pid}/home`),
  // divisions & subs
  divisions: (pid: string) => get<{ divisions: Division[] }>(`/api/projects/${pid}/divisions`),
  saveDivisions: (pid: string, divisions: { code: string; name: string }[]) => fetch(`/api/projects/${pid}/divisions`, json("PUT", { divisions })).then(j<{ divisions: Division[] }>),
  subs: (pid: string) => get<Sub[]>(`/api/projects/${pid}/subcontractors`),
  createSub: (pid: string, s: Partial<Sub>) => fetch(`/api/projects/${pid}/subcontractors`, json("POST", s)).then(j<Sub>),
  updateSub: (pid: string, id: string, s: Partial<Sub>) => fetch(`/api/projects/${pid}/subcontractors/${id}`, json("PUT", s)).then(j<Sub>),
  deleteSub: (pid: string, id: string) => fetch(`/api/projects/${pid}/subcontractors/${id}`, json("DELETE")).then(j<{ ok: boolean }>),
  // library
  library: (pid: string) => get<{ files: LibraryFile[]; summary: LibSummary; pending: { files: number; pages: number; est_ai_cost: number }; defaults: any }>(`/api/projects/${pid}/library`),
  uploadLibrary: (pid: string, files: File[], kind: string, division = "") =>
    fetch(`/api/projects/${pid}/library`, { method: "POST", credentials: "same-origin", body: form(files, { kind, division }) })
      .then(j<{ files: LibraryFile[]; errors: { filename: string; error: string }[] }>),
  process: (pid: string, file_ids: string[], use_ai: boolean) => fetch(`/api/projects/${pid}/library/process`, json("POST", { file_ids, use_ai })).then(j<{ queued: number }>),
  cancelProcessing: (pid: string, file_ids: string[] = []) => fetch(`/api/projects/${pid}/library/cancel`, json("POST", { file_ids })).then(j<{ cancelled: number }>),
  bulk: (pid: string, file_ids: string[], action: string, value = "") => fetch(`/api/projects/${pid}/library/bulk`, json("POST", { file_ids, action, value })).then(j<{ updated: number }>),
  patchFile: (fid: string, b: { kind?: string; division?: string }) => fetch(`/api/library/${fid}`, json("PATCH", b)).then(j<LibraryFile>),
  deleteFile: (fid: string) => fetch(`/api/library/${fid}`, json("DELETE")).then(j<{ ok: boolean }>),
  setDefaults: (pid: string, d: { cover?: string; examples?: string[]; div01?: string[] }) => fetch(`/api/projects/${pid}/library/defaults`, json("PUT", d)).then(j<any>),
  cover: (fid: string) => get<{ fields: CoverField[]; mapping: Record<string, string>; tokens: Record<string, string>; include_generated: boolean; page_size: number[]; error: string }>(`/api/library/${fid}/cover`),
  saveCover: (fid: string, mapping: Record<string, string>, include_generated: boolean) => fetch(`/api/library/${fid}/cover`, json("PUT", { mapping, include_generated })).then(j<any>),
  coverPreviewUrl: (fid: string) => `/api/library/${fid}/cover/preview`,
  tree: (pid: string) => get<TreeNode[]>(`/api/projects/${pid}/catalog/tree`),
  sections: (pid: string) => get<{ number: string; title: string; pages: number; division: string }[]>(`/api/projects/${pid}/catalog/sections`),
  catalogItem: (pid: string, id: string) => get<any>(`/api/projects/${pid}/catalog/item?id=${encodeURIComponent(id)}`),
  // submittal review
  upload: (files: File[]) => fetch("/api/uploads", { method: "POST", credentials: "same-origin", body: form(files) })
    .then(j<{ files: Upload[]; errors: { filename: string; error: string }[] }>),
  autofill: (pid: string, upload_ids: string[]) => fetch(`/api/projects/${pid}/autofill`, json("POST", { upload_ids })).then(j<any>),
  suggestScope: (pid: string, body: { spec_section: string; upload_ids: string[]; instructions: string; reset?: boolean }) =>
    fetch(`/api/projects/${pid}/scope/suggest`, json("POST", body)).then(j<{ items: ScopeItem[]; mode: string; source: string; section: string; section_title: string; found: boolean }>),
  estimate: (pid: string, body: { upload_ids: string[]; scope_items: ScopeItem[]; review_mode?: string }) => fetch(`/api/projects/${pid}/estimate`, json("POST", body)).then(j<Estimate>),
  namingPreview: (pid: string, params: any, example_file_ids: string[]) =>
    fetch(`/api/projects/${pid}/naming/preview`, json("POST", { params, example_file_ids })).then(j<{ name: string; pattern: string; source: string; number: string }>),
  nextSeq: (pid: string, spec: string) => get<{ seq: number }>(`/api/projects/${pid}/submittals/next?spec_section=${encodeURIComponent(spec)}`),
  submittals: (pid: string) => get<Submittal[]>(`/api/projects/${pid}/submittals`),
  submittal: (sid: string) => get<Submittal>(`/api/submittals/${sid}`),
  deleteSubmittal: (sid: string) => fetch(`/api/submittals/${sid}`, json("DELETE")).then(j<{ ok: boolean }>),
  createJob: (pid: string, body: any) => fetch(`/api/projects/${pid}/jobs`, json("POST", body)).then(j<Job>),
  jobs: (pid: string) => get<JobBrief[]>(`/api/projects/${pid}/jobs`),
  job: (id: string) => get<Job>(`/api/jobs/${id}`),
  cancel: (id: string) => fetch(`/api/jobs/${id}/cancel`, json("POST")).then(j<{ ok: boolean }>),
  rerun: (id: string, notes: string) => fetch(`/api/jobs/${id}/rerun`, json("POST", { notes })).then(j<Job>),
  txt: (id: string) => fetch(`/api/jobs/${id}/txt`, { credentials: "same-origin" }).then((r) => r.text()),
  openFolder: (id: string) => fetch(`/api/jobs/${id}/open-folder`, json("POST")).then(j<{ ok: boolean; folder: string; error?: string }>),
};

export const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export const fmtTime = (t: number) => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
export const fmtDate = (t: number) => new Date(t * 1000).toLocaleDateString([], { dateStyle: "medium" });
export const DIVISION_NAMES: Record<string, string> = {
  "00": "Procurement and Contracting", "01": "General Requirements", "02": "Existing Conditions", "03": "Concrete", "04": "Masonry",
  "05": "Metals", "06": "Wood, Plastics, and Composites", "07": "Thermal and Moisture Protection", "08": "Openings", "09": "Finishes",
  "10": "Specialties", "11": "Equipment", "12": "Furnishings", "13": "Special Construction", "14": "Conveying Equipment",
  "21": "Fire Suppression", "22": "Plumbing", "23": "HVAC", "25": "Integrated Automation", "26": "Electrical", "27": "Communications",
  "28": "Electronic Safety and Security", "31": "Earthwork", "32": "Exterior Improvements", "33": "Utilities",
};
/** Division codes in MasterFormat order (00, 01, ... 33). Object.keys would put "10".."33" before "00".."09". */
export const DIVISION_CODES = Object.keys(DIVISION_NAMES).sort();
export const STATUS_LABEL: Record<string, { label: string; tone: "ok" | "bad" | "warn" | "info" | "neutral" }> = {
  draft: { label: "Draft", tone: "neutral" }, in_review: { label: "In review", tone: "info" }, clean: { label: "No exceptions", tone: "ok" },
  exceptions: { label: "Exceptions", tone: "bad" }, needs_review: { label: "Needs review", tone: "warn" }, error: { label: "Run failed", tone: "bad" },
};
