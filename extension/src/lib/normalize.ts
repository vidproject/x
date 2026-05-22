import { TWEET_URL_PREFIX } from './config.js';
import type {
  CanonicalTweet,
  CommunityNote,
  MediaItem,
  TweetCard,
  TweetType,
  UnavailableTweet,
  UrlEntity,
  UserSnapshot,
} from './types.js';

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
  sourceUrl?: string | null;
}

export interface NormalizeResult {
  tweets: CanonicalTweet[];
  observed_ids: string[];
  unavailable_tweets: UnavailableTweet[];
  /**
   * Tweet-shaped nodes the walker saw (had a `rest_id`) but couldn't turn
   * into a full `CanonicalTweet` — typically because the embedded user
   * info was missing or the entry was a media-only thumbnail card from
   * the UserMedia endpoint. Only IDs with a trustworthy handle from the
   * tweet node itself are returned; otherwise the background cannot build
   * a reliable /<handle>/status/<id> detail URL.
   */
  partial_ids: Array<{ tweet_id: string; hint_handle: string | null }>;
}

// X tweet IDs are 64-bit positive integers serialized as decimal strings.
// They've grown to 19 chars (~1.5e18) by 2026. Reject anything that doesn't
// match this shape — otherwise spurious card IDs, cursor strings, ad slot
// IDs, etc. end up in the partial-capture queue and the crawl loop
// navigates to invalid URLs that show "this page doesn't exist".
const TWEET_ID_RE = /^\d{15,20}$/;

// Only one endpoint actually exposes the failure mode the partial-capture
// queue exists for — the Media tab's `UserMedia` query returning
// thumbnail-only entries without a populated author block. Every other
// endpoint that hands us a `Tweet` shape gives us the full entry; if
// `buildTweet` fails there it's a real error, not "go re-fetch this".
// Restricting partial-id emission to UserMedia stops the floods of
// false positives reported on Replies / TweetDetail / SearchTimeline.
const PARTIAL_OK_ENDPOINTS: ReadonlySet<string> = new Set(['UserMedia']);

export function normalize(payload: unknown, ctx: NormalizeContext): NormalizeResult {
  const rawTweets = collectTweets(payload);
  const tweets: CanonicalTweet[] = [];
  const unavailableTweets: UnavailableTweet[] = [];
  const observed: string[] = [];
  const built = new Set<string>();
  const partialMap = new Map<string, string | null>();
  const collectPartials = PARTIAL_OK_ENDPOINTS.has(ctx.endpoint);
  for (const raw of rawTweets) {
    try {
      const unavailable = pickUnavailableTweet(raw, ctx);
      if (unavailable) {
        unavailableTweets.push(unavailable);
        observed.push(unavailable.tweet_id);
        built.add(unavailable.tweet_id);
        continue;
      }
      const t = buildTweet(raw, ctx);
      if (!t) {
        if (collectPartials) {
          // Only enqueue real-tweet shells with a trustworthy handle (not
          // tombstones, stripped nodes lacking a legacy block, garbage IDs,
          // or IDs that would require guessing the author from the page).
          const partial = pickPartial(raw);
          if (partial) partialMap.set(partial.tweet_id, partial.hint_handle);
        }
        continue;
      }
      observed.push(t.tweet_id);
      built.add(t.tweet_id);
      if (ctx.allowedHandles.size === 0 || ctx.allowedHandles.has(t.account_handle.toLowerCase())) {
        tweets.push(t);
      }
    } catch {
      // One bad entry shouldn't take down the whole batch.
    }
  }
  // Anything that ended up in partialMap but ALSO came back as a built
  // tweet (different walker pass, same id) isn't actually partial.
  const partial_ids: Array<{ tweet_id: string; hint_handle: string | null }> = [];
  for (const [id, hint] of partialMap) {
    if (built.has(id)) continue;
    partial_ids.push({ tweet_id: id, hint_handle: hint });
  }
  return { tweets, observed_ids: observed, unavailable_tweets: unavailableTweets, partial_ids };
}

