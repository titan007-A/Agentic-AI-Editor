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
  /** The preset's name as its owner sees it ("Iconic Spanish"); empty from older servers. */
  preset_label?: string;
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
  caption_topic: string;
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
  /** Presets that couldn't tell which song it is: the job page asks for it. */
  unnamed: string[];
  /** Roughly how long a queued or running job still has, in seconds; null if unknown. */
  eta_seconds?: number | null;
};

export type ResearchItem = {
  id: number;
  rank: number;
  code: string;
  url: string;
  /** The search that found it first. */
  query: string;
  /** Its display name. */
  account: string;
  /** Its Instagram name (a YouTube channel's), shown as @handle. */
  handle: string;
  caption: string;
  likes: number | null;
  comments: number | null;
  /** YouTube and X show them; Instagram doesn't without signing in. */
  views: number | null;
  /** The best version the site offers: its picture, kbit/s and (YouTube's) seconds. */
  width: number | null;
  height: number | null;
  bitrate: number | null;
  duration: number | null;
  posted_at: string | null;
  thumbnail: string;
  /** 0-1: how well it fits the description; null when the model couldn't rate it. */
  relevance: number | null;
  why: string;
  /** "Song - Artist" when the model could tell. */
  song: string;
  /** What of the account (or description) it matches. */
  matches: string;
  /** The preset it suits best, and why. */
  preset: string;
  preset_why: string;
  /** Likes and comments a day since it was posted. */
  per_day: number | null;
  /** 0-1: how far it spread among the reels found. */
  virality: number;
  score: number;
  /** In a PDF research, the number of the song it is for. */
  entry: number | null;
  /** Shown by an earlier research. */
  seen: boolean;
  /** Edited since. */
  used: boolean;
  /** The person's feedback, which later researches learn from. */
  verdict: "good" | "bad" | null;
  verdict_reason: string;
  /** Its account is one the person hid. */
  hidden: boolean;
};

/** Feedback on a reel; null takes it back. */
export type Feedback = {
  verdict: "good" | "bad" | null;
  reason?: string;
  hide_account?: boolean;
};

export type HiddenAccount = { handle: string; created_at: string };

/** How an account's own reels found were doing when it was last learned, and before. */
export type Performance = {
  taken_at: string | null;
  previous_at: string | null;
  reels: {
    code: string;
    url: string;
    caption: string;
    likes: number | null;
    comments: number | null;
    posted_at: string | null;
    per_day: number | null;
    /** Likes since the snapshot before; null when it wasn't in that one. */
    gained: number | null;
  }[];
  average: number | null;
  previous_average: number | null;
};

/** What the person's recent researches found spreading: reels posted lately, fastest
 * first, and songs several accounts posted reels of. */
export type Trends = {
  reels: ResearchItem[];
  songs: { song: string; reels: number; accounts: number; per_day: number }[];
};

/** A song a PDF lists, with the words to write above and below its reel. */
export type PdfEntry = {
  singer: string;
  song: string;
  top: string;
  bottom: string;
  /** From a numbered list read by rule: its year, number, section and section's preset. */
  year?: string;
  number?: number;
  section?: string;
  preset?: string;
  /** What the list is of; song and singer lines go to the edit only for songs. */
  kind?: string;
};

/** A song a PDF lists, to choose from: read by rule from a numbered list or by the model. */
export type PdfListed = {
  number: number;
  section: string;
  song: string;
  singer: string;
  year: string;
  lines: string[];
  labelled: Record<string, string>;
};

/** How the chosen songs of a PDF are researched: `keep` holds their positions in the
 * research's listing. */
export type PdfChoice = {
  keep: number[];
  swap: boolean;
  line: "top" | "bottom" | "none";
  presets: Record<string, string>;
  understood?: string;
};

/** Where a research searches: Instagram reels, YouTube Shorts. */
export type Site = "instagram" | "shorts" | "x";

/** A question the researcher asks before searching, with suggested answers. */
export type ResearchQuestion = { question: string; choices: string[] };

export type ResearchAnswer = { question: string; answer: string };

/** A line of the chat on a research; the researcher's may have started a follow-up. */
export type ResearchMessage = {
  id: number;
  role: "user" | "researcher";
  text: string;
  answered: boolean;
  follow_up_id: string | null;
  created_at: string;
};

