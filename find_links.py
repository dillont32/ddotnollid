#!/usr/bin/env python3
"""
Ddot Nollid link finder.

Finds every Ddot Nollid release on the streaming services DistroKid delivers to,
looks up each song on every platform through Songlink (Odesli), and writes
links.json, which the website reads.

Sources (no accounts needed unless noted):
  - Deezer public API      -> full catalog + Deezer artist page
  - Apple / iTunes Search  -> catalog + Apple Music artist page
  - Spotify Web API        -> optional; needs SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET
                              and the app owner must have Spotify Premium (Feb 2026 rule)
  - YouTube feeds          -> latest videos and Shorts; YOUTUBE_API_KEY (optional) gets all
  - Songlink / Odesli API  -> optional; needs ODESLI_API_KEY (the free tier was retired
                              on 2026-07-31). Adds YouTube Music, Amazon, Tidal, Pandora links.

Usage:
  python find_links.py                 # normal weekly run
  python find_links.py --refresh-all   # re-check every song on every platform
  python find_links.py --dry-run       # print results, don't write links.json

Standard library only. Python 3.9+.
"""
import base64
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# Works whether this file sits next to links.config.json or in a scripts/ folder.
ROOT = HERE if os.path.exists(os.path.join(HERE, "links.config.json")) else os.path.dirname(HERE)
CONFIG_PATH = os.path.join(ROOT, "links.config.json")
OUT_PATH = os.path.join(ROOT, "links.json")
UA = "DdotNollidLinkFinder/1.0"

# Songlink platform key -> the website's platform id
ODESLI_MAP = {
    "spotify": "spotify",
    "appleMusic": "apple",
    "youtubeMusic": "ytmusic",
    "amazonMusic": "amazon",
    "deezer": "deezer",
    "tidal": "tidal",
    "pandora": "pandora",
    "youtube": "youtube",
    "soundcloud": "soundcloud",
    "audiomack": "audiomack",
    "anghami": "anghami",
    "boomplay": "boomplay",
    "napster": "napster",
}
CORE_PLATFORMS = ["spotify", "apple", "ytmusic", "amazon", "deezer", "tidal", "pandora", "youtube"]


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def http_json(url, headers=None, data=None, tries=4):
    """GET/POST JSON. Retries on rate limits and server errors; returns None on other failures."""
    host = urllib.parse.urlsplit(url).netloc
    for attempt in range(tries):
        req = urllib.request.Request(
            url, data=data,
            headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            # Deezer reports errors (including its rate limit) inside a normal-looking answer.
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                err = data["error"]
                log(f"  {host} error: {err.get('message')} (code {err.get('code')})")
                if err.get("code") in (4, 700):  # 4 = quota exceeded, 700 = service busy
                    time.sleep(6 * (attempt + 1))
                    continue
                return None
            if "deezer.com" in host:
                time.sleep(0.15)  # stay well under Deezer's 50 requests / 5 seconds
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
                log(f"  {host} answered {e.code}; waiting {wait}s")
                time.sleep(min(wait, 120))
                continue
            log(f"  {host} answered {e.code} for {url.split('?')[0]}")
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            log(f"  {host} unreachable ({e}); retrying")
            time.sleep(5 * (attempt + 1))
    return None


def norm(s):
    """Normalize a title for matching across stores."""
    s = re.sub(r"['‘’`´]", "", s or "")  # don't / don’t -> dont
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[\(\[](feat|ft|with)\.?[^\)\]]*[\)\]]", "", s)  # drop (feat. X)
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------- sources

