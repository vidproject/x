export type AccountCategory = 'core' | 'government' | 'officials' | 'public_figures' | 'public';

export interface AccountConfig {
  handle: string;
  label: string;
  /** Over-category for this account. Untracked authors that fall through
   * to `_misc.parquet` are implicitly `public` and don't appear in
   * accounts.yaml at all. */
  category: AccountCategory;
}

/** One tag entry attached to a tweet in `data/tags.json`. Tags are an
 * annotation layer — they're never written into the per-account
 * parquets. See docs/TAGS.md and config/tag_taxonomy.yaml. */
export interface TweetTag {
  /** Namespaced tag, e.g. "subject:detainee", "genre:statistics". */
  tag: string;
  /** Set to true when the tag was applied with non-trivial uncertainty
   * (image-classifier output, ambiguous keyword match, etc.) and should
   * be visually de-emphasized + open to correction via the suggestion
   * flow. Omitted on confirmed tags. */
  tentative?: boolean;
  /** Where the tag came from. `auto` = the deterministic auto-tagger;
   * `human` = an editor with PAT write access; `suggestion` = a
   * GitHub-Discussion suggestion the editor applied. Defaults to `human`
   * when omitted. */
  source?: 'auto' | 'human' | 'suggestion';
}

export interface Settings {
  pat: string;
  owner: string;
  repo: string;
  branch: string;
  /** Master on/off switch. When false the extension still runs (sidebar,
   * settings, connection check) but the capture pipeline is fully paused:
   * graphql events are dropped, auto-scroll stops, the refetch loop pauses,
   * and no buffers are flushed. Re-enabling resumes everything. */
  enabled: boolean;
  autoCapture: boolean;
  configuredAt: number | null;
  /** Seconds between auto-scroll / refetch / media-crawl ticks (clamped
   * 3..60 in the UI). The loops themselves are now driven by per-loop
   * Start/Cancel buttons; only the cadence lives in settings. */
  autoScrollIntervalSec: number;
  /** When false, tweets we've already committed are skipped entirely —
   * we don't re-capture them even when engagement counts have changed.
   * Cuts the bandwidth + GitHub-API overhead of churn updates on long-tail
   * tweets. Defaults to true so users keep the existing engagement-history
   * behaviour unless they opt out. */
  updateExisting: boolean;
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
  /** When the current rate-limit window resets, as a Unix epoch in seconds.
   * Set when the last GitHub call returned 403/429 with rate-limit headers.
   * The background loops use this to back off until the window closes
   * instead of hammering GitHub through the rate-limited period. null when
   * we're not currently rate-limited or no reset time was reported. */
  rateLimitResetAt: number | null;
}

export interface ArchiveSnapshotAccount {
  handle: string;
  latest_post_at: string | null;
  latest_capture_at: string | null;
  row_count: number | null;
}

