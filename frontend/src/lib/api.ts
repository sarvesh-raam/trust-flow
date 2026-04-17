import axios from "axios";

// Dev: Vite proxy forwards /api → localhost:8000.
// Prod Docker: nginx proxies /api → backend:8000.
// If VITE_API_BASE is set (e.g. "/api"), it already IS the base path — append only "/v1".
// If not set, fall back to direct localhost:8000 (dev) or empty string (other).
const _envBase = import.meta.env.VITE_API_BASE;
// @ts-ignore
const BASE = _envBase
  ? `${_envBase}/v1`
  : `/api/v1`;

export type CountryCode = "us" | "uae";
export type WorkflowStatus =
  | "queued"
  | "uploaded"
  | "running"
  | "completed"
  | "failed"
  | "blocked"
  | "interrupted";


export const STATUS_CONFIG: Record<WorkflowStatus, { label: string; cls: string }> = {
  completed:   { label: "PASS",    cls: "badge-pass"  },
  blocked:     { label: "BLOCK",   cls: "badge-block" },
  failed:      { label: "FAIL",    cls: "badge-block" },
  running:     { label: "RUNNING", cls: "badge-blue"  },
  queued:      { label: "QUEUED",  cls: "badge-muted" },
  uploaded:    { label: "UPLOADED", cls: "badge-muted" },
  interrupted: { label: "PAUSED",  cls: "badge-warn"  },
};

export interface WorkflowStep {
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "blocked" | "interrupted" | "queued";
  started_at?: string | null;
  completed_at?: string | null;
  output?: Record<string, any> & { reasoning_note?: string | null };
  reasoning_note?: string | null;
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

export interface WorkflowChatResponse {
  reply: string;
  updated: boolean;
  changes: string[];
  declaration?: Record<string, any>;
  summary?: string;
  chat_history: Array<Record<string, any>>;
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || (window.location.hostname === "localhost" ? "http://localhost:8000/api/v1" : "/api/v1"),
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Auto-refresh on 401 — use stored refresh_token to get a new access_token
let _refreshing: Promise<string | null> | null = null;

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    if (error.response?.status !== 401 || original._retried) {
      return Promise.reject(error);
    }
    original._retried = true;

    if (!_refreshing) {
      _refreshing = (async () => {
        const refreshToken = localStorage.getItem("refresh_token");
        if (!refreshToken) return null;
        try {
          const { data } = await api.post("/auth/refresh", { refresh_token: refreshToken });
          localStorage.setItem("access_token", data.access_token);
          return data.access_token as string;
        } catch {
          localStorage.removeItem("access_token");
          localStorage.removeItem("refresh_token");
          localStorage.removeItem("guest_session");
          return null;
        } finally {
          _refreshing = null;
        }
      })();
    }

    const newToken = await _refreshing;
    if (!newToken) return Promise.reject(error);
    original.headers.Authorization = `Bearer ${newToken}`;
    return api(original);
  },
);

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

export const resumeWorkflow = async (runId: string, grossWeightKg: number) => {
  const { data } = await api.post(`/workflow/resume/${runId}`, {
    gross_weight_kg: grossWeightKg,
  });
  return data;
};

export const chatWithWorkflow = async (
  runId: string,
  message: string,
): Promise<WorkflowChatResponse> => {
  const { data } = await api.post(`/workflow/chat/${runId}`, { message });
  return data;
};

export const createGuestSession = async () => {
  const { data } = await api.post("/auth/google", { firebase_token: "local-guest" });
  localStorage.setItem("access_token", data.access_token);
  localStorage.setItem("refresh_token", data.refresh_token);
  localStorage.setItem(
    "guest_session",
    JSON.stringify({
      displayName: "Local Guest",
      email: "guest@local",
      photoURL: null,
    }),
  );
  return data;
};

export { api };