def from_deezer(cfg):
    name = cfg["artist_name"]
    artist_id = cfg.get("deezer_artist_id")
    if not artist_id:
        res = http_json("https://api.deezer.com/search/artist?q=" + urllib.parse.quote(name)) or {}
        hits = [a for a in res.get("data", []) if norm(a.get("name")) == norm(name)]
        if not hits:
            log("Deezer: no artist named", name)
            return None, []
        artist_id = hits[0]["id"]
    artist = http_json(f"https://api.deezer.com/artist/{artist_id}") or {}
    tracks = []
    url = f"https://api.deezer.com/artist/{artist_id}/albums?limit=100"
    while url:
        page = http_json(url) or {}
        for album in page.get("data", []):
            listing = http_json(f"https://api.deezer.com/album/{album['id']}/tracks?limit=100") or {}
            for t in listing.get("data", []):
                tracks.append({
                    "title": t.get("title"),
                    "release": album.get("title"),
                    "type": album.get("record_type"),
                    "date": album.get("release_date"),
                    "art": album.get("cover_xl") or album.get("cover_big"),
                    "seed": t.get("link"),
                    "preview": t.get("preview") if t.get("preview") and "hdnea" not in t.get("preview") else None,
                    "platform": "deezer",
                })
        url = page.get("next")
    log(f"Deezer: {len(tracks)} songs")
    return artist.get("link"), tracks


def from_apple(cfg):
    name = cfg["artist_name"]
    params = {"term": name, "entity": "song", "attribute": "artistTerm",
              "limit": 200, "country": cfg.get("country", "US")}
    res = http_json("https://itunes.apple.com/search?" + urllib.parse.urlencode(params)) or {}
    artist_url, tracks = None, []
    for r in res.get("results", []):
        if norm(r.get("artistName")) != norm(name):
            continue
        artist_url = artist_url or (r.get("artistViewUrl") or "").split("?")[0] or None
        tracks.append({
            "title": r.get("trackName"),
            "release": r.get("collectionName"),
            "type": "single" if (r.get("trackCount") or 0) <= 3 else "album",
            "date": (r.get("releaseDate") or "")[:10],
            "art": (r.get("artworkUrl100") or "").replace("100x100bb", "1000x1000bb"),
            "seed": re.sub(r"[?&]uo=\d+", "", r.get("trackViewUrl") or "") or None,
            "platform": "apple",
            "preview": r.get("previewUrl"),
        })
    log(f"Apple Music: {len(tracks)} songs")
    return artist_url, tracks


def from_spotify(cfg):
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    artist_id = cfg.get("spotify_artist_id")
    if not (cid and secret and artist_id):
        log("Spotify: skipped (no API keys set). Spotify buttons on the site will open a Spotify search for each song.")
        return []
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    token = http_json("https://accounts.spotify.com/api/token",
                      {"Authorization": "Basic " + auth,
                       "Content-Type": "application/x-www-form-urlencoded"},
                      b"grant_type=client_credentials")
    if not token:
        log("Spotify: sign-in failed. Check the keys, and that the app owner has Spotify Premium.")
        return []
    H = {"Authorization": "Bearer " + token["access_token"]}
    tracks = []
    url = (f"https://api.spotify.com/v1/artists/{artist_id}/albums"
           f"?include_groups=album,single&limit=10&market={cfg.get('country', 'US')}")
    while url:
        page = http_json(url, H)
        if page is None:
            log("Spotify: could not list releases; continuing with other sources")
            break
        for album in page.get("items", []):
            imgs = album.get("images") or [{}]
            turl = f"https://api.spotify.com/v1/albums/{album['id']}/tracks?limit=50"
            while turl:
                tl = http_json(turl, H) or {}
                for t in tl.get("items", []):
                    tracks.append({
                        "title": t.get("name"),
                        "release": album.get("name"),
                        "type": album.get("album_type"),
                        "date": album.get("release_date"),
                        "art": imgs[0].get("url"),
                        "seed": (t.get("external_urls") or {}).get("spotify"),
                        "platform": "spotify",
                    })
                turl = tl.get("next")
        url = page.get("next")
    log(f"Spotify: {len(tracks)} songs")
    return tracks


def download(url, path):
    """Save an image file. Returns True only for a real JPEG or PNG."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as e:  # network errors, 404s
        log(f"  cover download failed ({e}): {url}")
        return False
    if len(data) < 2000 or not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        log(f"  cover download returned something other than an image: {url}")
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return True


YT_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def youtube_feed(playlist_id, kind):
    """Read YouTube's public feed for a playlist (about 15 newest entries, no key needed)."""
    url = "https://www.youtube.com/feeds/videos.xml?playlist_id=" + playlist_id
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            root = ET.fromstring(resp.read())
    except Exception as e:
        log(f"  YouTube feed unavailable ({e}); keeping the videos found earlier")
        return None
    items = []
    for entry in root.findall("a:entry", YT_NS):
        vid = entry.findtext("yt:videoId", namespaces=YT_NS)
        link_el = entry.find("a:link", YT_NS)
        link = link_el.get("href", "") if link_el is not None else ""
        if not vid:
            continue
        items.append({
            "id": vid,
            "title": entry.findtext("a:title", namespaces=YT_NS) or "",
            "published": (entry.findtext("a:published", namespaces=YT_NS) or "")[:10],
            "kind": "short" if (kind == "short" or "/shorts/" in link) else "video",
        })
    return items


