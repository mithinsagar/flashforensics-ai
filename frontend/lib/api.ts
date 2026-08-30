import type {
  AskResponse,
  Fragment,
  HealthResponse,
  SessionDetail,
  SessionSummary,
  VerdictStatus,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // response had no JSON body, the status text is the best available message
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  listSessions: () => request<{ sessions: SessionSummary[] }>("/api/sessions"),

  createFromPath: (path: string) =>
    request<SessionSummary>("/api/sessions/from-path", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  /**
   * Uploads bypass the JSON helper because multipart bodies must set their own
   * boundary, and the browser only does that when Content-Type is left alone.
   */
  upload: async (file: File): Promise<SessionSummary> => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_BASE}/api/sessions`, { method: "POST", body: form });
    if (!response.ok) {
      throw new ApiError(await response.text(), response.status);
    }
    return response.json();
  },

  analyze: (sessionId: string) =>
    request<{ session_id: string; status: string }>(`/api/sessions/${sessionId}/analyze`, {
      method: "POST",
    }),

  getSession: (sessionId: string) => request<SessionDetail>(`/api/sessions/${sessionId}`),

  fragments: (sessionId: string, params: { status?: VerdictStatus; category?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.category) query.set("category", params.category);
    query.set("limit", String(params.limit ?? 500));
    return request<{ total: number; fragments: Fragment[] }>(
      `/api/sessions/${sessionId}/fragments?${query}`,
    );
  },

  ask: (sessionId: string, question: string) =>
    request<AskResponse>(`/api/sessions/${sessionId}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),

  exportAll: (sessionId: string, status: string) =>
    request<{ exported: number; archive: string; bytes: number }>(
      `/api/sessions/${sessionId}/export?status=${status}`,
      { method: "POST" },
    ),

  downloadUrl: (sessionId: string, fragmentId: string) =>
    `${API_BASE}/api/sessions/${sessionId}/fragments/${fragmentId}/download`,

  streamUrl: (sessionId: string) => `${API_BASE}/api/sessions/${sessionId}/stream`,
};

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${index === 0 ? value : value.toFixed(1)} ${units[index]}`;
}

export function formatHex(offset: number): string {
  return `0x${offset.toString(16).toUpperCase().padStart(8, "0")}`;
}

export const STATUS_STYLES: Record<VerdictStatus, { label: string; color: string; bg: string; border: string }> = {
  RECOVERABLE: {
    label: "Recoverable",
    color: "text-signal-recover",
    bg: "bg-signal-recover/10",
    border: "border-signal-recover/30",
  },
  PARTIAL: {
    label: "Partial",
    color: "text-signal-partial",
    bg: "bg-signal-partial/10",
    border: "border-signal-partial/30",
  },
  METADATA_ONLY: {
    label: "Metadata only",
    color: "text-signal-meta",
    bg: "bg-signal-meta/10",
    border: "border-signal-meta/30",
  },
  JUNK: {
    label: "Junk",
    color: "text-signal-junk",
    bg: "bg-signal-junk/10",
    border: "border-signal-junk/30",
  },
};

/** Colours the entropy strip by what each band means, not by a gradient ramp. */
export const BAND_COLORS: Record<string, string> = {
  empty: "#1b2029",
  structured: "#3d5a80",
  text: "#4fb286",
  mixed: "#e0a458",
  compressed: "#d1495b",
};
