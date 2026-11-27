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
  /** The folder it was put in inside its preset's folder on the piles; null: none. */
  folder_id?: number | null;
  edl: Edl;
  caption: string;
  caption_status: "queued" | "writing" | "done" | "failed" | null;
  caption_sources: { title: string; url: string }[];
  caption_notes: string[];
  caption_song: string;
  caption_artist: string;
  caption_topic: string;
  /** The reel's posts in Buffer and how each went; empty from older servers. */
  posts?: BufferPost[];
};

export type Verdict = "approved" | "posted" | "dismissed";

/** A folder made inside a preset's folder on the Autopilot piles (or inside another one). */
export type PileFolder = {
  id: number;
  preset: string;
  /** The folder it is in; null: straight in the preset's folder. */
  parent_id: number | null;
  name: string;
  /** The token of the link that shows its videos to anyone who has it; null: not shared. */
  share?: string | null;
};

/** A shared folder as a client sees it (no login). */
export type SharedFolder = {
  name: string;
  clips: { id: number; title: string; duration: number; caption: string; made: string }[];
};

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
  /** Start from the latest videos already there, newest first, after skipping the
   * `skip_newest` newest; new ones still go first as they come. */
  from_latest: boolean;
  skip_newest: number;
  /** Only videos about this are edited ("GTA 5"); null: every video. */
  topic: string | null;
  /** The folder on the piles its videos go in; null: straight in the preset's folder. */
  folder: string | null;
  /** Each day's videos in a folder of that day inside it ("23-NOV"). */
  dated: boolean;
};

export type FeedItem = {
  title: string;
  source: string;
  job_id: string | null;
  status: string;
  created_at: string;
  /** Why it was passed over (status "passed over"). */
  reason?: string | null;
};

export type Feed = FeedFields & {
  id: number;
  last_checked: string | null;
  last_result: string | null;
  counts: Record<string, number>;
  recent: FeedItem[];
};

/** A label group (see songfacts.GROUPS on the server). */
export type LabelGroup = "UMG" | "Sony" | "Warner" | "other";

/** The strike list's entry a song is on. */
export type Blocked = {
  id: number;
  song: string;
  artist: string;
  label_group: LabelGroup;
  what: "blocked" | "removed" | "strike";
  what_label: string;
  note: string;
};

/** A job's song, once known, and what to know before posting it (strikes.notice). */
export type SongNotice = {
  song: string;
  artist: string;
  year: string | null;
  year_from: string;
  label: string;
  /** "" when no label was found. */
  group: LabelGroup | "";
  /** "Sony Music"; "" for any other label. */
  group_name: string;
  label_from: string;
  /** On the strike list: a label blocked it before. */
  blocked: Blocked | null;
  /** A label group the app warns about. */
  notified: boolean;
  /** Out in the last few years: labels block new songs more often. */
  new: boolean;
};

/** One song on the strike list. */
export type Strike = Blocked & {
  /** Who put it there; null for the list's first entry. */
  added_by: string | null;
  job_id: string | null;
  created_at: string;
  /** An admin, or whoever added it. */
  can_remove: boolean;
};

export type StrikeList = {
  entries: Strike[];
  groups: LabelGroup[];
  whats: Record<Blocked["what"], string>;
};