def youtube_api(playlist_id, kind, key):
    """Full list through the YouTube Data API (needs a free YOUTUBE_API_KEY)."""
    items, token = [], ""
    while True:
        params = {"part": "snippet,contentDetails", "maxResults": 50, "playlistId": playlist_id, "key": key}
        if token:
            params["pageToken"] = token
        page = http_json("https://www.googleapis.com/youtube/v3/playlistItems?" + urllib.parse.urlencode(params))
        if page is None:
            return None if not items else items
        for it in page.get("items", []):
            sn, cd = it.get("snippet") or {}, it.get("contentDetails") or {}
            if sn.get("title") in ("Private video", "Deleted video"):
                continue
            items.append({
                "id": cd.get("videoId"),
                "title": html.unescape(sn.get("title") or ""),
                "published": (cd.get("videoPublishedAt") or sn.get("publishedAt") or "")[:10],
                "kind": kind,
            })
        token = page.get("nextPageToken")
        if not token:
            return items


def yt_api(path, params, key):
    return http_json("https://www.googleapis.com/youtube/v3/" + path + "?" + urllib.parse.urlencode(dict(params, key=key)))


def youtube_full_songs(cfg, tracks, cache, previous, key):
    """Find each song's full-length YouTube Music track (DistroKid delivers these to the
    artist's auto-generated "Topic" channel). Plays of these count as YouTube Music streams."""
    artist = cfg["artist_name"]
    topic = cfg.get("youtube_topic_channel_id") or previous.get("youtube_topic_channel")
    if not topic:  # search: 100 quota units, done once and remembered
        res = yt_api("search", {"part": "snippet", "type": "channel", "q": f"{artist} - Topic", "maxResults": 5}, key) or {}
        for it in res.get("items", []):
            if norm(html.unescape(it["snippet"].get("title", ""))) == norm(f"{artist} - Topic"):
                topic = it["snippet"]["channelId"]
                break
    by_title = {}
    if topic:  # the Topic channel's uploads: 1 quota unit per 50 songs
        for v in youtube_api("UU" + topic[2:], "music", key) or []:
            by_title.setdefault(norm(html.unescape(v["title"])), v["id"])
    searches, limit = 0, int(cfg.get("youtube_search_limit", 25))
    for t in tracks:
        k = norm(t["title"])
        vid = by_title.get(k) or (cache.get(k) or {}).get("youtube")
        if not vid and searches < limit:  # per-song search: 100 units each, capped per run
            searches += 1
            params = {"part": "snippet", "type": "video", "q": f"{artist} {t['title']}", "maxResults": 5}
            if topic:
                params["channelId"] = topic
            res = yt_api("search", params, key) or {}
            for it in res.get("items", []):
                sn = it.get("snippet") or {}
                if norm(html.unescape(sn.get("title", ""))) == k and (topic or sn.get("channelTitle", "").endswith("- Topic")):
                    vid = (it.get("id") or {}).get("videoId")
                    break
        if vid:
            t["youtube"] = vid
    log(f"YouTube Music: Topic channel {topic or 'not found'}; full songs for "
        f"{sum(1 for t in tracks if t.get('youtube'))} of {len(tracks)} ({searches} searches)")
    return topic


