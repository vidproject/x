import { TWEET_URL_PREFIX } from './config.js';
import type { CanonicalTweet, CommunityNote, MediaItem, TweetType, UrlEntity } from './types.js';

/**
 * Normalize GraphQL response payloads from X's internal API into
 * `CanonicalTweet`s. Many endpoints share a similar tweet shape but wrap it in
 * different envelopes; this module walks the structure defensively.
 *
 * The parser is intentionally permissive: it will silently skip entries it
 * can't recognize as tweets (cursors, ads, gap entries, modules of other
 * tweets, tombstones). It throws *only* when given a payload it doesn't know
 * how to walk at all — those cases are caught and quarantined upstream.
 */

export interface NormalizeContext {
  capturedAt: string;
  runId: string;
  endpoint: string;
  allowedHandles: ReadonlySet<string>;
}

export interface NormalizeResult {
  tweets: CanonicalTweet[];
  observed_ids: string[];
}

export function normalize(payload: unknown, ctx: NormalizeContext): NormalizeResult {
  const rawTweets = collectTweets(payload);
  const tweets: CanonicalTweet[] = [];
  const observed: string[] = [];
  for (const raw of rawTweets) {
    try {
      const t = buildTweet(raw, ctx);
      if (!t) continue;
      observed.push(t.tweet_id);
      if (ctx.allowedHandles.size === 0 || ctx.allowedHandles.has(t.account_handle.toLowerCase())) {
        tweets.push(t);
      }
    } catch {
      // One bad entry shouldn't take down the whole batch.
    }
  }
  return { tweets, observed_ids: observed };
}

function collectTweets(obj: unknown): unknown[] {
  // Walk the response tree gathering everything that looks like a tweet
  // result. We look for nodes shaped like:
  //   { __typename?: 'Tweet'|'TweetWithVisibilityResults', rest_id, legacy }
  // and unwrap visibility wrappers.
  const out: unknown[] = [];
  const seen = new WeakSet<object>();
  const stack: unknown[] = [obj];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node === null || node === undefined) continue;
    if (typeof node !== 'object') continue;
    if (seen.has(node as object)) continue;
    seen.add(node as object);

    if (looksLikeTweet(node)) {
      out.push(unwrapTweet(node));
    }
    if (Array.isArray(node)) {
      for (const v of node) stack.push(v);
    } else {
      for (const v of Object.values(node as Record<string, unknown>)) stack.push(v);
    }
  }
  return out;
}

function looksLikeTweet(node: unknown): node is Record<string, unknown> {
  if (typeof node !== 'object' || node === null) return false;
  const n = node as Record<string, unknown>;
  const typename = n.__typename;
  if (
    typename === 'Tweet' ||
    typename === 'TweetWithVisibilityResults' ||
    typename === 'TweetTombstone'
  ) {
    return true;
  }
  // Some entries lack __typename but still carry legacy + rest_id + core.
  return typeof n.rest_id === 'string' && typeof n.legacy === 'object' && n.legacy !== null;
}

function unwrapTweet(node: Record<string, unknown>): Record<string, unknown> {
  if (node.__typename === 'TweetWithVisibilityResults' && typeof node.tweet === 'object') {
    return node.tweet as Record<string, unknown>;
  }
  return node;
}

