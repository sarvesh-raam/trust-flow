import axios, { type AxiosRequestConfig } from "axios";
import { auth, type User } from "./firebase";

export type CountryCode = "us" | "uae";
export type WorkflowStatus = "queued" | "running" | "completed" | "failed" | "blocked" | "interrupted";

export const STATUS_CONFIG: Record<WorkflowStatus, { label: string; cls: string }> = {
  completed:   { label: "PASS",    cls: "badge-pass"  },
  blocked:     { label: "BLOCK",   cls: "badge-block" },
  failed:      { label: "FAIL",    cls: "badge-block" },
  running:     { label: "RUNNING", cls: "badge-blue"  },
  queued:      { label: "QUEUED",  cls: "badge-muted" },
  interrupted: { label: "PAUSED",  cls: "badge-warn"  },
};

export interface WorkflowStep {
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "blocked" | "interrupted" | "queued";
  started_at?: string | null;
  completed_at?: string | null;
  output?: Record<string, any>;
  error?: string | null;
}

export interface BBoxEntry {
  field_name: string;
  value: string;
  bbox: number[];
  page: number;
  confidence: number;
  source: "invoice" | "bl";
}

export interface ComplianceIssue {
  field: string;
  message: string;
  severity: "warn" | "block";
}

export interface WorkflowResponse {
  id: string;
  status: WorkflowStatus;
  country: CountryCode;
  created_at: string;
  updated_at?: string;
  document_id?: string;
  steps: WorkflowStep[];
  result: Record<string, any>;
  bboxes?: BBoxEntry[];
}

export interface StatusResponse extends WorkflowResponse {
  bboxes: BBoxEntry[];
  invoice_pdf_url?: string;
  bl_pdf_url?: string;
}

const API_BASE: string =
  import.meta.env.VITE_API_BASE ||
  (window.location.hostname === "localhost" ? "http://localhost:8000/api/v1" : "/api/v1");

const api = axios.create({ baseURL: API_BASE });

/** Turn a backend path like "/api/v1/upload/…" into a URL on the API host. */
export function resolveApiUrl(path: string): string {
  return path.startsWith("/api/v1") ? API_BASE + path.slice("/api/v1".length) : path;
}

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Exchange the Firebase ID token for a backend JWT and store it. */
export async function exchangeFirebaseToken(u: User, forceRefresh = false): Promise<void> {
  const idToken = await u.getIdToken(forceRefresh);
  // Plain axios call — must not go through the 401-retry interceptor below.
  const { data } = await axios.post(`${API_BASE}/auth/google`, { firebase_token: idToken });
  localStorage.setItem("access_token", data.access_token);
}

api.interceptors.request.use((config) => {
  Object.assign(config.headers, authHeaders());
  return config;
});

// On 401, re-exchange the Firebase token once and retry the request, so an
// expired backend JWT never surfaces as an error while the user is signed in.
let refreshing: Promise<void> | null = null;
api.interceptors.response.use(undefined, async (error) => {
  const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined;
  const user = auth.currentUser;
  if (error.response?.status !== 401 || !original || original._retried || !user) {
    throw error;
  }
  original._retried = true;
  refreshing ??= exchangeFirebaseToken(user, true).finally(() => { refreshing = null; });
  await refreshing;
  return api(original);
});

export const uploadDocument = async (invoice: File, bl: File, country: CountryCode) => {
  const formData = new FormData();
  formData.append("invoice_pdf", invoice);
  formData.append("bl_pdf", bl);
  formData.append("country", country);
  const { data } = await api.post("/upload/", formData);
  return data;
};

export const createWorkflow = async (documentId: string, country: CountryCode) => {
  const { data } = await api.post("/workflow/", { document_id: documentId, country });
  return data;
};

export const listWorkflows = async (): Promise<WorkflowResponse[]> => {
  const { data } = await api.get("/workflow/");
  return data;
};

export const getRunStatus = async (runId: string): Promise<StatusResponse> => {
  const { data } = await api.get(`/workflow/status/${runId}`);
  return data;
};

export interface ResumeCorrections {
  gross_weight_kg?: number;
  invoice_gross_weight_kg?: number;
}

export const resumeWorkflow = async (runId: string, corrections: ResumeCorrections): Promise<WorkflowResponse> => {
  const { data } = await api.post(`/workflow/resume/${runId}`, corrections);
  return data;
};

export { api };
