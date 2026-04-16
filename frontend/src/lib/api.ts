import axios from "axios";

// Dev: Vite proxy forwards /api → localhost:8000.
// Prod Docker: nginx proxies /api → backend:8000.
// If VITE_API_BASE is set (e.g. "/api"), it already IS the base path — append only "/v1".
// If not set, fall back to direct localhost:8000 (dev) or empty string (other).
const _envBase = import.meta.env.VITE_API_BASE;
const BASE = _envBase
  ? `${_envBase}/v1`
  : `/api/v1`;

export const api = axios.create({ baseURL: BASE });

// Attach stored JWT on every request (skip the auth exchange endpoint itself)
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token && !config.url?.includes("/auth/google")) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401 response: get a fresh Firebase ID token, re-exchange, and retry once
api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry && !original.url?.includes("/auth/google")) {
      original._retry = true;
      try {
        const { auth } = await import("./firebase");
        const user = auth.currentUser;
        if (user) {
          const idToken = await user.getIdToken(/* forceRefresh */ true);
          const { data } = await api.post("/auth/google", { firebase_token: idToken });
          localStorage.setItem("access_token", data.access_token);
          original.headers.Authorization = `Bearer ${data.access_token}`;
          return api(original);
        }
      } catch {
        localStorage.removeItem("access_token");
      }
    }
    return Promise.reject(error);
  }
);

// --- Types mirroring backend Pydantic models ---

export type CountryCode    = "us" | "uae";
export type DocumentStatus = "pending" | "processing" | "completed" | "failed";
export type WorkflowStatus = "queued" | "running" | "completed" | "failed" | "blocked";

export interface DocumentResponse {
  id: string;
  filename: string;
  country: CountryCode;
  status: DocumentStatus;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  workflow_id: string | null;
}

export interface WorkflowStep {
  name: string;
  status: WorkflowStatus;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  output?: Record<string, unknown>;
}

export interface WorkflowResponse {
  id: string;
  document_id: string;
  country: CountryCode;
  status: WorkflowStatus;
  steps: WorkflowStep[];
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface BBoxEntry {
  field_name: string;
  value: string;
  /** [x1, y1, x2, y2] in PDF points, origin at top-left */
  bbox: [number, number, number, number];
  page: number;
  confidence: number;
  source: "invoice" | "bl";
}

export interface StatusResponse extends WorkflowResponse {
  bboxes: BBoxEntry[];
  invoice_pdf_url: string;
  bl_pdf_url: string;
}

export interface ComplianceIssue {
  field: string;
  message: string;
  severity: "warn" | "block";
}

export interface DeclarationResponse {
  run_id: string;
  generated_at: string;
  jurisdiction: string;
  invoice: Record<string, unknown> | null;
  bill_of_lading: Record<string, unknown> | null;
  compliance: {
    status: "PASS" | "WARN" | "BLOCK";
    issues: ComplianceIssue[];
  } | null;
  hs_codes: Array<{ description: string; hs_code: string }>;
}

// --- API calls ---

/** Upload both trade documents together (invoice + bill of lading). */
export async function uploadDocument(
  invoiceFile: File,
  blFile: File,
  country: CountryCode
): Promise<DocumentResponse> {
  const form = new FormData();
  form.append("invoice_pdf", invoiceFile);
  form.append("bl_pdf", blFile);
  form.append("country", country);
  const { data } = await api.post<DocumentResponse>("/upload/", form);
  return data;
}

export async function getDocument(id: string): Promise<DocumentResponse> {
  const { data } = await api.get<DocumentResponse>(`/upload/${id}`);
  return data;
}

export async function listDocuments(): Promise<DocumentResponse[]> {
  const { data } = await api.get<DocumentResponse[]>("/upload/");
  return data;
}

export async function createWorkflow(
  documentId: string,
  country: CountryCode
): Promise<WorkflowResponse> {
  const { data } = await api.post<WorkflowResponse>("/workflow/", {
    document_id: documentId,
    country,
  });
  return data;
}

export async function getWorkflow(id: string): Promise<WorkflowResponse> {
  const { data } = await api.get<WorkflowResponse>(`/workflow/${id}`);
  return data;
}

export async function listWorkflows(): Promise<WorkflowResponse[]> {
  const { data } = await api.get<WorkflowResponse[]>("/workflow/");
  return data;
}

export async function getRunStatus(runId: string): Promise<StatusResponse> {
  const { data } = await api.get<StatusResponse>(`/workflow/status/${runId}`);
  return data;
}

export async function getDeclaration(runId: string): Promise<DeclarationResponse> {
  const { data } = await api.get<DeclarationResponse>(`/workflow/declaration/${runId}`);
  return data;
}

export async function resumeWorkflow(
  runId: string,
  corrections: { gross_weight_kg?: number }
): Promise<WorkflowResponse> {
  const { data } = await api.post<WorkflowResponse>(
    `/workflow/resume/${runId}`,
    corrections,
  );
  return data;
}
