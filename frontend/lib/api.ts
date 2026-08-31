import type {
  AskResponse,
  DemoInfo,
  DevicesResponse,
  Fragment,
  HealthResponse,
  SessionDetail,
  SessionSummary,
  VerdictStatus,
  VerificationResponse,
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

  devices: () => request<DevicesResponse>("/api/devices"),

  createFromDevice: (path: string) =>
    request<SessionSummary>("/api/sessions/from-device", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  demoInfo: () => request<DemoInfo>("/api/demo"),

  createDemo: () => request<SessionSummary>("/api/sessions/demo", { method: "POST" }),

  verification: (sessionId: string) =>
    request<VerificationResponse>(`/api/sessions/${sessionId}/verification`),

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

/**
 * Verdict presentation.
 *
 * `label` is the plain-English name a person reads first and `meaning` is the
 * sentence that tells them what to do about it. The internal names are kept in
 * `code` because the API, the CLI and the exported reports all use them, and a
 * user who reads one and then the other should not have to work out that
 * "Partial" and "PARTIAL" are the same thing.
 */
export const STATUS_STYLES: Record<
  VerdictStatus,
  { label: string; code: string; meaning: string; color: string; bg: string; border: string }
> = {
  RECOVERABLE: {
    label: "Fully recovered",
    code: "RECOVERABLE",
    meaning: "This file came back complete and should open normally.",
    color: "text-signal-recover",
    bg: "bg-signal-recover/10",
    border: "border-signal-recover/30",
  },
  PARTIAL: {
    label: "Partly damaged",
    code: "PARTIAL",
    meaning: "Some of this file survived. It may open with parts missing, or not at all.",
    color: "text-signal-partial",
    bg: "bg-signal-partial/10",
    border: "border-signal-partial/30",
  },
  METADATA_ONLY: {
    label: "Name only",
    code: "METADATA_ONLY",
    meaning: "The card still remembers this file existed, but its contents are gone.",
    color: "text-signal-meta",
    bg: "bg-signal-meta/10",
    border: "border-signal-meta/30",
  },
  JUNK: {
    label: "Not a real file",
    code: "JUNK",
    meaning: "This looked like a file at first glance but the evidence says otherwise.",
    color: "text-signal-junk",
    bg: "bg-signal-junk/10",
    border: "border-signal-junk/30",
  },
};

/**
 * Colours the entropy strip by what each band means, not by a gradient ramp.
 *
 * The order still reads as a ramp to the eye — cold and dark for nothing there,
 * warming through structure and text, to gold and ember where the bytes are
 * dense enough to be a photo or an archive — so the strip can be scanned for
 * shape without consulting the legend, while each colour keeps a fixed meaning.
 */
export const BAND_COLORS: Record<string, string> = {
  empty: "#241f1c",
  structured: "#5fc9df",
  text: "#4bd894",
  mixed: "#f0a92b",
  compressed: "#f2643a",
};
