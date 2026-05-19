import type { AccountConfig, Settings } from './types.js';

export const DEFAULT_SETTINGS: Settings = {
  pat: '',
  owner: 'vidproject',
  repo: 'x',
  branch: 'main',
  autoCapture: true,
  configuredAt: null,
};

// Built-in fallback if config/accounts.yaml cannot be fetched (e.g., before the
// repo has been initialized or PAT misconfigured). Kept in sync with the file.
export const FALLBACK_ACCOUNTS: AccountConfig[] = [
  { handle: 'DHSgov', label: 'Department of Homeland Security' },
  { handle: 'ICEgov', label: 'U.S. Immigration and Customs Enforcement' },
  { handle: 'CBP', label: 'U.S. Customs and Border Protection' },
  { handle: 'USCIS', label: 'U.S. Citizenship and Immigration Services' },
  { handle: 'WhiteHouse', label: 'The White House' },
  { handle: 'PressSec', label: 'White House Press Secretary' },
  { handle: 'POTUS', label: 'President of the United States' },
  { handle: 'USDOL', label: 'U.S. Department of Labor' },
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

// Auxiliary endpoints we don't extract tweets from but use for context (e.g.,
// to learn account_id <-> handle bindings).
export const PROFILE_ENDPOINTS: ReadonlySet<string> = new Set(['UserByScreenName', 'UserByRestId']);

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