export interface ArchiveSnapshot {
  generated_at: string | null;
  fetched_at: string;
  accounts: Record<string, ArchiveSnapshotAccount>;
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

/** Per-author profile snapshot, attached to every tweet at capture time
 * because the underlying values (display name, bio, follower counts,
 * verification) drift slowly. Ingest aggregates the latest non-null
 * values into `data/users.json` for the viewer to render. */
export interface UserSnapshot {
  display_name: string | null;
  avatar_url: string | null;
  verified: boolean | null;
  is_blue_verified: boolean | null;
  verified_type: string | null;
  description: string | null;
  location: string | null;
  url: string | null;
  followers_count: number | null;
  friends_count: number | null;
  statuses_count: number | null;
  account_created_at: string | null;
  protected: boolean | null;
}

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

/** Inline preview / "card" rendered when the tweet links to an unfurlable
 * URL (article, YouTube video, …). X's source-of-truth for card content
 * is the unfurled URL — when that URL is later removed by the destination
 * site we lose the preview entirely, so the capture-time snapshot is the
 * only durable record. */
export interface TweetCard {
  name: string | null;
  card_url: string | null;
  vendor_url: string | null;
  title: string | null;
  description: string | null;
  image_url: string | null;
}

export interface CanonicalTweet {
  tweet_id: string;
  account_handle: string;
  account_id: string;
  posted_at: string;
  first_captured_at: string;
  last_seen_at: string;
  deletion_detected_at: string | null;
  /** X returned a tombstone / unavailable notice for this tweet. Kept
   * separately from deletion_detected_at because X sometimes supplies a
   * specific reason, e.g. copyright or withholding. */
  unavailable_detected_at: string | null;
  unavailable_reason: string | null;
  unavailable_text: string | null;
  unavailable_source_url: string | null;
  tweet_url: string;
  tweet_type: TweetType;
  /** ID of the root tweet that started this conversation thread. Needed to
   * reconstruct reply chains; the timeline endpoint emits replies one at a
   * time and only `conversation_id_str` ties them to a root. */
  conversation_id: string | null;
  reply_to_tweet_id: string | null;
  reply_to_account: string | null;
  reply_to_account_id: string | null;
  quoted_tweet_id: string | null;
  retweeted_tweet_id: string | null;
  text: string;
  text_resolved: string;
  lang: string | null;
  /** X's own NSFW / violence flag for this tweet. Captured because users
   * can toggle the flag for their own historical tweets; the at-capture
   * value is the only reliable record. */
  possibly_sensitive: boolean | null;
  /** HTML-encoded client identifier from `legacy.source` (e.g. "Twitter
   * for iPhone"). Sometimes stripped by X later. */
  source: string | null;
  /** Stringified place metadata when the tweet was geotagged ("San Diego,
   * CA, US"); null otherwise. */
  place_full_name: string | null;
  hashtags: string[];
  mentions: string[];
  urls: UrlEntity[];
  card: TweetCard | null;
  media: MediaItem[];
  like_count: number;
  retweet_count: number;
  reply_count: number;
  quote_count: number;
  view_count: number | null;
  bookmark_count: number | null;
  engagement_history: EngagementSnapshot[];
  /** Author profile snapshot at capture time. Ingest aggregates the latest
   * non-null fields per handle into `data/users.json` (which the viewer
   * fetches to render avatars + display names inline). */
  author: UserSnapshot;
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

export interface UnavailableTweet {
  tweet_id: string;
  account_handle: string | null;
  unavailable_detected_at: string;
  unavailable_reason: string | null;
  unavailable_text: string | null;
  unavailable_source_url: string | null;
}

export interface RetweetEdge {
  retweeter_handle: string;
  retweeter_account_id: string | null;
  retweeter_category: string | null;
  retweet_tweet_id: string;
  retweet_url: string;
  original_tweet_id: string;
  original_author_handle: string | null;
  original_author_account_id: string | null;
  original_author_category: string | null;
  captured_at: string;
  capture_run_id: string;
  endpoint: string;
  source_url: string | null;
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
  unavailable_tweets?: UnavailableTweet[];
  retweet_edges?: RetweetEdge[];
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

export type TweetArchiveStatus = 'new' | 'saved';
export type TweetCaptureAction = 'buffered' | 'skipped' | 'unchanged';

export interface TweetSighting {
  tweet_id: string;
  account_handle: string;
  posted_at: string;
  text: string;
  tweet_type: TweetType;
  tweet_url: string;
  seen_at: string;
  endpoint: string;
  archive_status: TweetArchiveStatus;
  action: TweetCaptureAction;
}

export interface AccountCounter {
  todayCount: number;
  todayDate: string;
  lastCaptureAt: string | null;
  totalCommitted: number;
  bufferedCount: number;
}

export interface QueueProgress {
  /** Total tweets in the queue right now (not yet processed). */
  total: number;
  /** Whether a loop is currently running over this queue. */
  running: boolean;
  /** Tweets the loop has processed (ingested or dropped after retries)
   * since the user clicked "Start". Zero when not running. */
  processed: number;
  /** Initial queue size at the moment the user started this run, used as the
   * progress denominator. `processed + total` may exceed this if X surfaced
   * more truncated tweets mid-run; the UI shows the larger of the two. */
  total_at_start: number;
}

export interface AutoScrollProgress {
  /** Whether the auto-scroll loop is currently armed. */
  active: boolean;
  /** Live count of x.com tabs we'd scroll on the next tick. */
  tabCount: number;
  /** Scroll ticks issued since the user clicked "Start". */
  scrollCount: number;
  /** Tweets ingested across all handles since the user clicked "Start". */
  ingestedCount: number;
  /** Buffered tweets not recognized as already present in the archive. */
  ingestedNewCount: number;
  /** Buffered tweets recognized as already present in the archive. */
  ingestedExistingCount: number;
  /** Old/archive tweets skipped because updateExisting=false. */
  skippedOldCount: number;
  /** "Show more" links clicked since the user clicked "Start". */
  expandedCount: number;
}

export interface ExtensionState {
  version: string;
  settings: Omit<Settings, 'pat'> & { patSuffix: string; patSet: boolean };
  connection: ConnectionState;
  accounts: AccountConfig[];
  counters: Record<string, AccountCounter>;
  autoScroll: AutoScrollProgress;
  refetchQueue: QueueProgress;
  mediaCrawlQueue: QueueProgress;
  threadOpenQueue: QueueProgress;
  recentTweetSightings: TweetSighting[];
}

export type RuntimeMessage =
  | { type: 'graphql-capture'; endpoint: string; url: string; pageUrl?: string; response: unknown }
  | { type: 'content-alive'; url: string }
  | { type: 'page-hook-active'; url: string }
  | { type: 'log-content-event'; level: LogLevel; msg: string; url: string }
  | { type: 'get-unfold-targets' }
  | { type: 'show-more-unfolded'; count: number }
  | { type: 'get-state' }
  | { type: 'capture-now'; handle: string }
  | { type: 'capture-all' }
  | { type: 'capture-this-page' }
  | { type: 'flush-all' }
  | { type: 'flush-handle'; handle: string }
  | { type: 'toggle-auto-capture'; on: boolean }
  | { type: 'toggle-enabled'; on: boolean }
  | { type: 'toggle-update-existing'; on: boolean }
  | { type: 'start-auto-scroll' }
  | { type: 'cancel-auto-scroll' }
  | { type: 'set-auto-scroll-interval'; seconds: number }
  | { type: 'start-refetch' }
  | { type: 'cancel-refetch' }
  | { type: 'start-media-crawl' }
  | { type: 'cancel-media-crawl' }
  | { type: 'start-thread-open' }
  | { type: 'cancel-thread-open' }
  | { type: 'purge-unrelated' }
  | { type: 'refresh-accounts' }
  | { type: 'verify-connection' }
  | { type: 'clear-activity' }
  | { type: 'open-options' }
  | { type: 'open-viewer' }
  | { type: 'log-event'; event: LogEvent }
  | { type: 'state-changed'; state: ExtensionState }
  | { type: 'activity-tail'; events: LogEvent[] };