function pickUnavailableTweet(node: unknown, ctx: NormalizeContext): UnavailableTweet | null {
  if (typeof node !== 'object' || node === null) return null;
  const n = node as Record<string, unknown>;
  if (n.__typename !== 'TweetTombstone') return null;

  const tweetId =
    strOrNull(n.rest_id) ??
    pickTweetIdFromUrls(node) ??
    (ctx.sourceUrl ? tweetIdFromTweetUrl(ctx.sourceUrl) : null);
  if (!tweetId || !TWEET_ID_RE.test(tweetId)) return null;

  const unavailableText = pickTombstoneText(node);
  return {
    tweet_id: tweetId,
    account_handle:
      pickHandleFromTweetUrls(node, tweetId) ??
      (ctx.sourceUrl ? handleFromTweetUrl(ctx.sourceUrl, tweetId) : null),
    unavailable_detected_at: ctx.capturedAt,
    unavailable_reason: classifyUnavailableReason(unavailableText),
    unavailable_text: unavailableText,
    unavailable_source_url: ctx.sourceUrl ?? null,
  };
}

function pickTweetIdFromUrls(node: unknown): string | null {
  const seen = new WeakSet<object>();
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const cur = stack.pop();
    if (typeof cur === 'string') {
      const id = tweetIdFromTweetUrl(cur);
      if (id) return id;
      continue;
    }
    if (cur === null || typeof cur !== 'object') continue;
    if (seen.has(cur as object)) continue;
    seen.add(cur as object);
    if (Array.isArray(cur)) {
      for (const v of cur) stack.push(v);
    } else {
      for (const v of Object.values(cur as Record<string, unknown>)) stack.push(v);
    }
  }
  return null;
}

function tweetIdFromTweetUrl(rawUrl: string): string | null {
  try {
    const url = new URL(rawUrl, TWEET_URL_PREFIX);
    if (url.hostname !== 'x.com' && url.hostname !== 'twitter.com') return null;
    const match = url.pathname.match(/^\/[A-Za-z0-9_]{1,15}\/status\/(\d{15,20})(?:\/|$)/);
    return match?.[1] ?? null;
  } catch {
    return null;
  }
}

function pickTombstoneText(node: unknown): string | null {
  const strings: string[] = [];
  const seen = new WeakSet<object>();
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const cur = stack.pop();
    if (typeof cur === 'string') {
      const trimmed = cur.trim();
      if (
        trimmed.length >= 4 &&
        !trimmed.startsWith('http://') &&
        !trimmed.startsWith('https://')
      ) {
        strings.push(trimmed);
      }
      continue;
    }
    if (cur === null || typeof cur !== 'object') continue;
    if (seen.has(cur as object)) continue;
    seen.add(cur as object);
    if (Array.isArray(cur)) {
      for (const v of cur) stack.push(v);
    } else {
      for (const v of Object.values(cur as Record<string, unknown>)) stack.push(v);
    }
  }
  const preferred = strings.find((s) =>
    /\b(copyright|dmca|unavailable|withheld|removed|deleted|violat|suspended)\b/i.test(s)
  );
  return preferred ?? strings[0] ?? null;
}

function classifyUnavailableReason(text: string | null): string | null {
  if (!text) return null;
  if (/\b(copyright|dmca)\b/i.test(text)) return 'copyright';
  if (/\bwithheld\b/i.test(text)) return 'withheld';
  if (/\bdeleted|removed\b/i.test(text)) return 'removed';
  if (/\bsuspended\b/i.test(text)) return 'suspended';
  if (/\bunavailable|not available\b/i.test(text)) return 'unavailable';
  return null;
}

function pickPartial(node: unknown): { tweet_id: string; hint_handle: string | null } | null {
  if (typeof node !== 'object' || node === null) return null;
  const n = node as Record<string, unknown>;
  // Deleted tweets surface as TweetTombstone — there's nothing to refetch,
  // skip them or the crawl loop will spin on "this page doesn't exist".
  if (n.__typename === 'TweetTombstone') return null;
  // Require a recognized Tweet typename OR a legacy block. Cursors, ads,
  // module headers, and the like get rejected here.
  const tn = n.__typename;
  if (tn !== 'Tweet' && tn !== 'TweetWithVisibilityResults' && !obj(n.legacy)) {
    return null;
  }
  const restId =
    (typeof n.rest_id === 'string' && n.rest_id) ||
    (typeof obj(n.tweet_result)?.result === 'object' &&
      (obj(obj(n.tweet_result)?.result)?.rest_id as string));
  if (typeof restId !== 'string' || !TWEET_ID_RE.test(restId)) return null;
  const hintHandle = pickHintHandle(node, restId);
  if (!hintHandle) return null;
  return { tweet_id: restId, hint_handle: hintHandle };
}

function pickHintHandle(node: unknown, tweetId: string): string | null {
  if (typeof node !== 'object' || node === null) return null;
  const n = node as Record<string, unknown>;
  const userResult = obj(obj(obj(n.core)?.user_results)?.result);
  return (
    strOrNull(obj(userResult?.core)?.screen_name) ??
    strOrNull(obj(userResult?.legacy)?.screen_name) ??
    strOrNull(obj(n.legacy)?.screen_name) ??
    pickHandleFromTweetUrls(node, tweetId)
  );
}