def from_youtube(cfg, previous):
    """Videos and Shorts for the Watch section. Keeps earlier finds when using the feeds."""
    channel = cfg.get("youtube_channel_id")
    if not channel:  # fall back to the channel in artist_links
        m = re.search(r"/channel/(UC[\w-]+)", (cfg.get("artist_links") or {}).get("youtube", ""))
        channel = m.group(1) if m else None
    if not channel or not channel.startswith("UC"):
        return previous
    base = channel[2:]
    key = os.environ.get("YOUTUBE_API_KEY")
    known = {v["id"]: v for v in previous}
    got_any = False
    for prefix, kind in (("UULF", "video"), ("UUSH", "short")):
        items = youtube_api(prefix + base, kind, key) if key else youtube_feed(prefix + base, kind)
        if items is None:
            continue
        got_any = True
        if key:  # the API gives the complete list, so drop anything no longer on the channel
            known = {k: v for k, v in known.items() if v.get("kind") != kind}
        for v in items:
            known[v["id"]] = v
    if not got_any and not key:
        # Fall back to the combined uploads feed
        items = youtube_feed("UU" + base, "video") or []
        for v in items:
            known[v["id"]] = v
    videos = sorted(known.values(), key=lambda v: (v.get("published") or "", v["id"]), reverse=True)
    log(f"YouTube: {sum(v['kind'] == 'video' for v in videos)} videos, "
        f"{sum(v['kind'] == 'short' for v in videos)} Shorts" + ("" if key else " (feeds; add YOUTUBE_API_KEY for the full catalog)"))
    return videos


def songlink(url, cfg):
    params = {"url": url, "userCountry": cfg.get("country", "US")}
    key = os.environ.get("ODESLI_API_KEY")
    if key:
        params["key"] = key
    data = http_json("https://api.song.link/v1-alpha.1/links?" + urllib.parse.urlencode(params))
    time.sleep(0.5 if key else 6.5)  # free tier: about 10 lookups a minute
    if not data:
        return None, {}
    by_platform = data.get("linksByPlatform") or {}
    links = {site: (by_platform.get(od) or {}).get("url") for od, site in ODESLI_MAP.items()}
    return data.get("pageUrl"), {k: v for k, v in links.items() if v}


# ---------------------------------------------------------------- main

