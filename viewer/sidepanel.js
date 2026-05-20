// Side-panel detail view: opens when a table row is clicked.

export function openSidepanel(panelEl, titleEl, bodyEl, row, thread) {
  if (!row) return;
  titleEl.textContent = `@${row.account_handle} · ${shortDate(row.posted_at)}`;
  bodyEl.replaceChildren();
  bodyEl.append(
    section('Tweet', tweetText(row), tweetLinks(row), truncationBadge(row)),
    section('Tags', tagsBlock(row), suggestButton(row)),
    section('Identifiers', grid(idRows(row))),
    section('Engagement', grid(engagementRows(row)))
  );
  // When the clicked row is a thread master, surface its sibling
  // replies that the table doesn't inline (everything that isn't a
  // self-reply from the same handle). This is the "click to see the
  // rest" half of the threading design.
  if (thread && thread.otherSlaves && thread.otherSlaves.length > 0) {
    bodyEl.append(
      section(
        `Other replies (${thread.otherSlaves.length})`,
        otherRepliesBlock(thread.otherSlaves)
      )
    );
  }
  if (row.community_note) {
    bodyEl.append(section('Community Note', communityNoteBlock(row.community_note)));
  }
  if (row.card) {
    bodyEl.append(section('Link preview', cardBlock(row.card)));
  }
  if (Array.isArray(row.media) && row.media.length > 0) {
    bodyEl.append(section('Media', mediaGrid(row.media)));
  }
  if (Array.isArray(row.engagement_history) && row.engagement_history.length > 1) {
    bodyEl.append(section('Engagement history', engagementHistory(row.engagement_history)));
  }
  if (row.deletion_detected_at) {
    bodyEl.append(
      section(
        'Deletion detected',
        para(
          `Marked at ${row.deletion_detected_at}. The detector treats this as a soft signal — see the operating principles in the README.`
        )
      )
    );
  }
  panelEl.hidden = false;
  panelEl.setAttribute('aria-hidden', 'false');
}

export function closeSidepanel(panelEl) {
  panelEl.hidden = true;
  panelEl.setAttribute('aria-hidden', 'true');
}

function section(title, ...children) {
  const sec = document.createElement('div');
  sec.className = 'sp-section';
  const h = document.createElement('h3');
  h.textContent = title;
  sec.append(h, ...children);
  return sec;
}

function tweetText(row) {
  const div = document.createElement('div');
  div.className = 'sp-text';
  div.textContent = row.text_resolved || row.text || '';
  return div;
}

function tweetLinks(row) {
  const div = document.createElement('div');
  div.style.marginTop = '6px';
  div.style.fontSize = '12px';
  if (row.tweet_url) {
    const a = document.createElement('a');
    a.className = 'sp-link';
    a.href = row.tweet_url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = 'Open on x.com ↗';
    div.append(a);
  }
  if (row.wayback_url) {
    if (div.childElementCount > 0) div.append(' · ');
    const w = document.createElement('a');
    w.className = 'sp-link';
    w.href = row.wayback_url;
    w.target = '_blank';
    w.rel = 'noopener';
    w.textContent = 'Wayback snapshot ↗';
    div.append(w);
  }
  return div;
}

function grid(rows) {
  const wrap = document.createElement('div');
  wrap.className = 'sp-grid';
  for (const [k, v] of rows) {
    if (v === null || v === undefined || v === '') continue;
    const kEl = document.createElement('div');
    kEl.className = 'k';
    kEl.textContent = k;
    const vEl = document.createElement('div');
    vEl.className = 'v';
    if (typeof v === 'string' && /^https?:\/\//.test(v)) {
      const a = document.createElement('a');
      a.href = v;
      a.target = '_blank';
      a.rel = 'noopener';
      a.className = 'sp-link';
      a.textContent = v;
      vEl.append(a);
    } else {
      vEl.textContent = String(v);
    }
    wrap.append(kEl, vEl);
  }
  return wrap;
}

function idRows(r) {
  return [
    ['tweet_id', r.tweet_id],
    ['account', r.account_handle],
    ['account_id', r.account_id],
    ['posted_at', r.posted_at],
    ['first_captured_at', r.first_captured_at],
    ['last_seen_at', r.last_seen_at],
    ['tweet_type', r.tweet_type],
    ['reply_to', r.reply_to_account ? `@${r.reply_to_account}` : null],
    ['quoted_tweet_id', r.quoted_tweet_id],
    ['retweeted_tweet_id', r.retweeted_tweet_id],
    ['lang', r.lang],
    ['capture_run_id', r.capture_run_id],
  ];
}

function engagementRows(r) {
  return [
    ['likes', fmtNum(r.like_count)],
    ['retweets', fmtNum(r.retweet_count)],
    ['replies', fmtNum(r.reply_count)],
    ['quotes', fmtNum(r.quote_count)],
    ['views', fmtNum(r.view_count)],
    ['bookmarks', fmtNum(r.bookmark_count)],
  ];
}

