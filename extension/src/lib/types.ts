export interface AccountConfig {
  handle: string;
  label: string;
}

export interface Settings {
  pat: string;
  owner: string;
  repo: string;
  branch: string;
  autoCapture: boolean;
  configuredAt: number | null;
  /** When true, the background periodically scrolls all open x.com tabs to
   * the bottom — works around X de-prioritizing deep pagination on the
   * `with_replies` tab. */
  autoScroll: boolean;
  /** Seconds between auto-scroll ticks (clamped 3..60 in the UI). */
  autoScrollIntervalSec: number;
}

export type ConnectionStatus =
  | 'unknown'
  | 'not-configured'
  | 'ok'
  | 'auth-error'
  | 'rate-limited'
  | 'network-error';

export interface ConnectionState {
  status: ConnectionStatus;
  login: string | null;
  checkedAt: string | null;
  error: string | null;
  /** GitHub's reported default branch for the configured repo, when known. */
  defaultBranch: string | null;
  /** Whether settings.branch exists on the remote, as of the last verify.
   * null = unknown / not probed yet. A non-default branch is fine as long
   * as it actually exists; this is what governs the sidebar warning. */
  configuredBranchExists: boolean | null;
}

export interface MediaItem {
  media_id: string;
  media_type: 'photo' | 'video' | 'animated_gif';
  original_url: string;
  release_asset_url: string | null;
  sha256: string | null;
  bytes: number | null;
  duration_sec: number | null;
  width: number | null;
  height: number | null;
  alt_text: string | null;
  archive_status: 'pending' | 'archived' | 'failed' | 'expired';
  archive_attempts: number;
  last_attempt_at: string | null;
}

export interface UrlEntity {
  short: string;
  expanded: string;
  display: string;
}

export interface EngagementSnapshot {
  captured_at: string;
  likes: number;
  retweets: number;
  replies: number;
  quotes: number;
  views: number | null;
  bookmarks: number | null;
}

export type TweetType = 'original' | 'retweet' | 'quote' | 'reply';

export interface CommunityNote {
  /** ID of the Community Note (formerly Birdwatch note). */
  note_id: string | null;
  /** Header text — usually "Readers added context". */
  title: string | null;
  /** Compact title (mobile / space-constrained UI). */
  short_title: string | null;
  /** The actual body of the reader-supplied context. */
  summary: string | null;
  /** Where X links the reader to for full note + ratings. */
  destination_url: string | null;
  /** When the extension first saw this note attached to the tweet. */
  observed_at: string;
}

export interface CanonicalTweet {
  tweet_id: string;
  account_handle: string;
  account_id: string;
  posted_at: string;
  first_captured_at: string;
  last_seen_at: string;
  deletion_detected_at: string | null;
  tweet_url: string;
  tweet_type: TweetType;
  reply_to_tweet_id: string | null;
  reply_to_account: string | null;
  quoted_tweet_id: string | null;
  retweeted_tweet_id: string | null;
  text: string;
  text_resolved: string;
  lang: string | null;
  hashtags: string[];
  mentions: string[];
  urls: UrlEntity[];
  media: MediaItem[];
  like_count: number;
  retweet_count: number;
  reply_count: number;
  quote_count: number;
  view_count: number | null;
  bookmark_count: number | null;
  engagement_history: EngagementSnapshot[];
  /** Reader-supplied context attached by X's Community Notes program.
   * `null` when no note is attached (the vast majority of tweets). */
  community_note: CommunityNote | null;
  /** Heuristic: the timeline returned a "show more" link without the
   * accompanying `note_tweet` block, so the archived `text` is likely
   * the 280-char head of a long tweet. Re-fetching the tweet's detail
   * page returns the full body. */
  is_truncated: boolean;
  wayback_url: string | null;
  wayback_submitted_at: string | null;
  capture_source: 'extension' | 'manual';
  capture_run_id: string;
  schema_version: 1;
}

export interface CapturePayload {
  schema_version: 1;
  capture_run_id: string;
  account_handle: string;
  captured_at: string;
  endpoint: string;
  user_agent: string;
  source_url: string | null;
  tweets: CanonicalTweet[];
}

export interface SeenPayload {
  schema_version: 1;
  capture_run_id: string;
  account_handle: string;
  captured_at: string;
  tweet_ids_observed: string[];
}

export interface QuarantinePayload {
  schema_version: 1;
  reason: string;
  endpoint: string;
  captured_at: string;
  source_url: string | null;
  error: { message: string; stack: string | null };
  raw: unknown;
}

export type LogLevel = 'info' | 'warn' | 'error';

export interface LogEvent {
  ts: string;
  level: LogLevel;
  msg: string;
  context: Record<string, unknown>;
}

export interface AccountCounter {
  todayCount: number;
  todayDate: string;
  lastCaptureAt: string | null;
  totalCommitted: number;
  bufferedCount: number;
}

export interface RefetchQueueState {
  /** Total tweets queued for full-text refetch across all handles. */
  total: number;
  /** Whether a refetch loop is currently iterating. */
  running: boolean;
  /** ISO of the last refetch tick, if any. */
  lastTickAt: string | null;
}

export interface ExtensionState {
  version: string;
  settings: Omit<Settings, 'pat'> & { patSuffix: string; patSet: boolean };
  connection: ConnectionState;
  accounts: AccountConfig[];
  counters: Record<string, AccountCounter>;
  /** Auto-scroll runtime state. Tab count comes from the SW's live tab query. */
  autoScroll: { active: boolean; tabCount: number };
  refetchQueue: RefetchQueueState;
}

export type RuntimeMessage =
  | { type: 'graphql-capture'; endpoint: string; url: string; response: unknown }
  | { type: 'content-alive'; url: string }
  | { type: 'page-hook-active'; url: string }
  | { type: 'log-content-event'; level: LogLevel; msg: string; url: string }
  | { type: 'get-state' }
  | { type: 'capture-now'; handle: string }
  | { type: 'capture-all' }
  | { type: 'capture-this-page' }
  | { type: 'flush-all' }
  | { type: 'flush-handle'; handle: string }
  | { type: 'toggle-auto-capture'; on: boolean }
  | { type: 'toggle-auto-scroll'; on: boolean }
  | { type: 'set-auto-scroll-interval'; seconds: number }
  | { type: 'start-refetch' }
  | { type: 'cancel-refetch' }
  | { type: 'refresh-accounts' }
  | { type: 'verify-connection' }
  | { type: 'clear-activity' }
  | { type: 'open-options' }
  | { type: 'open-viewer' }
  | { type: 'log-event'; event: LogEvent }
  | { type: 'state-changed'; state: ExtensionState }
  | { type: 'activity-tail'; events: LogEvent[] };
