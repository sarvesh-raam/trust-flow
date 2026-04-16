import axios from "axios";

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
  const { data } = await api.get(`/workflow/${runId}`);
  return data;
};

export { api };