export type NewStrike = {
  song: string;
  artist: string;
  label_group: LabelGroup;
  what: Blocked["what"];
  note: string;
  job_id?: string | null;
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
  /** False when the person chose to keep the whole video, not cut to the preset's length. */
  trim?: boolean;
  /** Its song, once known, with its label and year; absent from older servers. */
  song?: SongNotice | null;
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
  /** Its song's year and label, "" when not known (older servers: absent). */
  year?: string;
  label?: string;
  label_group?: LabelGroup | "";
  /** Its year is a new song's. */
  new?: boolean;
  /** Its song is on the strike list (a PDF's songs may be). */
  blocked?: Blocked | null;
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
/** Where a research searches (see research.SITES); "songs" with the youtube_songs feature,
 * "tiktok" while the server has the TikTok gateway. */
export type Site = "instagram" | "shorts" | "x" | "tiktok" | "songs";

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
  /** The primary account: the one admin who makes, changes and removes the other admins. */
  primary?: boolean;
  notice_accepted: boolean;
  /** Features this account tries first (trial_reel, fun, niche_finder, trim_choice). */
  features?: string[];
  /** Its Buffer posts that failed in the last week (the header shows the number). */
  buffer_failed?: number;
  /** The server has the TikTok gateway: TikTok links download and researches search TikTok. */
  tiktok?: boolean;
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
  primary?: boolean;
  /** An admin whose presets only they and the primary account see. */
  private_presets?: boolean;
  /** For an employee: the admin (by id) whose alone, with the primary account, their presets are. */
  presets_by?: string | null;
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

/** Whether downloads can sign in to a site (Instagram, YouTube), since and until when
 * (never the cookies). */
export type SiteLogin = { set: boolean; since: string | null; until: string | null };

/** The TikTok gateway (a second Mac on a VPN, reached by an SSH tunnel), as checked just now. */
export type TikTokGateway = {
  /** TIKTOK_PROXY is set on the server. */
  set: boolean;
  proxy: string;
  /** TikTok is reached without a gateway ("direct"). */
  direct: boolean;
  /** The tunnel answers; null when there is none to check. */
  tunnel: boolean | null;
  tunnel_said: string;
  /** TikTok answers through it; null when not set. */
  tiktok: boolean | null;
  tiktok_said: string;
  seconds: number | null;
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
export type BufferPostStatus =
  | "scheduled"
  | "sending"
  | "needs_approval"
  | "draft"
  | "sent"
  | "error"
  | "gone"
  | "retried"
  | "unknown";
/** A post the editor put in Buffer, followed until it went out. */
export type BufferPost = {
  id: string;
  post_id: string;
  channel: string;
  service: string;
  output_id: number | null;
  title: string;
  text: string;
  status: BufferPostStatus;
  due_at: string | null;
  sent_at: string | null;
  /** Buffer's words when it failed, and the network's own. */
  error: string;
  raw_error: string;
  draft: boolean;
  /** Its video went to Buffer from Cloudflare R2, not from this machine. */
  hosted: boolean;
  /** Sent again by the editor itself after the first try failed. */
  auto: boolean;
  /** The failed post this one sends again. */
  retry_of: string | null;
  /** Instagram's last download of the video from this machine stopped before the end. */
  instagram_cut: boolean;
  created_at: string;
};
export type BufferPosts = { posts: BufferPost[]; auto_retry: boolean; hosted: boolean };
/** Cloudflare R2 for the videos Buffer fetches, as an admin set it up. */
export type Hosting = {
  configured: boolean;
  enabled: boolean;
  files: number;
  account_id?: string;
  access_key_id?: string;
  /** The secret's last four letters; the secret itself never comes back. */
  secret_tail?: string;
  bucket?: string;
  public_url?: string;
  bytes?: number;
  cap?: number;
};
export type HostingForm = {
  account_id: string;
  access_key_id: string;
  /** Empty keeps the one saved before. */
  secret_access_key: string;
  bucket: string;
  public_url: string;
};

/** A connected Instagram account (direct Meta API, not Buffer). */
/** How an account's comments are answered: not at all, drafted for a person, or by itself. */
export type ReplyMode = "off" | "review" | "auto";

export type IgChannel = {
  id: string;
  username: string;
  name: string;
  avatar: string | null;
  connected_at: string;
  /** null: the token doesn't expire. */
  token_expires_at: string | null;
  reply_mode: ReplyMode;
  reply_style: string;
  /** Replies sent by themselves in an hour, at most. */
  reply_limit: number;
  /** A reply goes out at a random time this many minutes after it was drafted. */
  reply_wait_min: number;
  reply_wait_max: number;
  /** While Meta refuses the app, no looking until then; null: not paused. */
  paused_until: string | null;
  /** Whether that pause is still on. */
  paused: boolean;
  checked_at: string | null;
  check_note: string;
  /** Comments waiting for something to be done. */
  waiting: number;
};

/** What a channel autopilot is set to (channel/routes.py PilotSettings). */
export type ChannelPilotSettings = {
  active: boolean;
  /** Everything but the post itself: nothing goes out to Instagram. */
  dry_run: boolean;
  /** A research account's id; "new": one made for the page's own name; null: none. */
  research_account_id: string | null;
  preset: string;
  /** What its researches look for besides what the page's best posts show. */
  focus: string;
  /** Rate a recognisable but less obvious song over an artist's most famous hit. */
  surprise: boolean;
  research_hour: number;
  posts_per_day: number;
  /** "HH:MM", local time on the server. */
  post_times: string[];
  /** Reels a person approves before it posts alone. */
  needs: number;
};

export type ChannelPilot = ChannelPilotSettings & {
  id: string;
  channel_id: string;
  username: string;
  approved: number;
  /** Why it paused itself (a post failed); empty otherwise. */
  paused_reason: string;
  note: string;
  research_handle: string | null;
  research: {
    id: string;
    status: string;
    message: string;
    progress: number;
    found: number;
    created_at: string;
  } | null;
  counts: Record<string, number>;
  posted_today: number;
  next_post: string | null;
  learned_at: string | null;
  learn_note: string;
  posts_read: number;
  /** Whether Meta gives the page's insights (views, reach, saves); null: not asked yet. */
  insights_allowed: boolean | null;
  best: { caption: string; likes: number | null; comments: number | null; link: string }[];
};

export type ChannelPilots = {
  channels: { id: string; username: string; avatar: string | null; pilot: ChannelPilot | null }[];
  presets: { name: string; label: string; split: boolean }[];
  research_accounts: { id: string; handle: string }[];
  defaults: ChannelPilotSettings;
};

export type ChannelCheck = { name: string; ok: boolean; detail: string };

export type ChannelPostStatus =
  | "queued"
  | "checking"
  | "skipped"
  | "unused"
  | "editing"
  | "reviewing"
  | "look"
  | "awaiting"
  | "ready"
  | "due"
  | "posting"
  | "posted"
  | "failed";

export type ChannelPost = {
  id: number;
  status: ChannelPostStatus;
  reason: string;
  code: string;
  url: string;
  title: string;
  song: string;
  artist: string;
  heard_song: string;
  heard_artist: string;
  score: number;
  relevance: number | null;
  duration: number | null;
  checks: ChannelCheck[];
  approved: boolean;
  job_id: string | null;
  job_status: string | null;
  output_id: number | null;
  has_thumbnail: boolean;
  caption: string;
  post_id: string | null;
  permalink: string;
  dry_run: boolean;
  posted_at: string | null;
  /** The post's numbers, as far as Meta gives them: likes, comments, views, reach, saved... */
  insights: Record<string, number>;
  insights_at: string | null;
  created_at: string;
  /** Its edit's song with its label and year, once known. */
  notice?: SongNotice | null;
};

export type IgComment = {
  id: string;
  channel_id: string;
  channel: string;
  post_id: string;
  post_caption: string;
  post_url: string;
  username: string;
  text: string;
  /** new, drafted, look (a person should answer), sending, sent, skipped, failed */
  status: string;
  draft: string;
  why: string;
  error: string;
  sent_by: string;
  commented_at: string | null;
  sent_at: string | null;
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
  /** Messages opened to it from the finder by everyone in the team; the person's own. */
  dm_opens?: number;
  my_dm_opens?: number;
  /** The last of them; null when none. */
  dm_opened_at?: string | null;
  /** Searches that showed it to the person (more than 1: shown again). */
  times_shown?: number;
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

/** One item of a scene's visual (a list row, a timeline date, an icon, a side of a compare). */
export type AnimationItem = {
  emoji: string;
  text: string;
  year: string;
  value: string;
  motion: string;
  /** A World map card's picture: what it shows, its file once painted, and a number to
   * paint it again. */
  art?: string;
  image?: string;
  art_seed?: number;
};

export type AnimationVisualKind =
  | "title"
  | "stat"
  | "icons"
  | "compare"
  | "list"
  | "timeline"
  | "place"
  | "quote"
  | "reveal"
  | "dots"
  | "scene"
  | "country"
  | "moment"
  | "verse";

/** A character in a scene: who (from the cast), what they do and feel, what they hold, a line. */
export type AnimationActor = {
  name: string;
  action: string;
  mood: string;
  prop: string;
  says: string;
};

export type AnimationCastMember = { name: string; look: string };

export type AnimationVisual = {
  kind: AnimationVisualKind;
  text: string;
  highlight: string;
  kicker: string;
  value: string;
  prefix: string;
  suffix: string;
  detail: string;
  by: string;
  answer: string;
  emoji: string;
  items: AnimationItem[];
  /** Character scenes: where it happens and who acts. */
  setting?: string;
  actors?: AnimationActor[];
  /** People standing around in a character scene (0-8). */
  crowd?: number;
  /** The painted picture behind the scene: what it shows, its file once painted, a number
   * changed to paint it again. */
  art?: string;
  image?: string;
  art_seed?: number;
  /** A country scene's country as the map knows it, when the narration says it otherwise. */
  country?: string;
  /** A Song story moment's camera move over its picture. */
  camera?: string;
  /** A singer edit's line: how the singer is shown (hero, close, far, print, mirror, echo,
   * silhouette, words, painted). */
  shot?: string;
};

export type AnimationScene = { narration: string; visual: AnimationVisual; hold: number };

/** What the reel says and shows (animate/plan.py). */
export type AnimationPlan = {
  v: number;
  title: string;
  kind: "facts" | "lesson" | "story" | "news";
  style: "glow" | "paper" | "story" | "kinetic" | "stick" | "map" | "song" | "singer";
  voice: string;
  mood: string;
  scenes: AnimationScene[];
  cast?: AnimationCastMember[];
  cta: string;
  caption: string;
  sources: { title: string; url: string }[];
  /** A World map reel's look: white picture cards, gold horror or neon food labels. */
  look?: MapLook;
  /** A Song story's song: the part it plays and its lyric lines (animate/songhook.py). */
  song?: AnimationSong | null;
  /** A singer edit's song and photos (set by the studio, never edited here). */
  singer?: SingerEdit | null;
};

export type SingerEdit = {
  query: string;
  title: string;
  artist: string;
  duration: number;
  photos: { image: string; cutout: string }[];
  lines: { text: string; start: number; end: number }[];
};

export type MapLook = "cards" | "horror" | "neon";

export type AnimationSongWord = {
  text: string;
  start: number;
  end: number;
  key: boolean;
  color: string;
  /** What its letters do while it is sung (fall, rise, shake, scatter, spin); "" none. */
  effect?: string;
};

export type AnimationSong = {
  query: string;
  title: string;
  artist: string;
  video: string;
  url: string;
  duration: number;
  start: number;
  end: number;
  language: string;
  lyrics: string;
  lines: {
    text: string;
    said: string;
    start: number;
    end: number;
    words: AnimationSongWord[];
    /** Pictures shown over it, a scene each (two for a line on screen a long time). */
    pictures?: number;
    /** Which of `pieces` it is sung in (a reel joined from two parts). */
    piece?: number;
    /** The word its second row on screen starts with; 0: set by width. */
    row?: number;
  }[];
  choices: { start: number; end: number; first: string; score: number }[];
  /** Which of the choices it plays: one, or the two joined into it. */
  playing?: number[];
  /** A joined reel's stretches of the song, one after the other (empty: start-end). */
  pieces?: { start: number; end: number }[];
  people: AnimationPerson[];
  story: string;
};

/** Someone in a Song story: how they look in every picture, and their portrait once painted. */
export type AnimationPerson = {
  name: string;
  look: string;
  /** The portrait every picture with them is painted from (where the likeness model is). */
  image?: string;
  /** A number to paint the portrait again. */
  art_seed?: number;
};

/** Another reel of a Song story's song, made for another of its parts. */
export type AnimationPart = {
  id: string;
  title: string;
  status: Animation["status"];
  message: string;
  error: string | null;
  /** The reel the others were made from. */
  first: boolean;
  /** The stretches of the song it plays (seconds). */
  spans: [number, number][];
  render: AnimationRender | null;
};

/** player.js load() data, scenes with their times (animate/timeline.py player_data). */
export type AnimationPlayerData = {
  duration: number;
  scenes: { kind: string; start: number; end: number }[];
} & Record<string, unknown>;

export type AnimationRender = {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  output_id: number | null;
};

export type Animation = {
  id: string;
  idea: string;
  kind: string;
  style: string;
  voice: string;
  length: string;
  /** Painted pictures behind the scenes. */
  images?: boolean;
  status: "queued" | "drafting" | "changing" | "painting" | "voicing" | "ready" | "failed";
  message: string;
  error: string | null;
  notes: string[];
  title: string;
  scenes: number;
  duration: number | null;
  can_undo: boolean;
  created_at: string;
  updated_at: string;
  render: AnimationRender | null;
  /** Made by the autopilot: what became of its post (null: made by hand). */
  autopilot_id?: string | null;
  post_status?:
    "rendering" | "awaiting" | "posting" | "posted" | "approved" | "skipped" | "failed" | null;
  post_error?: string | null;
  post_id?: string | null;
  /** Claims the fact check found no source for. */
  doubts?: string[];
  /** A Song story made for another part of the song another reel found. */
  made_from?: string | null;
  /** The other reels of a Song story's song (with the full reel only). */
  parts?: AnimationPart[];
  plan?: AnimationPlan | null;
  player?: AnimationPlayerData | null;
};

export type AnimationAutopilot = {
  exists: boolean;
  active: boolean;
  niche: string;
  topics: string[];
  hour: number;
  per_day: number;
  kind: string;
  style: string;
  voice: string;
  length: string;
  channel_id: string | null;
  auto_post: boolean;
  images: boolean;
  approved: number;
  needs: number;
  used: string[];
  last_on: string | null;
  waiting: number;
  channels: { id: string; username: string }[];
};

/** The account's morning Kick clips (Kick Clipper tab, "Every morning"). */
export type KickDaily = {
  on: boolean;
  /** Local hour it starts (0-23). */
  hour: number;
  streamers: string[];
  /** Clips from GTA streams, filed into "DD-MON-GTA". */
  gta: number;
  /** Clips from the other streams, filed into "DD-MON-FOOTAGE". */
  footage: number;
  /** Newest finished streams looked at per streamer. */
  streams: number;
  last_on: string;
  last_result: string;
  facecam_preset: string;
  full_preset: string;
};

export type KickDailyRequest = Pick<
  KickDaily,
  "on" | "hour" | "streamers" | "gta" | "footage" | "streams"
>;

/** A Kick streamer the account clips (Kick Clipper tab); `live`: clipped while it streams. */
export type KickStreamer = {
  id: string;
  slug: string;
  username: string;
  avatar: string;
  live: boolean;
  preset: string;
  per_hour: number;
  folder: string;
  /** The folder its clips go to on the piles ("Kick <name>" when none is given). */
  folder_shown: string;
  is_live: boolean;
  last_checked: string | null;
  last_result: string | null;
  link: string;
  created_at: string;
};

export type KickStream = {
  id: number;
  uuid: string;
  title: string;
  started: string;
  /** Seconds; 0 while live. */
  duration: number;
  live: boolean;
  thumbnail: string;
  views: number;
  link: string;
  has_video: boolean;
};

export type KickStreams = {
  slug: string;
  username: string;
  avatar: string;
  is_live: boolean;
  streams: KickStream[];
};

export type KickClipJob = {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  outputs: { id: number; duration: number; title: string; preset: string }[];
};

/** What a moment's clip opens on, once its hook was judged (scores 0-10). */
export type KickHook = {
  said?: string;
  grabs?: string;
  score?: number;
  payoff?: number;
  alone?: number;
  what?: string;
  tease?: string;
  /** The crazy thing done or said, and its kind (wild_reaction, argument, ...). */
  crazy?: string;
  kind?: string;
  /** A cold open: this stretch of the stream plays first. */
  flash?: [number, number];
};

/** A moment a hunt found: the window cut from the stream (seconds into it) and its clip. */
export type KickMoment = {
  start: number;
  end: number;
  peak: number;
  score: number;
  why: string[];
  chat_at: number | null;
  hook?: KickHook;
  job_id: string | null;
  error?: string;
  link: string;
  job: KickClipJob | null;
};

export type KickHunt = {
  id: string;
  slug: string;
  username: string;
  title: string;
  uuid: string;
  stream_id: number;
  start: number;
  end: number;
  count: number;
  preset: string;
  live: boolean;
  folder: string;
  status: "queued" | "reading" | "listening" | "cutting" | "done" | "failed";
  message: string;
  error: string | null;
  created_at: string;
  streamer_id: string | null;
  link: string;
  moments: KickMoment[];
};

export type KickHuntRequest = {
  link: string;
  start?: string;
  end?: string;
  count?: number;
  preset?: string;
  folder?: string;
};

export type AnimationVoice = { id: string; name: string; group: string; about: string };

/** A way in for Claude to the Animations tab: a key made here, or an app connected by OAuth. */
export type McpConnection = {
  id: string;
  label: string;
  kind: "key" | "oauth";
  /** The app's own name, for a connection made through the sign-in page. */
  app: string;
  /** A key's last letters, to tell it apart; the key itself never comes back. */
  tail: string;
  posting: boolean;
  created_at: string;
  last_used_at: string | null;
};
export type McpConnections = {
  /** The MCP endpoint's address, as this browser reaches the site. */
  url: string;
  connections: McpConnection[];
  /** A key just made, shown this once. */
  key?: string;
};
/** An app asking to connect, on the sign-in page (/connect/). */
export type McpAsk = {
  app: string;
  /** The host (and port) the browser goes back to after the answer. */
  goes_to: string;
  /** Claude's own address: claude.ai, or Claude Code on this computer. */
  known: boolean;
  account: string;
  studio: boolean;
};

export type AnimationBrief = {
  idea: string;
  kind: string;
  style: string;
  voice: string;
  length: string;
  images: boolean;
  /** A singer edit's photos of the singer (names from uploadAnimationPhoto). */
  photos?: string[];
};

export const api = {
  login: (username: string, password: string) => request("POST", "/login", { username, password }),
  logout: () => request("POST", "/logout"),
  me: () => request<Me>("GET", "/me"),
  fun: () => request<FunStats>("GET", "/fun"),
  animations: () => request<Animation[]>("GET", "/animations"),
  animation: (id: string) => request<Animation>("GET", `/animations/${id}`),
  createAnimation: (body: AnimationBrief) => request<Animation>("POST", "/animations", body),
  uploadAnimationPhoto: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    // The photo made ready (trimmed, text painted out, cropped to the singer) and what was done.
    return request<{ name: string; notes?: string[] }>("POST", "/animation-photos", form);
  },
  editAnimation: (id: string, plan: AnimationPlan) =>
    request<Animation>("PUT", `/animations/${id}`, { plan }),
  setAnimation: (
    id: string,
    body: { style?: string; voice?: string; images?: boolean; look?: MapLook },
  ) => request<Animation>("PATCH", `/animations/${id}`, body),
  changeAnimation: (
    id: string,
    body: { request: string; scene?: number; choice?: number; join?: number[] },
  ) => request<Animation>("POST", `/animations/${id}/change`, body),
  makeAnimationParts: (id: string) => request<Animation>("POST", `/animations/${id}/parts`),
  retryAnimation: (id: string) => request<Animation>("POST", `/animations/${id}/retry`),
  undoAnimation: (id: string) => request<Animation>("POST", `/animations/${id}/undo`),
  renderAnimation: (id: string) => request<Animation>("POST", `/animations/${id}/render`),
  deleteAnimation: (id: string) => request<{ ok: boolean }>("DELETE", `/animations/${id}`),
  animationVoices: () => request<AnimationVoice[]>("GET", "/animation-voices"),
  animationIdeas: (style: string) =>
    request<{ ideas: string[] }>("GET", `/animation-ideas?style=${encodeURIComponent(style)}`),
  animationAutopilot: () => request<AnimationAutopilot>("GET", "/animation-autopilot"),
  setAnimationAutopilot: (
    body: Omit<
      AnimationAutopilot,
      "exists" | "approved" | "needs" | "used" | "last_on" | "waiting" | "channels"
    >,
  ) => request<AnimationAutopilot>("PUT", "/animation-autopilot", body),
  animationAutopilotNow: () => request<Animation>("POST", "/animation-autopilot/now"),
  mcpConnections: () => request<McpConnections>("GET", "/mcp/connections"),
  newMcpKey: (body: { label: string; posting: boolean }) =>
    request<McpConnections>("POST", "/mcp/keys", body),
  setMcpPosting: (id: string, posting: boolean) =>
    request<{ ok: boolean; posting: boolean }>("PATCH", `/mcp/connections/${id}`, { posting }),
  removeMcpConnection: (id: string) => request<{ ok: boolean }>("DELETE", `/mcp/connections/${id}`),
  mcpAsk: (ask: string) => request<McpAsk>("GET", `/mcp/requests/${encodeURIComponent(ask)}`),
  answerMcpAsk: (ask: string, body: { allow: boolean; posting: boolean }) =>
    request<{ redirect: string }>("POST", `/mcp/requests/${encodeURIComponent(ask)}`, body),
  kickStreamers: () => request<KickStreamer[]>("GET", "/kick/streamers"),
  addKickStreamer: (body: {
    name: string;
    live?: boolean;
    preset?: string;
    per_hour?: number;
    folder?: string;
  }) => request<KickStreamer>("POST", "/kick/streamers", body),
  changeKickStreamer: (
    id: string,
    body: Partial<Pick<KickStreamer, "live" | "preset" | "per_hour" | "folder">>,
  ) => request<KickStreamer>("PATCH", `/kick/streamers/${id}`, body),
  removeKickStreamer: (id: string) => request<{ ok: boolean }>("DELETE", `/kick/streamers/${id}`),
  kickStreams: (name: string) =>
    request<KickStreams>("GET", `/kick/streams?name=${encodeURIComponent(name)}`),
  kickDaily: () => request<KickDaily>("GET", "/kick/daily"),
  setKickDaily: (body: KickDailyRequest) => request<KickDaily>("PUT", "/kick/daily", body),
  kickHunts: () => request<KickHunt[]>("GET", "/kick/hunts"),
  kickHunt: (id: string) => request<KickHunt>("GET", `/kick/hunts/${id}`),
  startKickHunt: (body: KickHuntRequest) => request<KickHunt>("POST", "/kick/hunts", body),
  retryKickHunt: (id: string) => request<KickHunt>("POST", `/kick/hunts/${id}/retry`),
  removeKickHunt: (id: string) => request<{ ok: boolean }>("DELETE", `/kick/hunts/${id}`),
  approveAnimation: (id: string) => request<Animation>("POST", `/animations/${id}/approve`),
  skipAnimation: (id: string) => request<Animation>("POST", `/animations/${id}/skip`),
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
  /** The person opened a message to the account: counted, and kept out of their searches for 30 days. */
  nicheDmOpened: (id: number) => request<NicheAccount>("POST", `/niche/accounts/${id}/dm`),
  nicheMessage: () => request<{ message: string }>("GET", "/niche-message"),
  setNicheMessage: (message: string) =>
    request<{ message: string }>("PUT", "/niche-message", { message }),
  wrapped: (month?: string) =>
    request<FunWrapped>("GET", `/fun/wrapped${month ? `?month=${month}` : ""}`),
  acceptNotice: () => request("POST", "/notice"),
  system: () => request<SystemInfo>("GET", "/system"),
  updateYtdlp: () => request<{ yt_dlp: string; note: string }>("POST", "/system/update-ytdlp"),
  tiktokGateway: () => request<TikTokGateway>("GET", "/system/tiktok-gateway"),
  siteLogin: (site: "instagram" | "youtube") => request<SiteLogin>("GET", `/system/${site}-login`),
  setSiteLogin: (site: "instagram" | "youtube", cookies: string) =>
    request<SiteLogin>("PUT", `/system/${site}-login`, { cookies }),
  forgetSiteLogin: (site: "instagram" | "youtube") =>
    request<SiteLogin>("DELETE", `/system/${site}-login`),
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
    request<{ results: BufferResult[]; link: string; hosted?: boolean }>(
      "POST",
      `/outputs/${outputId}/buffer`,
      body,
    ),
  bufferPosts: () => request<BufferPosts>("GET", "/buffer/posts"),
  /** Sends a failed post again: now, or at `dueAt` (ISO). The new post. */
  retryBufferPost: (id: string, dueAt: string | null = null) =>
    request<BufferPost>("POST", `/buffer/posts/${id}/retry`, { due_at: dueAt }),
  dismissBufferPost: (id: string) => request<{ ok: boolean }>("DELETE", `/buffer/posts/${id}`),
  setBufferAutoRetry: (on: boolean) =>
    request<{ auto_retry: boolean }>("PUT", "/buffer/auto-retry", { on }),
  hosting: () => request<Hosting>("GET", "/admin/hosting"),
  setHosting: (form: HostingForm) => request<Hosting>("PUT", "/admin/hosting", form),
  switchHosting: (on: boolean) => request<Hosting>("POST", `/admin/hosting/${on ? "on" : "off"}`),
  channelPilots: () => request<ChannelPilots>("GET", "/channel-autopilot"),
  saveChannelPilot: (channelId: string, body: ChannelPilotSettings) =>
    request<ChannelPilot>("PUT", `/channel-autopilot/${channelId}`, body),
  channelFindNow: (channelId: string) =>
    request<ChannelPilot>("POST", `/channel-autopilot/${channelId}/find`),
  channelLearnNow: (channelId: string) =>
    request<ChannelPilot>("POST", `/channel-autopilot/${channelId}/learn`),
  channelPosts: (channelId: string, show: "queue" | "posted" | "skipped") =>
    request<ChannelPost[]>("GET", `/channel-autopilot/${channelId}/posts?show=${show}`),
  approveChannelPost: (id: number) => request<ChannelPost>("POST", `/channel-posts/${id}/approve`),
  skipChannelPost: (id: number) => request<ChannelPost>("POST", `/channel-posts/${id}/skip`),
  retryChannelPost: (id: number) => request<ChannelPost>("POST", `/channel-posts/${id}/retry`),
  igChannels: () => request<IgChannel[]>("GET", "/instagram/channels"),
  removeIgChannel: (id: string) => request<{ ok: boolean }>("DELETE", `/instagram/channels/${id}`),
  changeIgChannel: (
    id: string,
    body: Partial<
      Pick<
        IgChannel,
        "reply_mode" | "reply_style" | "reply_limit" | "reply_wait_min" | "reply_wait_max"
      >
    >,
  ) => request<IgChannel>("PATCH", `/instagram/channels/${id}`, body),
  checkIgComments: (id: string) =>
    request<{ ok: boolean }>("POST", `/instagram/channels/${id}/check`),
  igComments: (show: "open" | "sent" | "skipped" = "open") =>
    request<IgComment[]>("GET", `/instagram/comments?show=${show}`),
  sendIgReply: (id: string, text: string) =>
    request<IgComment>("POST", `/instagram/comments/${encodeURIComponent(id)}/send`, { text }),
  skipIgComment: (id: string) =>
    request<IgComment>("POST", `/instagram/comments/${encodeURIComponent(id)}/skip`),
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
  strikes: () => request<StrikeList>("GET", "/strikes"),
  addStrike: (entry: NewStrike) => request<Strike>("POST", "/strikes", entry),
  removeStrike: (id: number) => request<{ ok: boolean }>("DELETE", `/strikes/${id}`),
  clearJobs: () => request<{ deleted: number; freed_mb: number }>("POST", "/jobs/clear"),
  storage: () => request<{ keep_days: number }>("GET", "/system/storage"),
  setStorage: (keepDays: number) =>
    request<{ keep_days: number }>("PUT", "/system/storage", { keep_days: keepDays }),
  createJobs: (sources: string[], presets: string[], notes: string, trim = true) =>
    request<Job[]>("POST", "/jobs", { sources, presets, notes: notes || null, trim }),
  retryJob: (id: string) => request<Job>("POST", `/jobs/${id}/retry`),
  regenerate: (id: string, preset: string, notes: string) =>
    request<Job>("POST", `/jobs/${id}/regenerate`, { preset, notes: notes || null }),
  feeds: () => request<Feed[]>("GET", "/feeds"),
  createFeed: (feed: FeedFields) => request<Feed>("POST", "/feeds", feed),
  updateFeed: (id: number, feed: FeedFields) => request<Feed>("PUT", `/feeds/${id}`, feed),
  deleteFeed: (id: number) => request("DELETE", `/feeds/${id}`),
  checkFeed: (id: number) => request<Feed>("POST", `/feeds/${id}/check`),
  pileCounts: () => request<{ ready: number; look: number }>("GET", "/piles"),
  pile: (name: "ready" | "look") => request<Job[]>("GET", `/piles/${name}`),
  pileFolders: () => request<PileFolder[]>("GET", "/pile-folders"),
  makePileFolder: (preset: string, parentId: number | null, name: string) =>
    request<PileFolder>("POST", "/pile-folders", { preset, parent_id: parentId, name }),
  renamePileFolder: (id: number, name: string) =>
    request<PileFolder>("PATCH", `/pile-folders/${id}`, { name }),
  deletePileFolder: (id: number) => request<{ ok: boolean }>("DELETE", `/pile-folders/${id}`),
  shareFolder: (id: number) => request<PileFolder>("POST", `/pile-folders/${id}/share`),
  unshareFolder: (id: number) => request<PileFolder>("DELETE", `/pile-folders/${id}/share`),
  pastePile: (preset: string, outputIds: number[], folderIds: number[], to: number | null) =>
    request<{ videos: number; folders: number }>("POST", "/pile-folders/paste", {
      preset,
      output_ids: outputIds,
      folder_ids: folderIds,
      to,
    }),
  moveOutput: (outputId: number, folderId: number | null) =>
    request<Job>("POST", `/outputs/${outputId}/folder`, { folder_id: folderId }),
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
  removeAccount: (id: string, deleteWork: boolean, myPassword = "") =>
    request("DELETE", `/admin/users/${id}?delete_work=${deleteWork}`, {
      my_password: myPassword,
    }),
  changeRole: (id: string, role: "member" | "admin", myPassword: string) =>
    request<Account>("POST", `/admin/users/${id}/role`, { role, my_password: myPassword }),
  setPrivatePresets: (id: string, on: boolean, by: string | null = null) =>
    request<{ private_presets: boolean; presets_by: string | null; presets: number }>(
      "PUT",
      `/admin/users/${id}/private-presets`,
      { on, by },
    ),
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