export type Research = {
  id: string;
  /** What to find; for an account, an optional focus. */
  description: string;
  account_id: string | null;
  handle: string;
  /** Where it searched. */
  sites: Site[];
  /** Started by its account's morning research. */
  daily: boolean;
  /** "pdf": songs from a PDF, each with its words above and below; else "search". */
  mode: "search" | "pdf";
  entries: PdfEntry[];
  /** Every item a PDF lists, while it is "choosing" (empty in lists and after). */
  listing: PdfListed[];
  /** What a PDF lists: "songs", "films", "military aircraft"... */
  kind: string;
  /** The research whose chat asked for this one. */
  parent_id: string | null;
  /** "asking": thinking of questions (none yet), then waiting for the answers; a PDF's is
   * "reading" while the model reads it and "choosing" until its items are chosen. */
  status: "asking" | "reading" | "choosing" | "queued" | "running" | "done" | "failed";
  questions: ResearchQuestion[];
  answers: ResearchAnswer[];
  messages: ResearchMessage[];
  message: string;
  progress: number;
  /** The request as the model understood it, in English. */
  brief: string;
  queries: string[];
  /** Reels found but left out, edited before. */
  used: number;
  /** Reels looked up and rated. */
  considered: number;
  /** Reels found but not given, by why. */
  left_out: Record<string, number>;
  /** The best score of the rated reels not given. */
  cutoff: number | null;
  error: string | null;
  created_at: string;
  found: number;
  owner: string | null;
  items: ResearchItem[];
};

/** One of the person's Instagram accounts, and the presets they edit its reels with. */
export type InstagramAccount = {
  id: string;
  handle: string;
  presets: string[];
  /** What the researcher read in its own reels; empty when none were found. */
  summary: string;
  learned_at: string | null;
  /** Its own reels found. */
  own: number;
  best: { code: string; likes: number | null; caption: string }[];
  learn_status: "idle" | "queued" | "running" | "done" | "failed";
  learn_message: string;
  learn_error: string | null;
  /** Pages it takes reels from: "instagram:name", "youtube:@name". */
  sources: string[];
  /** A research for it every morning at this hour (server time); null when off. */
  daily_hour: number | null;
  /** The day the last one started. */
  daily_on: string | null;
  created_at: string;
};

export type Me = {
  user: string;
  id: string;
  role: "admin" | "member";
  notice_accepted: boolean;
  /** Features this account tries first (trial_reel, fun). */
  features?: string[];
};

export type FunBadge = {
  id: string;
  name: string;
  description: string;
  goal: number;
  progress: number;
  /** When it was won, India time; null while still to win. */
  won_at: string | null;
};

export type FunStats = {
  me: {
    user: string;
    today: number;
    week: number;
    month: number;
    total: number;
    streak: number;
    best_streak: number;
    made_today: boolean;
    captions: number;
    posts: number;
    minutes_saved: number;
  };
  team_minutes_saved: number;
  /** The minutes a reel takes by hand that time saved is counted at. */
  manual_minutes: number;
  leaderboard: Record<"today" | "week" | "month", { user: string; reels: number }[]>;
  badges: FunBadge[];
  quest: { kind: string; text: string; goal: number; progress: number; done: boolean; day: string };
};

export type FunWrapped = {
  month: string;
  label: string;
  months: string[];
  reels: number;
  active_days: number;
  best_day: { date: string; reels: number } | null;
  best_run: number;
  top_preset: { name: string; reels: number } | null;
  top_song: { name: string; reels: number } | null;
  top_artist: { name: string; reels: number } | null;
  busiest_hour: number | null;
  night_reels: number;
  captions: number;
  posts: number;
  team_place: number | null;
  team_size: number;
  team_reels: number;
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

/** Which of the admin's presets one employee has, and the presets only they have. */
export type Assigned = {
  presets: { name: string; label: string; has: boolean }[];
  own: { name: string; label: string }[];
};

export type JobEvent = { stage: string; message: string; progress: number; time: string };

/** A preset as it is named for everyone who has it (the admin's rename card). */
export type PresetName = { name: string; label: string; who: string[] };

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

import { failureText, NO_CONNECTION } from "@/lib/failure.mjs";
import { loginPath } from "@/lib/next-path.mjs";
import { sendFile } from "@/lib/upload.mjs";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(failureText(status, detail));
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
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new Error(NO_CONNECTION);
  }
  const data = await response.json().catch(() => null);
  return settle<T>(path, response.status, response.statusText, data);
}