def main():
    refresh_all = "--refresh-all" in sys.argv
    dry_run = "--dry-run" in sys.argv
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    previous = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as f:
            previous = json.load(f)
    cache = {norm(t["title"]): t for t in previous.get("tracks", [])}

    spotify_tracks = from_spotify(cfg)
    deezer_artist, deezer_tracks = from_deezer(cfg)
    apple_artist, apple_tracks = from_apple(cfg)
    source_line = (f"Found this run: Spotify {len(spotify_tracks)}, Deezer {len(deezer_tracks)}, "
                   f"Apple Music {len(apple_tracks)} songs.")
    log(source_line)

    # Merge by title. Earlier sources win the "seed" link used for the Songlink lookup.
    found = {}
    for t in spotify_tracks + deezer_tracks + apple_tracks:
        if not t.get("title") or not t.get("seed"):
            continue
        key = norm(t["title"])
        if key not in found:
            found[key] = dict(t, seeds=[t["seed"]], direct={t["platform"]: t["seed"]})
        else:
            found[key]["seeds"].append(t["seed"])
            found[key]["direct"].setdefault(t["platform"], t["seed"])
            if t.get("preview") and (t["platform"] == "apple" or not found[key].get("preview")):
                found[key]["preview"] = t["preview"]  # Apple previews preferred: they don't expire
            for field in ("release", "type", "date", "art"):
                found[key][field] = found[key].get(field) or t.get(field)

    # Keep songs from earlier runs even if a store temporarily hides them.
    for key, old in cache.items():
        if key not in found:
            found[key] = dict(old, seeds=[], direct={})

    min_platforms = int(cfg.get("min_platforms", 6))
    # Songlink retired its free API on 2026-07-31; it now needs a key from Linktree.
    use_songlink = bool(os.environ.get("ODESLI_API_KEY"))
    if not use_songlink:
        log("Songlink: skipped (no ODESLI_API_KEY). Using direct Spotify, Apple Music and Deezer links.")
    overrides = {norm(k): v for k, v in (cfg.get("track_links") or {}).items()}
    tracks, new_titles = [], []
    for key, t in found.items():
        old = cache.get(key) or {}
        links = dict(old.get("links") or {})
        links.update(t.get("direct") or {})  # exact links straight from Spotify, Deezer and Apple
        page = old.get("songlink")
        if use_songlink and (refresh_all or not old or len(links) < min_platforms):
            for seed in t["seeds"]:
                log(f"Songlink: {t['title']}")
                page_new, links_new = songlink(seed, cfg)
                links.update(links_new)
                page = page_new or page
                if len(links) >= min_platforms:
                    break
        links.update(overrides.get(key, {}))
        if not old:
            new_titles.append(t["title"])
        tracks.append({
            "title": t["title"],
            "release": t.get("release"),
            "type": t.get("type"),
            "date": t.get("date"),
            "art": t.get("art"),
            "songlink": page,
            "preview": t.get("preview") or old.get("preview"),
            "links": dict(sorted(links.items())),
        })
    tracks.sort(key=lambda t: (t.get("date") or "", t["title"]), reverse=True)

    # Save each release's cover into covers/ so the site serves its own artwork
    # instead of depending on Deezer's or Apple's image servers.
    saved_covers = 0
    for t in tracks:
        remote = t.get("art")
        if not remote or not remote.startswith("http"):
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", norm(t.get("release") or t["title"])).strip("-") or "cover"
        rel_path = f"covers/{slug}.jpg"
        path = os.path.join(ROOT, rel_path)
        if not os.path.exists(path):
            if dry_run:
                continue
            if "dzcdn.net" in remote:
                url = re.sub(r"/\d+x\d+-", "/600x600-", remote)
            else:
                url = remote.replace("1000x1000bb", "600x600bb")
            if not download(url, path):
                continue
            saved_covers += 1
        t["art"] = rel_path
    log(f"Covers saved this run: {saved_covers}")

    videos = from_youtube(cfg, previous.get("videos") or [])
    yt_key = os.environ.get("YOUTUBE_API_KEY")
    topic = previous.get("youtube_topic_channel")
    if yt_key:
        topic = youtube_full_songs(cfg, tracks, cache, previous, yt_key)
    else:  # keep what earlier runs found
        for t in tracks:
            old_vid = (cache.get(norm(t["title"])) or {}).get("youtube")
            if old_vid:
                t["youtube"] = old_vid

    artist_links = {"spotify": f"https://open.spotify.com/artist/{cfg['spotify_artist_id']}"} \
        if cfg.get("spotify_artist_id") else {}
    if apple_artist:
        artist_links["apple"] = apple_artist
    if deezer_artist:
        artist_links["deezer"] = deezer_artist
    artist_links.update({k: v for k, v in (cfg.get("artist_links") or {}).items() if v})

    out = {
        "artist": cfg["artist_name"],
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artist_links": dict(sorted(artist_links.items())),
        "tracks": tracks,
        "videos": videos,
        "youtube_topic_channel": topic,
    }

    # Report
    lines = [f"## {cfg['artist_name']} streaming links", "",
             f"{len(tracks)} songs found. New this run: {', '.join(new_titles) or 'none'}.", "",
             source_line, f"New cover images saved: {saved_covers}.",
             f"Songs with a preview: {sum(1 for t in tracks if t.get('preview'))} of {len(tracks)}.",
             f"YouTube: {sum(v['kind'] == 'video' for v in videos)} videos, {sum(v['kind'] == 'short' for v in videos)} Shorts.",
             f"Full songs on YouTube Music: {sum(1 for t in tracks if t.get('youtube'))} of {len(tracks)}.", "",
             "| Song | " + " | ".join(CORE_PLATFORMS) + " |",
             "|---|" + "---|" * len(CORE_PLATFORMS)]
    for t in tracks:
        lines.append(f"| {t['title']} | " + " | ".join("✓" if p in t["links"] else "·" for p in CORE_PLATFORMS) + " |")
    lines += ["", "Artist pages: " + (", ".join(sorted(artist_links)) or "none")]
    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")

    if not tracks and not previous:
        log("No songs found. Check artist_name in links.config.json.")
        sys.exit(1)

    unchanged = {k: v for k, v in previous.items() if k != "updated"} == \
                {k: v for k, v in out.items() if k != "updated"}
    if dry_run:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif unchanged:
        log("No changes.")
    else:
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
            f.write("\n")
        log(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
