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
  - Songlink / Odesli API  -> optional; needs ODESLI_API_KEY (the free tier was retired
                              on 2026-07-31). Adds YouTube Music, Amazon, Tidal, Pandora links.

Usage:
  python find_links.py                 # normal weekly run
  python find_links.py --refresh-all   # re-check every song on every platform
  python find_links.py --dry-run       # print results, don't write links.json

Standard library only. Python 3.9+.
"""
import base64
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
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
            "links": dict(sorted(links.items())),
        })
    tracks.sort(key=lambda t: (t.get("date") or "", t["title"]), reverse=True)

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
    }

    # Report
    lines = [f"## {cfg['artist_name']} streaming links", "",
             f"{len(tracks)} songs found. New this run: {', '.join(new_titles) or 'none'}.", "",
             source_line, "",
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