function buildTweet(raw: unknown, ctx: NormalizeContext): CanonicalTweet | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const t = raw as Record<string, unknown>;

  if (t.__typename === 'TweetTombstone') return null;

  const restId = strOrNull(t.rest_id);
  // `legacy` is still where most engagement / entities live in 2026-era X
  // payloads, but as of recent schema migrations several fields previously
  // there (notably the author handle) have moved to sibling locations. We
  // tolerate a missing legacy here and look up everything via fallbacks.
  const legacy = obj(t.legacy) ?? {};
  if (!restId) return null;

  const core = obj(t.core);
  const userResult = obj(obj(core?.user_results)?.result);
  // Author handle: try the new `core` substructure first (current X shape),
  // then the legacy `legacy.screen_name`, then a couple of older variants.
  const userCoreNew = obj(userResult?.core);
  const userLegacy = obj(userResult?.legacy);
  const handle =
    strOrNull(userCoreNew?.screen_name) ??
    strOrNull(userLegacy?.screen_name) ??
    strOrNull(userResult?.screen_name) ??
    strOrNull(legacy.screen_name);
  const accountId =
    strOrNull(userResult?.rest_id) ??
    strOrNull(userResult?.id_str) ??
    strOrNull(legacy.user_id_str);
  if (!handle || !accountId) return null;

  const noteTweet = obj(obj(obj(t.note_tweet)?.note_tweet_results)?.result);
  const textFromNote = strOrNull(noteTweet?.text);
  const textFromLegacy = strOrNull(legacy.full_text) ?? '';
  const text = textFromNote ?? textFromLegacy;
  const isTruncated = detectTruncation(noteTweet, legacy, textFromLegacy);
  const communityNote = extractCommunityNote(t, legacy, ctx.capturedAt);

  const entities = obj(legacy.entities) ?? {};
  const entitiesFromNote = obj(noteTweet?.entity_set);
  const hashtags = extractHashtags(entitiesFromNote ?? entities);
  const mentions = extractMentions(entitiesFromNote ?? entities);
  const urls = extractUrls(entitiesFromNote ?? entities);
  const media = extractMedia(legacy);

  const tweetType = classifyTweetType(t, legacy);

  // posted_at: legacy.created_at remains the canonical location; if that's
  // missing in a future schema we fall back to top-level created_at.
  const postedAt =
    parseTwitterDate(strOrNull(legacy.created_at)) ?? parseTwitterDate(strOrNull(t.created_at));
  if (!postedAt) return null;

  const views = obj(t.views);
  const viewCount = numOrNull(views?.count) ?? numOrNull(strOrNull(views?.count));

  const tweet: CanonicalTweet = {
    tweet_id: restId,
    account_handle: handle,
    account_id: accountId,
    posted_at: postedAt,
    first_captured_at: ctx.capturedAt,
    last_seen_at: ctx.capturedAt,
    deletion_detected_at: null,
    tweet_url: `${TWEET_URL_PREFIX}/${handle}/status/${restId}`,
    tweet_type: tweetType,
    reply_to_tweet_id: strOrNull(legacy.in_reply_to_status_id_str),
    reply_to_account: strOrNull(legacy.in_reply_to_screen_name),
    quoted_tweet_id: strOrNull(legacy.quoted_status_id_str),
    retweeted_tweet_id: strOrNull(
      obj(obj(legacy.retweeted_status_result)?.result)?.rest_id ??
        (legacy as Record<string, unknown>).retweeted_status_id_str
    ),
    text,
    text_resolved: resolveShortUrls(text, urls),
    lang: strOrNull(legacy.lang),
    hashtags,
    mentions,
    urls,
    media,
    like_count: numOrZero(legacy.favorite_count),
    retweet_count: numOrZero(legacy.retweet_count),
    reply_count: numOrZero(legacy.reply_count),
    quote_count: numOrZero(legacy.quote_count),
    view_count: viewCount,
    bookmark_count: numOrNull(legacy.bookmark_count),
    engagement_history: [
      {
        captured_at: ctx.capturedAt,
        likes: numOrZero(legacy.favorite_count),
        retweets: numOrZero(legacy.retweet_count),
        replies: numOrZero(legacy.reply_count),
        quotes: numOrZero(legacy.quote_count),
        views: viewCount,
        bookmarks: numOrNull(legacy.bookmark_count),
      },
    ],
    community_note: communityNote,
    is_truncated: isTruncated,
    wayback_url: null,
    wayback_submitted_at: null,
    capture_source: 'extension',
    capture_run_id: ctx.runId,
    schema_version: 1,
  };

  return tweet;
}

function classifyTweetType(t: Record<string, unknown>, legacy: Record<string, unknown>): TweetType {
  if (legacy.retweeted_status_result || legacy.retweeted_status_id_str) return 'retweet';
  if (legacy.in_reply_to_status_id_str) return 'reply';
  if (legacy.is_quote_status === true || legacy.quoted_status_id_str) return 'quote';
  if (t.__typename === 'TweetWithVisibilityResults') {
    // pass through
  }
  return 'original';
}

function extractHashtags(entities: Record<string, unknown>): string[] {
  const arr = entities.hashtags;
  if (!Array.isArray(arr)) return [];
  return arr
    .map((h) =>
      typeof h === 'object' && h && 'text' in h ? String((h as { text: unknown }).text) : null
    )
    .filter((s): s is string => typeof s === 'string' && s.length > 0);
}

function extractMentions(entities: Record<string, unknown>): string[] {
  const arr = entities.user_mentions;
  if (!Array.isArray(arr)) return [];
  return arr
    .map((u) =>
      typeof u === 'object' && u && 'screen_name' in u
        ? String((u as { screen_name: unknown }).screen_name)
        : null
    )
    .filter((s): s is string => typeof s === 'string' && s.length > 0);
}

function extractUrls(entities: Record<string, unknown>): UrlEntity[] {
  const arr = entities.urls;
  if (!Array.isArray(arr)) return [];
  const out: UrlEntity[] = [];
  for (const u of arr) {
    if (typeof u !== 'object' || u === null) continue;
    const o = u as Record<string, unknown>;
    const short = strOrNull(o.url);
    const expanded = strOrNull(o.expanded_url);
    const display = strOrNull(o.display_url);
    if (!short || !expanded) continue;
    out.push({ short, expanded, display: display ?? expanded });
  }
  return out;
}

