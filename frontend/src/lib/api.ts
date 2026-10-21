import axios, { type AxiosRequestConfig } from "axios";
import { auth, type User } from "./firebase";

export type CountryCode = "us" | "uae";

/** Loosely-typed JSON from the API — a run's `result` shape depends on its state. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ApiJson = Record<string, any>;
export type WorkflowStatus = "queued" | "uploaded" | "running" | "completed" | "failed" | "blocked" | "interrupted";

export const STATUS_CONFIG: Record<WorkflowStatus, { label: string; cls: string }> = {
  completed:   { label: "DONE",     cls: "badge-blue"  },
  blocked:     { label: "BLOCK",    cls: "badge-block" },
  failed:      { label: "FAIL",     cls: "badge-block" },
  running:     { label: "RUNNING",  cls: "badge-blue"  },
  queued:      { label: "QUEUED",   cls: "badge-muted" },
  uploaded:    { label: "UPLOADED", cls: "badge-muted" },
  interrupted: { label: "PAUSED",   cls: "badge-warn"  },
};

export interface WorkflowStep {
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "blocked" | "interrupted" | "queued";
  started_at?: string | null;
  completed_at?: string | null;
  output?: ApiJson & { reasoning_note?: string | null };
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
  result: ApiJson;
  bboxes?: BBoxEntry[];
}

export interface StatusResponse extends WorkflowResponse {
  bboxes: BBoxEntry[];
  invoice_pdf_url?: string;
  bl_pdf_url?: string;
}

export const API_BASE: string =
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

/** Guest sessions have no Firebase user, so they renew via the refresh token. */
async function refreshGuestToken(): Promise<void> {
  const refreshToken = localStorage.getItem("refresh_token");
  if (!refreshToken) throw new Error("No refresh token");
  const { data } = await axios.post(`${API_BASE}/auth/refresh`, { refresh_token: refreshToken });
  localStorage.setItem("access_token", data.access_token);
}

// On 401, renew the backend JWT once and retry the request, so an expired
// token never surfaces as an error while the user is signed in.
let refreshing: Promise<void> | null = null;
api.interceptors.response.use(undefined, async (error) => {
  const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined;
  const user = auth?.currentUser ?? null;
  const isGuest = Boolean(localStorage.getItem("guest_session"));
  if (error.response?.status !== 401 || !original || original._retried || (!user && !isGuest)) {
    throw error;
  }
  original._retried = true;
  refreshing ??= (user ? exchangeFirebaseToken(user, true) : refreshGuestToken())
    .finally(() => { refreshing = null; });
  await refreshing;
  return api(original);
});

/** Start a shared local guest session (only works when the backend sets ALLOW_GUEST_LOGIN). */
export const createGuestSession = async () => {
  try {
    const { data } = await axios.post(`${API_BASE}/auth/google`, { firebase_token: "local-guest" });
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);
    localStorage.setItem(
      "guest_session",
      JSON.stringify({ displayName: "Local Guest", email: "guest@local", photoURL: null }),
    );
    return data;
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 403) {
      throw new Error("Guest login is disabled on this server.");
    }
    throw e;
  }
};

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
  /** Corrected values keyed by issue field path, e.g. "invoice.date". */
  corrections: Record<string, string | number>;
}

export const resumeWorkflow = async (runId: string, corrections: ResumeCorrections): Promise<WorkflowResponse> => {
  const { data } = await api.post(`/workflow/resume/${runId}`, corrections);
  return data;
};

// ── Declaration chat ──────────────────────────────────────────────────────────

export interface ProposedChange {
  path: string;
  value: string | number;
  reason?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  proposals: ProposedChange[];
  /** "pending" proposals can be applied or dismissed; edits never apply on their own. */
  proposal_status: "none" | "pending" | "applied" | "dismissed";
  created_at: string;
}

export const getChat = async (runId: string): Promise<ChatMessage[]> => {
  const { data } = await api.get(`/workflow/chat/${runId}`);
  return data.messages;
};

export const sendChat = async (runId: string, message: string): Promise<ChatMessage[]> => {
  const { data } = await api.post(`/workflow/chat/${runId}`, { message });
  return data.messages;
};

export const actOnProposal = async (
  runId: string,
  index: number,
  action: "apply" | "dismiss",
): Promise<ChatMessage[]> => {
  const { data } = await api.post(`/workflow/chat/${runId}/proposals/${index}`, { action });
  return data.messages;
};

/** FastAPI error detail as one readable string. */
export function errorDetail(e: unknown, fallback: string): string {
  const detail: unknown = axios.isAxiosError(e) ? e.response?.data?.detail : undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d?.msg ?? String(d)).join("; ");
  return fallback;
}

export { api };
