export type Clip = { start: number; end: number; reason: string; score: number };

export type Edl = {
  clips: Clip[];
  hook: string;
  title: string;
  zooms: { time: number; scale: number; duration: number }[];
  crop_mode: string;
  variant: number;
  decisions: string[];
};

export type Output = {
  id: number;
  preset: string;
  duration: number;
  title: string;
  planner: string;
  renders: number;
  issues: string[];
  has_thumbnail: boolean;
  has_subtitles: boolean;
  confidence: number;
  doubts: string[];
  verdict: Verdict | null;
  pile: "ready" | "look" | null;
  edl: Edl;
  caption: string;
  caption_status: "queued" | "writing" | "done" | "failed" | null;
  caption_sources: { title: string; url: string }[];
  caption_notes: string[];
  caption_song: string;
  caption_artist: string;
};

export type Verdict = "approved" | "posted" | "dismissed";

export type FeedFields = {
  name: string;
  kind: "folder" | "link";
  location: string;
  presets: string[];
  notes: string | null;
  enabled: boolean;
  active_from: number | null;
  active_to: number | null;
  batch: number;
  include_existing: boolean;
};

export type FeedItem = {
  title: string;
  source: string;
  job_id: string | null;
  status: string;
  created_at: string;
};

export type Feed = FeedFields & {
  id: number;
  last_checked: string | null;
  last_result: string | null;
  counts: Record<string, number>;
  recent: FeedItem[];
};

export type Job = {
  id: string;
  source: string;
  title: string;
  presets: string[];
  notes: string | null;
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  parent_id: string | null;
  created_at: string;
  outputs: Output[];
  /** Who started it, shown to the admin. */
  owner: string | null;
};

export type Me = {
  user: string;
  id: string;
  role: "admin" | "member";
  notice_accepted: boolean;
};

export type Account = {
  id: string;
  username: string;
  role: "admin" | "member";
  created_at: string;
  last_seen_at: string | null;
  jobs: number;
  presets: number;
};

export type JobEvent = { stage: string; message: string; progress: number; time: string };

// Presets are edited as plain objects; the server validates them against its schema.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Preset = { name: string; label: string; platform: string } & Record<string, any>;

export type StyleDraft = {
  id: string;
  source: string;
  status: "queued" | "running" | "done" | "failed";
  message: string;
  preset: Preset | null;
  notes: string[];
  error: string | null;
  created_at: string;
};

export type SystemInfo = {
  accelerator: string;
  transcriber: string;
  encoder: string;
  llm_model: string;
  vision_model: string;
  whisper_model: string;
  yt_dlp: string;
};

import { loginPath } from "@/lib/next-path.mjs";
import { sendFile } from "@/lib/upload.mjs";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `request failed with ${status}`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(`/api${path}`, init);
  const data = await response.json().catch(() => null);
  return settle<T>(path, response.status, response.statusText, data);
}

let loggingIn = false;

/** Off to the login page, which comes back here afterwards. The returned promise never
 * settles, so no error flashes up while the page changes. */
export function toLogin(): Promise<never> {
  if (!loggingIn) {
    loggingIn = true;
    const here = window.location.pathname + window.location.search;
    window.location.assign(new URL(loginPath(here), window.location.origin));
  }
  return new Promise<never>(() => {});
}

async function settle<T>(path: string, status: number, statusText: string, data: unknown) {
  if (status === 401 && !path.startsWith("/login")) return toLogin();
  if (status < 200 || status >= 300) {
    throw new ApiError(status, (data as { detail?: unknown } | null)?.detail ?? statusText);
  }
  return data as T;
}

export type UploadOptions = { onProgress?: (fraction: number) => void; signal?: AbortSignal };

/** The query naming whose presets, for the admin managing an employee's; empty for one's own. */
const whose = (owner?: string | null) => (owner ? `?owner=${encodeURIComponent(owner)}` : "");

