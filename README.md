# Ddot Nollid website + link finder

This folder is the full Ddot Nollid website plus a small program (the **link finder**) that keeps its streaming links up to date.

Every Monday the link finder:

1. Finds every Ddot Nollid song on Deezer and Apple Music (plus Spotify, if you add keys).
2. Looks up each song on Songlink to get its page on Spotify, Apple Music, YouTube Music, Amazon Music, Deezer, Tidal, Pandora, YouTube and more.
3. Saves everything to `links.json`. The website reads that file, so each Listen button opens the exact song, and new releases appear on the site automatically.
4. Republishes the site on GitHub Pages (free hosting).

It checks a song again each week until it has links on at least 6 platforms, because stores go live at different times after a DistroKid release.

## One-time setup (about 15 minutes)

### 1. Make the repository
1. Sign in to github.com as **dillont32**.
2. Go to https://github.com/new. Set the owner to **dillont32**, name the repository `ddotnollid`, set it to **Public**, and click **Create repository**. Your repository will be at https://github.com/dillont32/ddotnollid.
3. Click **uploading an existing file** and drag in everything from this folder: `index.html`, `links.config.json`, `README.md`, `.nojekyll`, and the `img` and `scripts` folders. Click **Commit changes**.
4. Add the schedule file. Folders that start with a dot are often hidden and don't upload by dragging, so create this one by hand:
   - Click **Add file → Create new file**.
   - In the name box, type `.github/workflows/site.yml`.
   - Paste in the contents of `.github/workflows/site.yml` from this folder, then click **Commit changes**.

### 2. Turn on the website
1. Go to https://github.com/dillont32/ddotnollid/settings/pages.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.

### 3. Run it the first time
1. Go to https://github.com/dillont32/ddotnollid/actions. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Refresh links and publish site → Run workflow → Run workflow**.
3. It takes 2–5 minutes. When it finishes, open the run to see a table of every song and which platforms it was found on.
4. The site is live at `https://dillont32.github.io/ddotnollid/`.

From then on it runs every Monday morning by itself. You can also click **Run workflow** right after a new release.

## Adding links it can't find on its own

The finder gets **song** links on every platform, and **artist page** links for Spotify, Apple Music and Deezer. Amazon Music, YouTube Music, Tidal and Pandora don't offer a public way to look up an artist page, so paste those into `links.config.json` under `artist_links` once:

```json
"artist_links": {
  "amazon": "https://music.amazon.com/artists/...",
  "ytmusic": "https://music.youtube.com/channel/...",
  "tidal": "https://tidal.com/artist/...",
  "pandora": "https://www.pandora.com/artist/..."
}
```

To force a specific link for one song, add it under `track_links`:

```json
"track_links": {
  "They Don't Understand": { "spotify": "https://open.spotify.com/track/..." }
}
```

Edit it at https://github.com/dillont32/ddotnollid/edit/main/links.config.json, click **Commit changes**, and the site republishes.

## Optional: Spotify keys

The finder works without Spotify keys: Songlink still returns Spotify links for songs it finds on Deezer and Apple Music. Keys add Spotify as a third place to discover songs, which helps if a release reaches Spotify first.

Since February 2026, Spotify requires the developer app's owner to have **Spotify Premium**. If you have it:

1. Go to developer.spotify.com/dashboard, click **Create app**, choose **Web API**, and use any name. For the redirect URI, enter `https://dillont32.github.io/ddotnollid/`.
2. Copy the **Client ID** and **Client secret**.
3. Go to https://github.com/dillont32/ddotnollid/settings/secrets/actions and click **New repository secret**. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.

## Optional: your own domain

Buy a domain such as `ddotnollid.com`, then enter it under **Custom domain** at https://github.com/dillont32/ddotnollid/settings/pages and follow GitHub's DNS instructions.

## If something goes wrong

- **The run fails at "Save links.json" with a permission error:** go to https://github.com/dillont32/ddotnollid/settings/actions, scroll to **Workflow permissions**, choose **Read and write permissions**, and save.
- **"No songs found":** check that `artist_name` in `links.config.json` matches your artist name on Deezer and Apple Music exactly. You can also set `deezer_artist_id` to the number in your Deezer artist URL.
- **A song is missing a platform:** some stores take a week or more to go live. The finder keeps checking until a song has 6 platforms. Run it with **Re-check every song** ticked to refresh everything.

## Running it on your own computer

You need Python 3.9 or newer; nothing else needs installing.

```
python scripts/find_links.py --dry-run     # show results without saving
python scripts/find_links.py               # save links.json
```