export type BufferMode = "addToQueue" | "shareNext" | "shareNow" | "customScheduled";
export type BufferChannel = {
  id: string;
  name: string;
  service: string;
  avatar: string | null;
  paused: boolean;
  kind: string;
  /** Disconnected or locked in Buffer: nothing can be posted there. */
  broken: boolean;
  /** An Instagram personal profile: Buffer only posts reels to Creator or Business ones. */
  personal: boolean;
};
export type BufferKey = {
  id: string;
  label: string;
  /** The key's last four letters; the key itself never comes back. */
  tail: string;
  channels: BufferChannel[];
  error: string;
  refreshed_at: string | null;
  used_today: number;
};
export type BufferPresetLink = { name: string; label: string; channels: string[] };
export type BufferState = { keys: BufferKey[]; presets: BufferPresetLink[] };
/** What the Buffer dialog of one reel needs: its owner's keys and its preset's channels. */
export type OutputBuffer = {
  keys: BufferKey[];
  preset: string;
  preset_label: string;
  linked: string[];
  /** Whose reel it is, when it isn't the signed-in person's. */
  owner: string | null;
};
export type BufferResult = {
  channel: string;
  ok: boolean;
  post?: string;
  error?: string;
  /** Sent as a trial reel: kept in Buffer's drafts, under the "Trial reel" tag if made. */
  draft?: boolean;
  tagged?: boolean;
  note?: string;
};

/** A connected Instagram account (direct Meta API, not Buffer). */
export type IgChannel = {
  id: string;
  username: string;
  name: string;
  avatar: string | null;
  connected_at: string;
  token_expires_at: string | null;
};

export type AssetKind = "image" | "video" | "audio" | "lut";

export type PreviewShape = "16:9" | "4:3" | "1:1" | "4:5" | "9:16";

/** A still of what `preset` makes on a video of `shape`, and what looks wrong in it; null
 * when the preset has settings the server refuses, so the caller keeps the last still. */