export const api = {
  login: (username: string, password: string) => request("POST", "/login", { username, password }),
  logout: () => request("POST", "/logout"),
  me: () => request<Me>("GET", "/me"),
  acceptNotice: () => request("POST", "/notice"),
  system: () => request<SystemInfo>("GET", "/system"),
  updateYtdlp: () => request<{ yt_dlp: string; note: string }>("POST", "/system/update-ytdlp"),
  fonts: () => request<string[]>("GET", "/fonts"),
  presets: (owner?: string | null) => request<Preset[]>("GET", `/presets${whose(owner)}`),
  savePreset: (preset: Preset, owner?: string | null) =>
    request<Preset>("PUT", `/presets/${preset.name}${whose(owner)}`, preset),
  deletePreset: (name: string, owner?: string | null) =>
    request("DELETE", `/presets/${name}${whose(owner)}`),
  sharePreset: (name: string) => request<{ sent_to: string[] }>("POST", `/presets/${name}/share`),
  presetYaml: (name: string, owner?: string | null) =>
    request<{ yaml: string }>("GET", `/presets/${name}/yaml${whose(owner)}`),
  presetSchema: () =>
    request<{ properties: Record<string, { default?: unknown }> }>("GET", "/presets/schema"),
  learnStyle: (source: string, name: string | null) =>
    request<StyleDraft>("POST", "/styles", { source, name }),
  styleDraft: (id: string) => request<StyleDraft>("GET", `/styles/${id}`),
  jobs: () => request<Job[]>("GET", "/jobs"),
  job: (id: string) => request<Job>("GET", `/jobs/${id}`),
  deleteJob: (id: string) => request("DELETE", `/jobs/${id}`),
  createJobs: (sources: string[], presets: string[], notes: string) =>
    request<Job[]>("POST", "/jobs", { sources, presets, notes: notes || null }),
  regenerate: (id: string, preset: string, notes: string) =>
    request<Job>("POST", `/jobs/${id}/regenerate`, { preset, notes: notes || null }),
  feeds: () => request<Feed[]>("GET", "/feeds"),
  createFeed: (feed: FeedFields) => request<Feed>("POST", "/feeds", feed),
  updateFeed: (id: number, feed: FeedFields) => request<Feed>("PUT", `/feeds/${id}`, feed),
  deleteFeed: (id: number) => request("DELETE", `/feeds/${id}`),
  checkFeed: (id: number) => request<Feed>("POST", `/feeds/${id}/check`),
  pileCounts: () => request<{ ready: number; look: number }>("GET", "/piles"),
  pile: (name: "ready" | "look") => request<Job[]>("GET", `/piles/${name}`),
  verdict: (outputId: number, verdict: Verdict | null) =>
    request<Job>("POST", `/outputs/${outputId}/verdict`, { verdict }),
  output: (outputId: number) => request<Job>("GET", `/outputs/${outputId}`),
  writeCaption: (outputId: number, named?: { song: string; artist: string }) =>
    request<Job>("POST", `/outputs/${outputId}/caption`, named),
  accounts: () => request<Account[]>("GET", "/admin/users"),
  createAccount: (username: string, password: string) =>
    request<Account>("POST", "/admin/users", { username, password }),
  resetPassword: (id: string, password: string) =>
    request("POST", `/admin/users/${id}/password`, { password }),
  removeAccount: (id: string, deleteWork: boolean) =>
    request("DELETE", `/admin/users/${id}?delete_work=${deleteWork}`),
  upload: async (file: File, options: UploadOptions = {}) => {
    const { status, statusText, body } = await sendFile("/api/uploads", file, options);
    return settle<{ source: string; name: string }>("/uploads", status, statusText, body);
  },
};

export const videoUrl = (output: Output, download = false) =>
  `/api/outputs/${output.id}/video${download ? "?download=true" : ""}`;

export const thumbnailUrl = (output: Output, width?: number) =>
  `/api/outputs/${output.id}/thumbnail${width ? `?width=${width}` : ""}`;

export const subtitlesUrl = (output: Output) => `/api/outputs/${output.id}/subtitles`;

export function errorText(error: unknown): string {
  if (error instanceof ApiError && Array.isArray(error.detail)) {
    return error.detail
      .map((d: { loc?: string[]; msg?: string }) => {
        // Pydantic prefixes errors raised by validators with "Value error, ".
        const msg = (d.msg ?? "").replace(/^Value error, /, "");
        return d.loc?.length ? `${d.loc.join(".")}: ${msg}` : msg;
      })
      .join("\n");
  }
  return error instanceof Error ? error.message : String(error);
}