function mediaGrid(media) {
  const wrap = document.createElement('div');
  wrap.className = 'sp-media';
  for (const m of media) {
    if (!m) continue;
    const card = document.createElement('div');
    card.className = 'm';
    const kind = document.createElement('div');
    kind.textContent = m.media_type ?? 'media';
    kind.style.fontWeight = '600';
    card.append(kind);
    const meta = document.createElement('div');
    meta.className = 'meta';
    const bits = [];
    // Parquet Int64 columns come back from hyparquet as BigInt, which
    // crashes any arithmetic against a JS Number ("Cannot mix BigInt
    // and other types"). Coerce up front so the sidepanel can render
    // rows that carry media regardless of how the codec typed the
    // counts.
    const num = (v) => (typeof v === 'bigint' ? Number(v) : v);
    const w = num(m.width);
    const h = num(m.height);
    const dur = num(m.duration_sec);
    const bytes = num(m.bytes);
    if (w && h) bits.push(`${w}×${h}`);
    if (dur) bits.push(`${Math.round(dur)}s`);
    if (bytes) bits.push(`${Math.round(bytes / 1024)} KiB`);
    meta.textContent = bits.join(' · ') || '—';
    card.append(meta);
    if (m.alt_text) {
      const alt = document.createElement('div');
      alt.style.marginTop = '4px';
      alt.textContent = `alt: ${m.alt_text}`;
      card.append(alt);
    }
    if (m.release_asset_url) {
      const a = document.createElement('a');
      a.href = m.release_asset_url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.className = 'sp-link';
      a.textContent = 'archived asset ↗';
      a.style.display = 'block';
      a.style.marginTop = '4px';
      card.append(a);
    } else if (m.original_url) {
      const a = document.createElement('a');
      a.href = m.original_url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.className = 'sp-link';
      a.textContent = 'original (expires) ↗';
      a.style.display = 'block';
      a.style.marginTop = '4px';
      card.append(a);
    }
    wrap.append(card);
  }
  return wrap;
}