function pickHandleFromTweetUrls(node: unknown, tweetId: string): string | null {
  const seen = new WeakSet<object>();
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const cur = stack.pop();
    if (typeof cur === 'string') {
      const handle = handleFromTweetUrl(cur, tweetId);
      if (handle) return handle;
      continue;
    }
    if (cur === null || typeof cur !== 'object') continue;
    if (seen.has(cur as object)) continue;
    seen.add(cur as object);
    if (Array.isArray(cur)) {
      for (const v of cur) stack.push(v);
    } else {
      for (const v of Object.values(cur as Record<string, unknown>)) stack.push(v);
    }
  }
  return null;
}

function handleFromTweetUrl(rawUrl: string, tweetId: string): string | null {
  try {
    const url = new URL(rawUrl, TWEET_URL_PREFIX);
    if (url.hostname !== 'x.com' && url.hostname !== 'twitter.com') return null;
    const match = url.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/status\/(\d{15,20})(?:\/|$)/);
    if (!match || match[2] !== tweetId) return null;
    return match[1] ?? null;
  } catch {
    return null;
  }
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
  const userAvatarObj = obj(userResult?.avatar);
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
  // Author snapshot. Display name + avatar moved between `legacy` and the
  // new `core` substructure over time; the rest (verification, follower
  // counts, account_created_at) is still in `legacy`. We snapshot the
  // whole UserSnapshot per tweet because these values drift over time
  // and the at-capture state is the only reliable record.
  const author = extractUserSnapshot(userResult, userCoreNew, userLegacy, userAvatarObj);

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

  // X omits favorite_count / reply_count / quote_count from a retweet
  // wrapper's legacy block (only retweet_count is propagated) — the real
  // counts live on the retweeted status. Read engagement from there for
  // retweets so we don't record 0 likes/replies/quotes on every retweet.
  const retweetedLegacy = obj(obj(obj(legacy.retweeted_status_result)?.result)?.legacy);
  const engLegacy = tweetType === 'retweet' && retweetedLegacy ? retweetedLegacy : legacy;

  // posted_at: legacy.created_at remains the canonical location; if that's
  // missing in a future schema we fall back to top-level created_at.
  const postedAt =
    parseTwitterDate(strOrNull(legacy.created_at)) ?? parseTwitterDate(strOrNull(t.created_at));
  if (!postedAt) return null;

  const views = obj(t.views);
  const viewCount = numOrNull(views?.count) ?? numOrNull(strOrNull(views?.count));

  const card = extractCard(t);
  const place = obj(legacy.place);

  const tweet: CanonicalTweet = {
    tweet_id: restId,
    account_handle: handle,
    account_id: accountId,
    posted_at: postedAt,
    first_captured_at: ctx.capturedAt,
    last_seen_at: ctx.capturedAt,
    deletion_detected_at: null,
    unavailable_detected_at: null,
    unavailable_reason: null,
    unavailable_text: null,
    unavailable_source_url: null,
    tweet_url: `${TWEET_URL_PREFIX}/${handle}/status/${restId}`,
    tweet_type: tweetType,
    conversation_id: strOrNull(legacy.conversation_id_str),
    reply_to_tweet_id: strOrNull(legacy.in_reply_to_status_id_str),
    reply_to_account: strOrNull(legacy.in_reply_to_screen_name),
    reply_to_account_id: strOrNull(legacy.in_reply_to_user_id_str),
    quoted_tweet_id: strOrNull(legacy.quoted_status_id_str),
    retweeted_tweet_id: strOrNull(
      obj(obj(legacy.retweeted_status_result)?.result)?.rest_id ??
        (legacy as Record<string, unknown>).retweeted_status_id_str
    ),
    text,
    text_resolved: resolveShortUrls(text, urls),
    lang: strOrNull(legacy.lang),
    possibly_sensitive: boolOrNull(legacy.possibly_sensitive),
    source: strOrNull(legacy.source) ?? strOrNull(t.source),
    place_full_name: strOrNull(place?.full_name),
    hashtags,
    mentions,
    urls,
    card,
    media,
    like_count: numOrZero(engLegacy.favorite_count),
    retweet_count: numOrZero(legacy.retweet_count),
    reply_count: numOrZero(engLegacy.reply_count),
    quote_count: numOrZero(engLegacy.quote_count),
    view_count: viewCount,
    bookmark_count: numOrNull(legacy.bookmark_count),
    engagement_history: [
      {
        captured_at: ctx.capturedAt,
        likes: numOrZero(engLegacy.favorite_count),
        retweets: numOrZero(legacy.retweet_count),
        replies: numOrZero(engLegacy.reply_count),
        quotes: numOrZero(engLegacy.quote_count),
        views: viewCount,
        bookmarks: numOrNull(legacy.bookmark_count),
      },
    ],
    author,
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
function boolOrNull(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null;
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
 * body. A truncated tweet has X's "Show more" t.co URL appended — that URL
 * specifically expands to the tweet's own /i/web/status/<id> permalink, and
 * is the only reliable distinguishing feature. Plenty of normal tweets have
 * trailing t.co URLs (media, quotes, regular links), and their
 * display_text_range also trims past full_text.length, so the old
 * "display_text_range[1] < full_text.length" heuristic was overflagging
 * essentially every tweet with a link in it.
 *
 * Rules:
 *   - note_tweet present  → not truncated (we already have the full body).
 *   - One of legacy.entities.urls has an expanded_url like
 *     ".../i/web/status/<id>"  →  truncated.
 *   - Otherwise → not truncated.
 *
 * The previous fallback ("trailing t.co URL + length ≥ 270") flagged every
 * media tweet exactly at the 280-char limit. We drop it entirely; the only
 * cost is missing genuinely-truncated tweets whose payload was so degraded
 * it lacked entities — which is also where the refetch loop would fail
 * anyway, so nothing is actually lost.
 */
function detectTruncation(
  noteTweet: Record<string, unknown> | null,
  legacy: Record<string, unknown>,
  fullText: string
): boolean {
  if (noteTweet) return false;
  if (!fullText) return false;
  const entities = obj(legacy.entities);
  const urls = entities ? entities.urls : null;
  if (Array.isArray(urls)) {
    for (const u of urls) {
      if (typeof u !== 'object' || u === null) continue;
      const exp = (u as Record<string, unknown>).expanded_url;
      // The "Show more" link expands to either of these self-permalink
      // shapes:  https://x.com/i/web/status/<id>
      //          https://twitter.com/i/web/status/<id>
      if (typeof exp === 'string' && /\/i\/web\/status\/\d+/.test(exp)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Filter `tweets` down to those related to a tracked account. A tweet is
 * "related" if any of the following hold:
 *
 *   - its author is tracked (handle in `targeted`),
 *   - its author is core (handle in `coreHandles`), regardless of where X
 *     surfaced it,
 *   - it mentions a tracked handle,
 *   - it replies to a tracked account,
 *   - it quotes/replies-to a tweet that's also present in the
 *     batch *and* authored by a tracked account (transitively related —
 *     captures quoted/reply context that appears as a sibling node in the
 *     same GraphQL response).
 *
 * Non-core retweet wrappers are not kept merely because they retweeted a
 * tracked/core tweet. The underlying core tweet is kept as its own row when
 * present in the payload.
 *
 * Anything else (random replies under a tracked account's tweet, random
 * authors that happen to surface in a tracked account's thread) is dropped.
 * Pass an empty `targeted` set to disable filtering entirely.
 */
export function filterRelated(
  tweets: CanonicalTweet[],
  targeted: ReadonlySet<string>,
  coreHandles: ReadonlySet<string> = new Set()
): CanonicalTweet[] {
  if (targeted.size === 0 && coreHandles.size === 0) return tweets;
  // First pass: which tweet IDs do tracked-author tweets reference (the
  // "forward" direction — tracked account quotes / RTs / replies to X)?
  // And which tweet IDs ARE tracked-authored (the "reverse" direction —
  // X quotes / RTs / replies to a tracked tweet)?
  const referencedIds = new Set<string>();
  const targetedTweetIds = new Set<string>();
  for (const t of tweets) {
    if (!targeted.has(t.account_handle.toLowerCase())) continue;
    targetedTweetIds.add(t.tweet_id);
    if (t.quoted_tweet_id) referencedIds.add(t.quoted_tweet_id);
    if (t.retweeted_tweet_id) referencedIds.add(t.retweeted_tweet_id);
    if (t.reply_to_tweet_id) referencedIds.add(t.reply_to_tweet_id);
  }
  return tweets.filter((t) => {
    const h = t.account_handle.toLowerCase();
    if (targeted.has(h)) return true;
    if (coreHandles.has(h)) return true;
    // Forward: a tracked-author tweet pointed at this one.
    if (referencedIds.has(t.tweet_id)) return true;
    // Reverse: this tweet quotes/replies to a tracked-author tweet in the
    // batch. Reverse retweets are just "non-core account retweeted core
    // tweet" wrappers, and do not add core archive value.
    if (t.quoted_tweet_id && targetedTweetIds.has(t.quoted_tweet_id)) return true;
    if (t.reply_to_tweet_id && targetedTweetIds.has(t.reply_to_tweet_id)) return true;
    // Reply-to-account or mentions a tracked handle.
    if (t.reply_to_account && targeted.has(t.reply_to_account.toLowerCase())) return true;
    for (const m of t.mentions) {
      if (targeted.has(m.toLowerCase())) return true;
    }
    return false;
  });
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

/**
 * Pull a per-author UserSnapshot from the tweet's user_results node. Every
 * field defaults to null when missing so the schema stays stable across
 * the legacy / core shape migrations X has been doing.
 */
function extractUserSnapshot(
  userResult: Record<string, unknown> | null,
  userCoreNew: Record<string, unknown> | null,
  userLegacy: Record<string, unknown> | null,
  userAvatarObj: Record<string, unknown> | null
): UserSnapshot {
  const verification = obj(userResult?.verification);
  return {
    display_name:
      strOrNull(userCoreNew?.name) ?? strOrNull(userLegacy?.name) ?? strOrNull(userResult?.name),
    avatar_url:
      strOrNull(userAvatarObj?.image_url) ??
      strOrNull(userLegacy?.profile_image_url_https) ??
      strOrNull(userResult?.profile_image_url_https),
    verified: boolOrNull(verification?.verified) ?? boolOrNull(userLegacy?.verified),
    is_blue_verified: boolOrNull(userResult?.is_blue_verified),
    verified_type: strOrNull(verification?.verified_type) ?? strOrNull(userLegacy?.verified_type),
    description: strOrNull(userLegacy?.description),
    location: strOrNull(obj(userResult?.location)?.location) ?? strOrNull(userLegacy?.location),
    url: strOrNull(userLegacy?.url),
    followers_count: numOrNull(userLegacy?.followers_count),
    friends_count: numOrNull(userLegacy?.friends_count),
    statuses_count: numOrNull(userLegacy?.statuses_count),
    account_created_at:
      parseTwitterDate(strOrNull(userCoreNew?.created_at)) ??
      parseTwitterDate(strOrNull(userLegacy?.created_at)),
    protected: boolOrNull(userLegacy?.protected),
  };
}

/**
 * Walk the tweet's `card.legacy.binding_values` (key/value array) into a
 * flat TweetCard with title, description, and a representative image URL.
 * Returns null when the tweet has no card.
 */
function extractCard(t: Record<string, unknown>): TweetCard | null {
  const card = obj(t.card);
  const cardLegacy = obj(card?.legacy);
  if (!cardLegacy) return null;
  const bindings = cardLegacy.binding_values;
  const byKey: Record<string, unknown> = {};
  if (Array.isArray(bindings)) {
    for (const bv of bindings) {
      if (typeof bv !== 'object' || bv === null) continue;
      const o = bv as Record<string, unknown>;
      const k = strOrNull(o.key);
      const v = obj(o.value);
      if (!k || !v) continue;
      byKey[k] =
        strOrNull(v.string_value) ??
        strOrNull(obj(v.image_value)?.url) ??
        strOrNull(obj(v.image_color_value)?.palette);
    }
  }
  const name = strOrNull(cardLegacy.name);
  const cardUrl = strOrNull(cardLegacy.url);
  const vendorUrl =
    typeof byKey['vanity_url'] === 'string' ? (byKey['vanity_url'] as string) : null;
  const title = typeof byKey['title'] === 'string' ? (byKey['title'] as string) : null;
  const description =
    typeof byKey['description'] === 'string' ? (byKey['description'] as string) : null;
  const imageUrl =
    (typeof byKey['photo_image_full_size_original'] === 'string'
      ? (byKey['photo_image_full_size_original'] as string)
      : null) ??
    (typeof byKey['thumbnail_image_original'] === 'string'
      ? (byKey['thumbnail_image_original'] as string)
      : null) ??
    (typeof byKey['summary_photo_image_original'] === 'string'
      ? (byKey['summary_photo_image_original'] as string)
      : null);
  if (!name && !title && !description && !imageUrl) return null;
  return {
    name,
    card_url: cardUrl,
    vendor_url: vendorUrl,
    title,
    description,
    image_url: imageUrl,
  };
}
