import type { AccountConfig, Settings } from './types.js';

export const DEFAULT_SETTINGS: Settings = {
  pat: '',
  owner: 'vidproject',
  repo: 'x',
  // The repo's current default branch is "master". If you fork or rename
  // it later, update Settings before the next capture.
  branch: 'master',
  enabled: true,
  autoCapture: true,
  configuredAt: null,
  autoScrollIntervalSec: 6,
  updateExisting: true,
};

export const AUTO_SCROLL_MIN_SEC = 3;
export const AUTO_SCROLL_MAX_SEC = 60;

// Built-in fallback if config/accounts.yaml cannot be fetched (e.g., before the
// repo has been initialized or PAT misconfigured). Kept in sync with the file.
export const FALLBACK_ACCOUNTS: AccountConfig[] = [
  { handle: 'DHSgov', label: 'Department of Homeland Security', category: 'core' },
  { handle: 'ICEgov', label: 'U.S. Immigration and Customs Enforcement', category: 'core' },
  { handle: 'CBP', label: 'U.S. Customs and Border Protection', category: 'core' },
  { handle: 'USCIS', label: 'U.S. Citizenship and Immigration Services', category: 'core' },
  { handle: 'WhiteHouse', label: 'The White House', category: 'core' },
  { handle: 'PressSec', label: 'White House Press Secretary', category: 'core' },
  { handle: 'POTUS', label: 'President of the United States', category: 'core' },
  { handle: 'USDOL', label: 'U.S. Department of Labor', category: 'core' },
  { handle: 'RapidResponse47', label: 'Rapid Response 47', category: 'core' },
  { handle: 'StephenM', label: 'Stephen Miller', category: 'core' },
  { handle: 'GregoryKBovino', label: 'Gregory Bovino', category: 'core' },
  { handle: 'RealTomHoman', label: 'Thomas D. Homan', category: 'core' },
];

// GraphQL endpoint names we care about (matched by substring against URL path).
export const TWEET_ENDPOINTS: ReadonlySet<string> = new Set([
  'UserTweets',
  'UserTweetsAndReplies',
  'UserMedia',
  'UserHighlightsTweets',
  'TweetDetail',
  'TweetResultByRestId',
  'Likes',
  'Bookmarks',
  'HomeTimeline',
  'HomeLatestTimeline',
  'SearchTimeline',
  'ListLatestTweetsTimeline',
]);

// Endpoints that carry Community Note bodies — captured for completeness but
// they don't introduce new tweets, so they don't need to be in TWEET_ENDPOINTS.
export const BIRDWATCH_ENDPOINTS: ReadonlySet<string> = new Set([
  'BirdwatchFetchOneNote',
  'BirdwatchFetchNotes',
]);

// Auxiliary endpoints we don't extract tweets from but use for context (e.g.,
// to learn account_id <-> handle bindings).
export const PROFILE_ENDPOINTS: ReadonlySet<string> = new Set(['UserByScreenName', 'UserByRestId']);

// Endpoints whose responses are scoped to a particular account or
// conversation — i.e. visiting `/<handle>` / `/<handle>/with_replies`,
// looking at one tweet, etc. Every tweet returned by these endpoints is
// either authored by the tracked account or directly referenced by them
// (retweeted, quoted, replied to), so we keep everything we see rather
// than dropping non-tracked authors. That way a retweet of a non-tracked
// account still gets its original-tweet content archived.
//
// Endpoints NOT in this set (HomeTimeline, SearchTimeline, Likes, etc.)
// still get filtered to tracked-handle authors — see the spec's
// "Browsing my home timeline is ignored" rule.
export const USER_PAGE_ENDPOINTS: ReadonlySet<string> = new Set([
  'UserTweets',
  'UserTweetsAndReplies',
  'UserMedia',
  'UserHighlightsTweets',
  'TweetDetail',
  'TweetResultByRestId',
]);

// Maximum tweets to buffer per handle before forcing a commit.
export const FLUSH_TWEET_THRESHOLD = 200;

// Idle delay after the last capture before auto-flushing a handle's buffer.
export const FLUSH_IDLE_MS = 45_000;

// How long an alarm-based flush sweep waits between checks.
export const FLUSH_ALARM_MINUTES = 1;

// Activity tail size.
export const ACTIVITY_TAIL_MAX = 250;

// Periodic re-verification of the GitHub connection (ms).
export const VERIFY_CONNECTION_INTERVAL_MS = 10 * 60 * 1000;

export const TWEET_URL_PREFIX = 'https://x.com';
