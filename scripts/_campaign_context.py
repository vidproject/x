"""Campaign helper: gather everything needed to describe one media item.

Read-only. Given a ``tweet_id`` (videos) or ``media_id`` (photos), prints the
keyframe / thumbnail file paths to View, plus the text context (tweet text,
OCR-recovered on-image text, transcript) that helps ground a factual,
observational description. Used interactively by the campaign driver before
writing a review JSON.

Run with::

    uv run python -m scripts._campaign_context --tweet-id 123
    uv run python -m scripts._campaign_context --media-id 3_456
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"


def _tweet_text(tweet_id: str) -> dict[str, object]:
    cat = pl.read_parquet(
        DATA_DIR / "catalog.parquet",
        columns=[
            "tweet_id",
            "account_handle",
            "text",
            "text_resolved",
            "like_count",
            "retweet_count",
            "view_count",
            "posted_at",
        ],
    )
    sub = cat.filter(pl.col("tweet_id") == tweet_id)
    if sub.height == 0:
        return {}
    return sub.row(0, named=True)


def _ocr_for(tweet_id: str) -> list[str]:
    p = TAGS_DIR / "image_ocr.parquet"
    if not p.exists():
        return []
    df = pl.read_parquet(p, columns=["tweet_id", "media_id", "text", "status"])
    out = []
    for row in df.filter(pl.col("tweet_id") == tweet_id).iter_rows(named=True):
        txt = str(row.get("text") or "").strip()
        if txt:
            out.append(f"[{row.get('media_id')}] {txt}")
    return out


def _transcript_for(tweet_id: str) -> list[str]:
    p = TAGS_DIR / "transcripts.parquet"
    if not p.exists():
        return []
    df = pl.read_parquet(p, columns=["tweet_id", "text", "status"])
    out = []
    for row in df.filter(pl.col("tweet_id") == tweet_id).iter_rows(named=True):
        txt = str(row.get("text") or "").strip()
        if txt:
            out.append(txt)
    return out


def _audio_for(tweet_id: str) -> list[str]:
    p = TAGS_DIR / "audio_music.parquet"
    if not p.exists():
        return []
    df = pl.read_parquet(
        p,
        columns=[
            "tweet_id",
            "media_id",
            "music_score",
            "speech_score",
            "audio_stream_count",
            "status",
        ],
    )
    out = []
    for row in df.filter(pl.col("tweet_id") == tweet_id).iter_rows(named=True):
        out.append(
            f"[{row.get('media_id')}] streams={row.get('audio_stream_count')} "
            f"music={row.get('music_score')} speech={row.get('speech_score')}"
        )
    return out


def show_video(tweet_id: str) -> None:
    kf = pl.read_parquet(TAGS_DIR / "keyframes.parquet")
    rows = kf.filter((pl.col("tweet_id") == tweet_id) & (pl.col("status") == "ok"))
    meta = _tweet_text(tweet_id)
    print(f"=== VIDEO tweet_id={tweet_id} handle={meta.get('account_handle')} ===")
    print(
        f"posted_at: {meta.get('posted_at')}  "
        f"likes={meta.get('like_count')} rts={meta.get('retweet_count')} "
        f"views={meta.get('view_count')}"
    )
    print(f"TWEET TEXT: {meta.get('text_resolved') or meta.get('text')}")
    ocr = _ocr_for(tweet_id)
    if ocr:
        print("OCR (on-image / keyframe text):")
        for o in ocr:
            print(f"  {o}")
    tr = _transcript_for(tweet_id)
    if tr:
        print("TRANSCRIPT:")
        for t in tr:
            print(f"  {t[:1500]}")
    au = _audio_for(tweet_id)
    if au:
        print("AUDIO:")
        for a in au:
            print(f"  {a}")
    if rows.height == 0:
        print("NO KEYFRAMES ON DISK for this tweet_id")
        return
    for row in rows.iter_rows(named=True):
        sha = str(row.get("media_sha256") or "")
        dur = float(row.get("video_duration_sec") or 0.0)
        print(
            f"--- media_id={row.get('media_id')} sha={sha[:12]} "
            f"dur={dur:.1f}s "
            f"{row.get('video_width')}x{row.get('video_height')} ---"
        )
        for f in row.get("frames") or []:
            ts = float(f.get("timestamp_sec") or 0)
            mm, ss = divmod(int(ts), 60)
            print(f"  {mm}:{ss:02d}  {REPO_ROOT / str(f.get('path') or '')}")
        break  # one physical video per tweet is the norm


def show_photo(media_id: str) -> None:
    pt = pl.read_parquet(TAGS_DIR / "photo_thumbnails.parquet")
    rows = pt.filter(pl.col("media_id") == media_id)
    if rows.height == 0:
        print(f"NO THUMBNAIL for media_id={media_id}")
        return
    row = rows.row(0, named=True)
    tweet_id = str(row.get("tweet_id") or "")
    meta = _tweet_text(tweet_id)
    print(
        f"=== PHOTO media_id={media_id} tweet_id={tweet_id} handle={meta.get('account_handle')} ==="
    )
    print(f"likes={meta.get('like_count')} rts={meta.get('retweet_count')}")
    print(f"TWEET TEXT: {meta.get('text_resolved') or meta.get('text')}")
    ocr = _ocr_for(tweet_id)
    if ocr:
        print("OCR:")
        for o in ocr:
            if media_id in o:
                print(f"  {o}")
    tp = str(row.get("thumbnail_path") or "")
    print(f"THUMBNAIL: {REPO_ROOT / tp}  status={row.get('status')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tweet-id")
    parser.add_argument("--media-id")
    args = parser.parse_args(argv)
    if args.tweet_id:
        show_video(args.tweet_id)
    elif args.media_id:
        show_photo(args.media_id)
    else:
        parser.error("need --tweet-id or --media-id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
