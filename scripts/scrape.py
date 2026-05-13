#!/usr/bin/env python3
"""URL scraping utilities. TikTok: caption first, Groq Whisper transcription fallback."""
import json
import os
import re
import sys
import tempfile
import requests
from bs4 import BeautifulSoup


def scrape_url(url):
    if "tiktok.com" in url:
        return scrape_tiktok(url)
    return _scrape_generic(url)


def _scrape_generic(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    nyt_ing = soup.find_all(class_=lambda x: x and "recipe-ingredients" in x)
    nyt_inst = soup.find_all(class_=lambda x: x and "recipe-instructions" in x)

    if nyt_ing or nyt_inst:
        content = "\n".join(i.get_text() for i in nyt_ing + nyt_inst)
    else:
        main_el = soup.find("main") or soup.find("article") or soup.body
        content = main_el.get_text() if main_el else soup.get_text()

    cleaned = " ".join(content.split())[:4000]
    print(f"Scraped {len(cleaned)} chars from {url}", file=sys.stderr)
    return cleaned


def scrape_tiktok(url):
    """Extract recipe from TikTok: caption first, audio transcription fallback."""
    caption = _tiktok_caption(url)
    if caption and len(caption) > 80:
        print(f"Using TikTok caption ({len(caption)} chars)", file=sys.stderr)
        return caption

    print("Caption insufficient — downloading audio for transcription...", file=sys.stderr)
    return _transcribe_tiktok(url)


def _tiktok_caption(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"TikTok fetch failed: {e}", file=sys.stderr)
        return None

    # Try the embedded rehydration JSON blob first
    match = re.search(
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r.text, re.DOTALL
    )
    if match:
        try:
            data = json.loads(match.group(1))
            desc = _find_tiktok_desc(data)
            if desc:
                return desc
        except Exception:
            pass

    # Fallback: og:description / meta description
    soup = BeautifulSoup(r.text, "html.parser")
    meta = (soup.find("meta", {"property": "og:description"})
            or soup.find("meta", {"name": "description"}))
    if meta and meta.get("content"):
        content = meta["content"]
        # Strip trailing engagement stats TikTok appends ("1.2M likes, 340 comments…")
        content = re.sub(
            r'\s*[\d,.]+[KMB]?\s*(likes?|comments?|shares?|views?).*',
            '', content, flags=re.IGNORECASE
        )
        return content.strip() or None

    return None


def _find_tiktok_desc(data):
    """Walk TikTok's rehydration JSON to find the video description."""
    if not isinstance(data, dict):
        return None
    # Known path as of 2025
    try:
        scope = data["__DEFAULT_SCOPE__"]
        detail = scope.get("webapp.video-detail", {})
        desc = detail.get("itemInfo", {}).get("itemStruct", {}).get("desc", "")
        if desc:
            return desc
    except (KeyError, TypeError):
        pass
    # Recursive fallback across all dict values
    for v in data.values():
        if isinstance(v, dict):
            result = _find_tiktok_desc(v)
            if result:
                return result
    return None


def _transcribe_tiktok(url):
    """Download TikTok audio with yt-dlp and transcribe with Groq Whisper."""
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp not installed — run: pip install yt-dlp")

    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY env var not set — required for TikTok transcription")

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(audio_path):
            # yt-dlp may produce audio.mp3 directly or with a different stem
            candidates = [f for f in os.listdir(tmpdir) if f.endswith(".mp3")]
            if not candidates:
                raise RuntimeError("yt-dlp did not produce an mp3 file")
            audio_path = os.path.join(tmpdir, candidates[0])

        print("Transcribing via Groq Whisper...", file=sys.stderr)
        with open(audio_path, "rb") as f:
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": ("audio.mp3", f, "audio/mpeg")},
                data={"model": "whisper-large-v3-turbo", "response_format": "text"},
                timeout=120,
            )
        resp.raise_for_status()
        transcript = resp.text.strip()
        print(f"Transcript: {len(transcript)} chars", file=sys.stderr)
        return transcript