function extractMedia(legacy: Record<string, unknown>): MediaItem[] {
  const extended = obj(legacy.extended_entities);
  const fromExt = extended ? toArray(extended.media) : [];
  const fromEnt = toArray(obj(legacy.entities)?.media);
  const seen = new Set<string>();
  const merged = [...fromExt, ...fromEnt].filter((m) => {
    if (typeof m !== 'object' || m === null) return false;
    const id =
      strOrNull((m as Record<string, unknown>).media_key) ??
      strOrNull((m as Record<string, unknown>).id_str);
    if (!id) return false;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  return merged.map((raw) => buildMedia(raw as Record<string, unknown>));
}

function buildMedia(m: Record<string, unknown>): MediaItem {
  const type = strOrNull(m.type) as MediaItem['media_type'];
  const original = pickOriginalUrl(m, type);
  const info = obj(m.original_info);
  const videoInfo = obj(m.video_info);
  const durationMs = numOrNull(videoInfo?.duration_millis);
  const altText = strOrNull(m.ext_alt_text);
  const mediaId =
    strOrNull(m.media_key) ??
    strOrNull(m.id_str) ??
    `${type ?? 'media'}-${Math.random().toString(36).slice(2)}`;
  return {
    media_id: mediaId,
    media_type: (type ?? 'photo') as MediaItem['media_type'],
    original_url: original ?? '',
    release_asset_url: null,
    sha256: null,
    bytes: null,
    duration_sec: durationMs !== null ? durationMs / 1000 : null,
    width: numOrNull(info?.width),
    height: numOrNull(info?.height),
    alt_text: altText,
    archive_status: 'pending',
    archive_attempts: 0,
    last_attempt_at: null,
  };
}

function pickOriginalUrl(m: Record<string, unknown>, type: string | null): string | null {
  if (type === 'photo') return strOrNull(m.media_url_https) ?? strOrNull(m.media_url);
  const variants = toArray(obj(m.video_info)?.variants) as Record<string, unknown>[];
  if (variants.length === 0) return strOrNull(m.media_url_https);
  // Highest-bitrate mp4.
  let best: { url: string; bitrate: number } | null = null;
  for (const v of variants) {
    const ct = strOrNull(v.content_type);
    const url = strOrNull(v.url);
    if (!url) continue;
    if (ct === 'video/mp4') {
      const br = numOrNull(v.bitrate) ?? 0;
      if (!best || br > best.bitrate) best = { url, bitrate: br };
    } else if (!best) {
      // Fallback to any variant if no mp4 found.
      best = { url, bitrate: 0 };
    }
  }
  return best?.url ?? null;
}

function resolveShortUrls(text: string, urls: UrlEntity[]): string {
  if (urls.length === 0) return text;
  let out = text;
  for (const u of urls) out = out.split(u.short).join(u.expanded);
  return out;
}

function parseTwitterDate(s: string | null): string | null {
  if (!s) return null;
  // Twitter ships dates like "Mon Apr 12 14:23:01 +0000 2025".
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}
function numOrNull(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v.length > 0) {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}
function numOrZero(v: unknown): number {
  return numOrNull(v) ?? 0;
}
function obj(v: unknown): Record<string, unknown> | null {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}
function toArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/**
 * Decide whether a tweet's archived `text` is the truncated head of a longer
 * body. Triggered when `note_tweet` is absent (long-tweet payload not
 * inlined) AND `display_text_range[1]` falls short of `full_text.length` —
 * i.e. X glued a trailing "show more" t.co URL onto the visible text.
 *
 * Conservative on purpose: re-fetching incorrectly flagged tweets is cheap;
 * missing genuinely-truncated ones permanently loses content.
 */
function detectTruncation(
  noteTweet: Record<string, unknown> | null,
  legacy: Record<string, unknown>,
  fullText: string
): boolean {
  if (noteTweet) return false; // we have the long-form body already
  if (!fullText) return false;
  const range = legacy.display_text_range;
  if (Array.isArray(range) && range.length >= 2) {
    const end = typeof range[1] === 'number' ? range[1] : null;
    if (end !== null && end < fullText.length) return true;
  }
  // Some payloads omit display_text_range; fall back to a trailing-URL probe.
  // X always appends the "show more" t.co URL after a single space.
  return / https?:\/\/t\.co\/[A-Za-z0-9]+$/.test(fullText) && fullText.length >= 270;
}

/**
 * Pull the Community Note (formerly Birdwatch) block attached to a tweet, if
 * any. The pivot can live at `tweet.legacy.birdwatch_pivot` (older shape) or
 * `tweet.birdwatch_pivot` (current shape). The summary text is what readers
 * actually see; we keep the surrounding metadata so downstream tooling can
 * tell drafts apart from rated-helpful notes.
 */
function extractCommunityNote(
  t: Record<string, unknown>,
  legacy: Record<string, unknown>,
  observedAt: string
): CommunityNote | null {
  const pivot = obj(t.birdwatch_pivot) ?? obj(legacy.birdwatch_pivot);
  if (!pivot) return null;
  const subtitle = obj(pivot.subtitle);
  const note = obj(pivot.note);
  const summary = strOrNull(subtitle?.text);
  const title = strOrNull(pivot.title);
  const shortTitle = strOrNull(pivot.shortTitle);
  const destinationUrl = strOrNull(pivot.destinationUrl);
  const noteId = strOrNull(pivot.noteId) ?? strOrNull(note?.rest_id);
  // Skip empty shells that have no actual content.
  if (!summary && !title && !noteId) return null;
  return {
    note_id: noteId,
    title,
    short_title: shortTitle,
    summary,
    destination_url: destinationUrl,
    observed_at: observedAt,
  };
}