export async function presetPreview(
  preset: Preset,
  shape: PreviewShape,
  signal?: AbortSignal,
): Promise<{ image: Blob; notes: string[] } | null> {
  const response = await fetch(`/api/presets/preview?shape=${encodeURIComponent(shape)}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(preset),
    signal,
  });
  if (response.status === 401) return toLogin();
  if (response.status === 422) return null;
  if (!response.ok) throw new Error(response.statusText);
  let notes: string[] = [];
  try {
    notes = JSON.parse(response.headers.get("X-Preview-Notes") ?? "[]");
  } catch {}
  return { image: await response.blob(), notes };
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

export type NicheAccount = {
  id: number;
  handle: string;
  name: string;
  followers: number | null;
  posts: number | null;
  about: string;
  fit: number | null;
  why: string;
  /** "page", "person" or "business", as the model judged it; empty for older finds. */
  kind: string;
  vote: -1 | 0 | 1;
  reason: string;
  url: string;
  message_url: string;
};

/** Pages (theme, fan, meme, news pages), people's own accounts, or any account. */
export type NicheKind = "pages" | "people" | "any";

export type NicheFilters = {
  /** Missing on searches made before there was a choice: they looked for pages. */
  kind?: NicheKind;
  min_followers: number;
  max_followers: number | null;
  language: string;
  country: string;
};

export type NicheSearch = {
  id: string;
  niche: string;
  filters: NicheFilters;
  status: "queued" | "running" | "done" | "failed";
  message: string;
  progress: number;
  notes: string[];
  error: string | null;
  created_at: string;
  count: number;
  /** The schedule that started it; null when started by hand. */
  schedule_id?: string | null;
  accounts?: NicheAccount[];
};

export type NicheSchedule = {
  id: string;
  niche: string;
  filters: NicheFilters;
  hour: number;
  /** Monday 0; empty for every day. */
  days: number[];
  active: boolean;
  last_on: string | null;
  created_at: string;
};

export const api = {
  login: (username: string, password: string) => request("POST", "/login", { username, password }),
  logout: () => request("POST", "/logout"),
  me: () => request<Me>("GET", "/me"),
  fun: () => request<FunStats>("GET", "/fun"),
  nicheSearches: () => request<NicheSearch[]>("GET", "/niche"),
  nicheSearch: (id: string) => request<NicheSearch>("GET", `/niche/${id}`),
  startNiche: (body: { niche: string } & NicheFilters) =>
    request<NicheSearch>("POST", "/niche", body),
  deleteNiche: (id: string) => request<{ ok: boolean }>("DELETE", `/niche/${id}`),
  nicheSchedules: () => request<NicheSchedule[]>("GET", "/niche/schedules"),
  addNicheSchedule: (body: { niche: string; hour: number; days: number[] } & NicheFilters) =>
    request<NicheSchedule>("POST", "/niche/schedules", body),
  changeNicheSchedule: (
    id: string,
    body: Partial<Pick<NicheSchedule, "active" | "hour" | "days">>,
  ) => request<NicheSchedule>("PATCH", `/niche/schedules/${id}`, body),
  deleteNicheSchedule: (id: string) => request<{ ok: boolean }>("DELETE", `/niche/schedules/${id}`),
  voteNiche: (id: number, vote: -1 | 0 | 1, reason = "") =>
    request<NicheAccount>("POST", `/niche/accounts/${id}/vote`, { vote, reason }),
  wrapped: (month?: string) =>
    request<FunWrapped>("GET", `/fun/wrapped${month ? `?month=${month}` : ""}`),
  acceptNotice: () => request("POST", "/notice"),
  system: () => request<SystemInfo>("GET", "/system"),
  updateYtdlp: () => request<{ yt_dlp: string; note: string }>("POST", "/system/update-ytdlp"),
  fonts: () => request<string[]>("GET", "/fonts"),
  presets: (owner?: string | null) => request<Preset[]>("GET", `/presets${whose(owner)}`),
  savePreset: (preset: Preset, owner?: string | null) =>
    request<Preset>("PUT", `/presets/${preset.name}${whose(owner)}`, preset),
  deletePreset: (name: string, owner?: string | null) =>
    request("DELETE", `/presets/${name}${whose(owner)}`),
  sharePreset: (name: string, to: string[]) =>
    request<{ sent_to: string[] }>("POST", `/presets/${name}/share`, { to }),
  buffer: () => request<BufferState>("GET", "/buffer"),
  addBufferKey: (label: string, key: string) =>
    request<BufferState>("POST", "/buffer/keys", { label, key }),
  renameBufferKey: (id: string, label: string) =>
    request<BufferState>("PATCH", `/buffer/keys/${id}`, { label }),
  refreshBufferKey: (id: string) => request<BufferState>("POST", `/buffer/keys/${id}/refresh`),
  removeBufferKey: (id: string) => request<BufferState>("DELETE", `/buffer/keys/${id}`),
  linkPresetChannels: (preset: string, channels: string[]) =>
    request<BufferState>("PUT", `/buffer/presets/${preset}`, { channels }),
  outputBuffer: (outputId: number) => request<OutputBuffer>("GET", `/outputs/${outputId}/buffer`),
  postToBuffer: (
    outputId: number,
    body: {
      channels: string[];
      text: string;
      mode: BufferMode;
      due_at: string | null;
      first_comment: string;
      remember: boolean;
      trial?: boolean;
    },
  ) =>
    request<{ results: BufferResult[]; link: string }>("POST", `/outputs/${outputId}/buffer`, body),
  igChannels: () => request<IgChannel[]>("GET", "/instagram/channels"),
  removeIgChannel: (id: string) => request<{ ok: boolean }>("DELETE", `/instagram/channels/${id}`),
  postToInstagram: (
    outputId: number,
    body: { channel_id: string; caption: string; share_to_feed: boolean },
  ) =>
    request<{ post_id: string; channel: string }>("POST", `/outputs/${outputId}/instagram`, body),
  uploadAsset: (file: File, kind: AssetKind) => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    return request<{ path: string; name: string }>("POST", "/assets", form);
  },
  presetYaml: (name: string, owner?: string | null) =>
    request<{ yaml: string }>("GET", `/presets/${name}/yaml${whose(owner)}`),
  presetSchema: () =>
    request<{ properties: Record<string, { default?: unknown }> }>("GET", "/presets/schema"),
  learnStyle: (source: string, name: string | null) =>
    request<StyleDraft>("POST", "/styles", { source, name }),
  styleDraft: (id: string) => request<StyleDraft>("GET", `/styles/${id}`),
  jobs: () => request<Job[]>("GET", "/jobs"),
  job: (id: string) => request<Job>("GET", `/jobs/${id}`),
  presetNames: () => request<PresetName[]>("GET", "/presets/names"),
  renamePresets: (labels: Record<string, string>) =>
    request<PresetName[]>("PUT", "/presets/names", { labels }),
  deleteJob: (id: string) => request("DELETE", `/jobs/${id}`),
  clearJobs: () => request<{ deleted: number; freed_mb: number }>("POST", "/jobs/clear"),
  storage: () => request<{ keep_days: number }>("GET", "/system/storage"),
  setStorage: (keepDays: number) =>
    request<{ keep_days: number }>("PUT", "/system/storage", { keep_days: keepDays }),
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
  writeCaption: (outputId: number, named?: { song: string; artist: string } | { topic: string }) =>
    request<Job>("POST", `/outputs/${outputId}/caption`, named),
  researches: () => request<Research[]>("GET", "/research"),
  research: (id: string) => request<Research>("GET", `/research/${id}`),
  startResearch: (
    description: string,
    accountId: string | null = null,
    skipQuestions = false,
    sites: Site[] = ["instagram", "shorts"],
  ) =>
    request<Research>("POST", "/research", {
      description,
      account_id: accountId,
      skip_questions: skipQuestions,
      sites,
    }),
  researchPdf: (file: File, sites: Site[]) => {
    const form = new FormData();
    form.append("file", file);
    form.append("sites", sites.join(","));
    return request<Research>("POST", "/research/pdf", form);
  },
  chooseByWords: (id: string, words: string) =>
    request<PdfChoice>("POST", `/research/${id}/choose-by-words`, { words }),
  chooseSongs: (id: string, choice: PdfChoice, perResearch: number, kind: string) =>
    request<Research>("POST", `/research/${id}/choose`, {
      keep: choice.keep,
      swap: choice.swap,
      line: choice.line,
      presets: choice.presets,
      per_research: perResearch,
      kind,
    }),
  answerResearch: (id: string, answers: ResearchAnswer[]) =>
    request<Research>("POST", `/research/${id}/answers`, { answers }),
  chatOnResearch: (id: string, text: string) =>
    request<Research>("POST", `/research/${id}/chat`, { text }),
  deleteResearch: (id: string) => request("DELETE", `/research/${id}`),
  giveFeedback: (itemId: number, feedback: Feedback) =>
    request<Research>("POST", `/research/items/${itemId}/feedback`, feedback),
  hiddenAccounts: () => request<HiddenAccount[]>("GET", "/research/hidden"),
  researchTrends: () => request<Trends>("GET", "/research/trends"),
  unhideAccount: (handle: string) =>
    request("DELETE", `/research/hidden/${encodeURIComponent(handle)}`),
  instagramAccounts: () => request<InstagramAccount[]>("GET", "/research/accounts"),
  addInstagramAccount: (handle: string, presets: string[]) =>
    request<InstagramAccount>("POST", "/research/accounts", { handle, presets }),
  updateInstagramAccount: (id: string, presets: string[]) =>
    request<InstagramAccount>("PUT", `/research/accounts/${id}`, { presets }),
  pagePerformance: (id: string) =>
    request<Performance>("GET", `/research/accounts/${id}/performance`),
  setSourcePages: (id: string, sources: string[]) =>
    request<InstagramAccount>("PUT", `/research/accounts/${id}/sources`, { sources }),
  setDailyResearch: (id: string, hour: number | null) =>
    request<InstagramAccount>("PUT", `/research/accounts/${id}/daily`, { hour }),
  learnInstagramAccount: (id: string) =>
    request<InstagramAccount>("POST", `/research/accounts/${id}/learn`),
  removeInstagramAccount: (id: string) => request("DELETE", `/research/accounts/${id}`),
  accounts: () => request<Account[]>("GET", "/admin/users"),
  // An admin is only made with the admin's own password typed again (myPassword).
  createAccount: (
    username: string,
    password: string,
    role: "member" | "admin" = "member",
    myPassword = "",
  ) =>
    request<Account>("POST", "/admin/users", {
      username,
      password,
      role,
      my_password: myPassword,
    }),
  // Another admin's password changes only with the admin's own (myPassword).
  resetPassword: (id: string, password: string, myPassword = "") =>
    request("POST", `/admin/users/${id}/password`, { password, my_password: myPassword }),
  assignedPresets: (id: string) => request<Assigned>("GET", `/admin/users/${id}/presets`),
  assignPresets: (id: string, names: string[], replace: boolean) =>
    request<Assigned>("PUT", `/admin/users/${id}/presets`, { names, replace }),
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