function engagementHistory(history) {
  const wrap = document.createElement('div');
  const tbl = document.createElement('table');
  tbl.className = 'data-table';
  tbl.style.fontSize = '11px';
  const thead = document.createElement('thead');
  thead.innerHTML =
    '<tr><th>Captured</th><th class="cell-num">Likes</th><th class="cell-num">RTs</th><th class="cell-num">Replies</th><th class="cell-num">Views</th></tr>';
  const tbody = document.createElement('tbody');
  for (const s of history.slice().sort((a, b) => (a.captured_at < b.captured_at ? -1 : 1))) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${escape(s.captured_at)}</td><td class="cell-num">${fmtNum(s.likes)}</td><td class="cell-num">${fmtNum(s.retweets)}</td><td class="cell-num">${fmtNum(s.replies)}</td><td class="cell-num">${fmtNum(s.views)}</td>`;
    tbody.append(tr);
  }
  tbl.append(thead, tbody);
  wrap.append(tbl);
  return wrap;
}

function para(text) {
  const p = document.createElement('p');
  p.textContent = text;
  return p;
}

function cardBlock(card) {
  const wrap = document.createElement('div');
  wrap.className = 'sp-card';
  if (card.image_url) {
    const img = document.createElement('img');
    img.className = 'sp-card-image';
    img.loading = 'lazy';
    img.alt = '';
    img.src = card.image_url;
    wrap.append(img);
  }
  const body = document.createElement('div');
  body.className = 'sp-card-body';
  if (card.title) {
    const t = document.createElement('div');
    t.className = 'sp-card-title';
    t.textContent = card.title;
    body.append(t);
  }
  if (card.description) {
    const d = document.createElement('div');
    d.className = 'sp-card-desc';
    d.textContent = card.description;
    body.append(d);
  }
  const url = card.vendor_url || card.card_url;
  if (url) {
    const a = document.createElement('a');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.className = 'sp-link sp-card-link';
    a.textContent = url;
    body.append(a);
  }
  wrap.append(body);
  return wrap;
}

function truncationBadge(row) {
  if (!row.is_truncated) return document.createComment('');
  const div = document.createElement('div');
  div.className = 'sp-badge warn';
  div.textContent =
    'Text likely truncated — only the 280-char head was returned. Open the tweet to capture the full body.';
  return div;
}

function communityNoteBlock(note) {
  const wrap = document.createElement('div');
  const head = document.createElement('div');
  head.style.fontWeight = '600';
  head.textContent = note.title || note.short_title || 'Readers added context';
  wrap.append(head);
  if (note.summary) {
    const body = document.createElement('div');
    body.className = 'sp-text';
    body.style.marginTop = '4px';
    body.textContent = note.summary;
    wrap.append(body);
  }
  if (note.destination_url) {
    const a = document.createElement('a');
    a.href = note.destination_url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.className = 'sp-link';
    a.style.display = 'block';
    a.style.marginTop = '4px';
    a.textContent = 'Open note on x.com ↗';
    wrap.append(a);
  }
  if (note.note_id || note.observed_at) {
    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.style.marginTop = '4px';
    const bits = [];
    if (note.note_id) bits.push(`note_id=${note.note_id}`);
    if (note.observed_at) bits.push(`first seen ${shortDate(note.observed_at)}`);
    meta.textContent = bits.join(' · ');
    wrap.append(meta);
  }
  return wrap;
}

function tagsBlock(row) {
  const wrap = document.createElement('div');
  wrap.className = 'sp-tags';
  const tags = Array.isArray(row.tags) ? row.tags : [];
  if (tags.length === 0) {
    const muted = document.createElement('div');
    muted.className = 'meta';
    muted.textContent =
      'No tags. Either the lexical tagger has not run yet, or no rule fired against this tweet.';
    wrap.append(muted);
    return wrap;
  }
  // Group by namespace so the reader sees `subject:*` together,
  // `topic:*` together, etc. Within a namespace, confirmed first.
  const byNs = new Map();
  for (const entry of tags) {
    const name = typeof entry === 'string' ? entry : entry?.tag;
    if (!name) continue;
    const ns = String(name).split(':', 1)[0];
    if (!byNs.has(ns)) byNs.set(ns, []);
    byNs.get(ns).push(entry);
  }
  for (const [ns, entries] of byNs) {
    const grp = document.createElement('div');
    grp.className = `sp-tag-group ns-${ns}`;
    const lbl = document.createElement('span');
    lbl.className = 'sp-tag-ns';
    lbl.textContent = `${ns}:`;
    grp.append(lbl);
    entries
      .slice()
      .sort((a, b) => Number(!!a?.tentative) - Number(!!b?.tentative))
      .forEach((entry) => {
        const name = typeof entry === 'string' ? entry : entry.tag;
        const tentative = typeof entry === 'object' && entry?.tentative;
        const source = typeof entry === 'object' && entry?.source;
        const pill = document.createElement('span');
        pill.className = `tag-pill ns-${ns}${tentative ? ' tentative' : ''}`;
        pill.textContent = name.split(':').slice(1).join(':') || name;
        pill.title = `${name}${tentative ? ' (tentative)' : ''}${source ? ` — ${source}` : ''}`;
        grp.append(pill);
      });
    wrap.append(grp);
  }
  return wrap;
}

function suggestButton(row) {
  // Opens a prefilled GitHub Discussion. The viewer doesn't have
  // write access; once the maintainer (running the extension with a
  // PAT) acts on it, the discussion closes and the tag overlay
  // updates. See docs/TAGGING.md §Suggestion-flow for the protocol.
  const wrap = document.createElement('div');
  wrap.className = 'sp-suggest';
  const a = document.createElement('a');
  a.className = 'btn ghost sp-suggest-btn';
  a.target = '_blank';
  a.rel = 'noopener';
  const body = encodeURIComponent(
    [
      `tweet_id: ${row.tweet_id ?? ''}`,
      `tweet_url: ${row.tweet_url ?? ''}`,
      `account: @${row.account_handle ?? ''}`,
      '',
      '<!-- Describe the change you want to suggest. -->',
      '',
      'add:',
      '  - subject:detainee',
      '',
      'remove:',
      '  - (none)',
      '',
      'rationale: ',
    ].join('\n')
  );
  const title = encodeURIComponent(
    `tag suggestion: ${row.tweet_id ?? ''} (@${row.account_handle ?? ''})`
  );
  a.href = `https://github.com/vidproject/x/discussions/new?category=tag-suggestions&title=${title}&body=${body}`;
  a.textContent = '✎ Suggest a tag change';
  wrap.append(a);
  const note = document.createElement('div');
  note.className = 'meta sp-suggest-note';
  note.textContent =
    'Opens a GitHub Discussion. The maintainer can apply your suggestion from the extension once they review it.';
  wrap.append(note);
  return wrap;
}

function otherRepliesBlock(slaves) {
  const wrap = document.createElement('div');
  wrap.className = 'sp-other-replies';
  const ordered = slaves.slice().sort((a, b) => {
    const av = String(a.posted_at ?? '');
    const bv = String(b.posted_at ?? '');
    return av.localeCompare(bv);
  });
  for (const r of ordered) {
    const item = document.createElement('div');
    item.className = 'sp-other-reply';
    const head = document.createElement('div');
    head.className = 'sp-other-reply-head';
    const handleEl = document.createElement('span');
    handleEl.className = 'handle';
    handleEl.textContent = `@${r.account_handle ?? ''}`;
    const dateEl = document.createElement('span');
    dateEl.className = 'meta';
    dateEl.textContent = shortDate(r.posted_at) || '';
    head.append(handleEl, dateEl);
    if (r.tweet_url) {
      const a = document.createElement('a');
      a.className = 'sp-link';
      a.href = r.tweet_url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = '↗';
      head.append(a);
    }
    item.append(head);
    const body = document.createElement('div');
    body.className = 'sp-other-reply-body';
    body.textContent = r.text_resolved || r.text || '';
    item.append(body);
    wrap.append(item);
  }
  return wrap;
}

function shortDate(iso) {
  if (typeof iso !== 'string' || iso.length < 10) return iso ?? '';
  return iso.slice(0, 10);
}
function fmtNum(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}
function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : c === '"' ? '&quot;' : '&#39;'
  );
}
