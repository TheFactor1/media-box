# Data flow: paths, ports, who talks to whom

Companion to [PLAN.md](PLAN.md). Hub mode unless stated. Everything runs on the host network namespace.

## Ports
| Service | Port | LAN | Notes |
|---|---|---|---|
| Wizard (web app, incl. phone search + Keep this) | 80 | yes | Pairing code needed while unpaired |
| Jellyfin | 8096 | yes | |
| Hub | 8097 | yes | Player calls it over the LAN with the pairing token; setup page and pairing approval live here too |
| Hub discovery | 8098/udp | yes | `MEDIABOX?` → `MEDIABOX 1 <port> <name>`; nothing about the user |
| AIOStreams (on the box) | 3000 | yes, LAN | Only so "Adjust sources" opens its configure page from a phone; otherwise loopback would do |
| Kodi web server (Chorus + JSON-RPC) | 8080 | yes | Phone remote/keyboard; random per-box password seeded at first boot, shown as a QR on the status screen |
| AIOStreams | — | outbound only | The user's instance: LAN (EliteMini), tailnet, or a hosted URL |
| Tailscale / cloudflared | — | opt-in | Tailscale for video, a tunnel for the web pages |

## Filesystem
```
/usr/lib/bootc/storage            bound container images, pulled and GC'd by bootc with each OS update
/usr/lib/bootc/bound-images.d/    symlinks to the quadlets whose images bootc must keep
/var/lib/jellyfin/                Jellyfin config and database
/var/lib/resolver/                resolver config (AIOStreams URL, preset) and its stream cache
/var/lib/media/                   Kodi home ($HOME of user media): .kodi/
/var/lib/mediabox/                wizard state, pairing code, mode flag (0600 root)
/media/library/movies/<Title (Year)>/<Title (Year)>.strm + .nfo     ← Jellyfin and Kodi read here
/media/library/tv/<Show (Year)>/Season 01/<Show> S01E01.strm + .nfo
/media/downloads/                 later "real files" tier
/var/backups/mediabox/            nightly tar of /var/lib/* except caches, 7 kept
```
A `.strm` holds one line: `http://127.0.0.1:8097/play/tt1234567` (plus `/1/3` for S01E03). Kodi opens it directly (Jellyfin for Kodi direct paths), so playback never goes through Jellyfin. In Player mode the box has no library files; Browse works, Keep this needs a hub.

All containers run as uid 1000 (`media`) so Kodi, the resolver and Jellyfin agree on ownership.

## Keep this, end to end
```
phone or TV ─1─► resolver POST /keep/tt1234567
                    2  meta addon (Cinemeta): title, year, art, plot
                    3  AIOStreams: streams for tt1234567 → pick best for preset → badges (res, HDR, audio)
                    4  write /media/library/movies/<Title (Year)>/<Title (Year)>.strm and .nfo (metadata + badges)
                    5  Jellyfin: refresh that path (API) → item appears
                    6  Jellyfin for Kodi: websocket event → one item synced → Recently Added row
```
Target: 1 → 6 in under 30 s; most of it is Jellyfin's metadata fetch.

## Play, end to end
```
Kodi opens the .strm ─1─► GET resolver /play/tt1234567
                            2  AIOStreams: streams right now (cached per title for 10 min)
                            3  pick best for preset (same rule as Keep this) → 302 to the stream URL
Kodi ◄──────────────────────4  direct play: HTTP range requests to TorBox / the usenet stream endpoint
```
Target: first frame < 5 s for cached debrid, < 10 s for usenet. Seeks are range requests; no transcoding on the TV path.

**Where the bytes come from:** a TorBox stream is served by TorBox. A native usenet stream is served by the host running AIOStreams (Matt's EliteMini), which fetches the articles; on a LAN that is free, over the internet it uses that host's upload. Spike 4 measures it.

## Browse anything
Kodi Browse addon → the user's Stremio-protocol addon URLs (AIOStreams for streams, Cinemeta for catalogs/meta). Play uses the same "pick best for preset" rule as the resolver, implemented once in the resolver and called by the addon (`GET /pick/tt…` returns the URL and badges). "In your library" = IMDb id present in Jellyfin (one API call, cached). "Keep this" = `POST /keep`.

## Hub API (the resolver, grown up; decided 2026-10-27)
One small service (Python 3.12 stdlib + sqlite3, one container, port 8097) that both front ends talk to: Player's Home on Android and the Kodi addon on the Fedora box. Jellyfin is no longer in the path.

| Call | Does |
|---|---|
| `GET /health` | `{ok, addons: [..reachable?..]}` |
| `GET /library?collection=continue\|next\|movies\|shows` | Kept titles, filtered/sorted per collection. `continue` = has a resume point under 92 %, most recently watched first; `next` = series with a next episode; `movies`/`shows` = all kept of that type, newest kept first |
| `GET /new?skip=N` | **New releases from the user's catalogs**, not recently kept: the catalog named by `new_catalog` in `hub.toml` (default: Cinemeta's movie and series "top" catalogs filtered to the current and previous year, newest first), with `inLibrary` and `resume` filled in. A fresh box has a populated New tab on day one |
| `POST /keep {id, type}` | Adds the title: metadata from the meta addon, best-stream badges from the stream addons, returns the item. Idempotent |
| `DELETE /library/{id}` | Removes it |
| `GET /play/{id}[/{s}/{e}]?preset=slow\|standard\|fast\|max` | 302 to the best stream URL right now; `?json=1` returns `{url, headers, badges, source}` instead (Player uses this). Overrides: `?max_mbps=`, `?max_res=`, `?cut=`. Streams are **matched** first (year ±1, title tokens with no extras, right episode) and wrong ones dropped; then ranked by the preset (bitrate from size ÷ runtime, caps, cached, cut preference) |
| `GET /presets` | The effective preset table: `slow` (1080p, ≤ 8 Mbps), `standard` (≤ 20), `fast` (≤ 45), `max` (no cap, cached remux first). "Auto" lives in the front end: it measures its link and sends `?max_mbps=` |
| `GET /resume/{id}[/{s}/{e}]` · `PUT` `{positionMs, durationMs}` | `{}` when nothing is stored. Shared positions across front ends; Player writes on exit, reads on launch |
| `GET /browse/catalogs` | The user's catalogs (from the meta/catalog addons' manifests) |
| `GET /browse/catalog/{type}/{catalogId}?skip=N` | Items, with `inLibrary` and `resume` filled in |
| `GET /browse/meta/{type}/{id}` | Full metadata |
| `GET /browse/streams/{type}/{id}[/{s}/{e}]` | Ranked streams with parsed badges; same ranking `/play` uses |
| `GET /search?q=` | Library first (title start, then any word, then genre), then catalog search via the addons |

Item shape, everywhere: `{id (imdb tt…), type (movie|series), title, year, runtimeMin, genres[], poster, backdrop, logo, overview, badges {res, hdr, audio}, inLibrary, keptAt, resume {positionMs, durationMs, updatedAt} | null, nextEpisode {s, e, title} | null}`.

Config: `/var/lib/hub/hub.toml` (addon manifest URLs, meta addon, preset); data: `/var/lib/hub/hub.sqlite`. Stream cache per title 10 min. Addon unreachable → `/play` serves the cached URL if any, else 503 with `{reason}`.

## Resolver rules
- Preset → filter: Best = prefer remux, 4K, HDR, TrueHD/DTS-HD; Balanced = 4K web; Bandwidth saver = 1080p. Within a preset, AIOStreams' own order wins.
- Cache: last good stream URL per title for 10 min; if AIOStreams is unreachable, serve the cached URL if any, else 503 and the status screen shows "Streams: unreachable".
- Never stores provider keys; those live in AIOStreams.

## Jellyfin settings the image fixes
- Libraries: `/media/library/movies`, `/media/library/tv`; `.strm` enabled; real-time monitoring on (cheap: `.strm` are not probed); chapter images and trickplay off (nothing to extract).
- Hardware transcoding: QSV via `/dev/dri`, for remote clients only.

## Wizard writes
| Target | What the wizard sets |
|---|---|
| Jellyfin | Admin user, libraries, the settings above, Jellyfin for Kodi device auth |
| Resolver | AIOStreams manifest URL (+ other addon URLs), meta addon, quality preset |
| Kodi | Passthrough settings from the sound step, Jellyfin for Kodi server + direct paths, Browse addon URLs, hub/player mode |
| systemd | `hub.target` or `player.target`, Tailscale, cloudflared token, update window |

## Updates and health
- `bootc-update.timer`: `bootc upgrade --check` hourly; stage; reboot only in the window and only if Kodi reports nothing playing (JSON-RPC `Player.GetActivePlayers` empty).
- greenboot required checks: `kodi.service` active 30 s after boot, `jellyfin` healthy on `:8096/health` (Hub), resolver answering on `:8097/health`. Any failure → `bootc rollback` and reboot. Toast on next home screen.
- `/var` backup: nightly `tar` of `/var/lib/*` minus `*/cache` and `*/metadata` to `/var/backups/mediabox`, keep 7.

## Security and privacy (Matt's rule, 2026-10-27: both at max)
**Hub**
- **Every call needs a pairing token** except `GET /health` and the setup page while unpaired. The token is 32 random bytes, generated on first start, stored 0600, sent as `Authorization: Bearer`. Pairing: the TV (localhost or an already-paired client) reads a one-time 6-word code from `GET /pair` while a 10-minute window is open; the new box posts the words to `POST /pair` and receives the token; the code dies after one use; 5 guesses a minute. `POST /pair/rotate` invalidates every box. **Usual path:** the TV calls `POST /pair/request {name}` and shows the returned 6-digit code; the phone setup page (or any paired client) approves it (`GET /pair/requests`, `POST /pair/approve`); the TV polls `GET /pair/request/{id}` and receives the token. Nothing is typed on the TV.
- **Setup locks itself:** `/setup` answers only while unconfigured or for 10 minutes after "Set up again" is pressed on the TV; the page is served with a one-time nonce in the QR, no password field, no account.
- **Secrets at rest are encrypted:** the AIOStreams URL (it embeds a config token) and any debrid key the setup page handles are stored encrypted with a key held in a separate 0600 file, so a copied database is useless without the box. Never logged; logs show hosts only, never paths or query strings.
- **Bind to LAN only**: refuses to start on a public IP unless `allow_public = true`; CORS limited to the setup page's own origin; 10 req/s per client on `/setup` and `/play`; request bodies capped at 64 KB.
- **No telemetry, no third-party calls the user didn't configure.** Outbound traffic is only to the user's addon URLs and the meta addon; TMDB cast is off unless the user adds a key. `/health` reports addon reachability without sending anything about the user.
- **The hub never proxies streams** (it 302s), so it never sees what is watched beyond its own library and resume tables, which stay on the box and are in the nightly local backup only.
- **Updates**: the hub image is pinned by digest in the quadlet and compose file; bootc bound images are signed (cosign, Phase 5).

**Player (Android)**
- Hub token kept in Android Keystore-backed storage, never in plain SharedPreferences; cleartext HTTP allowed only to the paired hub's host (network security config), nothing else cleartext.
- No analytics, no crash reporting that leaves the device; the "Report a problem" bundle is a local file the user shares by choice.
- Phone companion stays LAN-only with its existing pairing.

**Box (Fedora)**
- nftables default-deny inbound except hub :8097, Kodi :8080 and the wizard :80 on the LAN interface; SSH off by default; secure boot where the hardware allows; `/var/lib/hub` and `/var/lib/mediabox` 0700.
- Tailscale for remote access (device-to-device, no port forwarding); a Cloudflare tunnel only for the phone pages, never for video.

**Privacy posture for users:** the box talks to the user's own providers and addons only; the project runs no server, collects nothing, and has no accounts. Said plainly on the status screen's "Privacy" card.

- LAN-reachable: the hub (:8097, token required), Kodi web server, the wizard. Jellyfin only in the "real files" tier.
- The wizard runs only while unpaired or after "Set up again" on the TV; the pairing code is shown on the TV, so physical access to the TV is the credential.
- Provider keys never touch the box; they live in the user's AIOStreams. The box stores only the AIOStreams URL (which may embed a config token: `/var/lib/resolver`, 0600).
- SSH: off by default; the wizard has an "Advanced → enable SSH with this public key" card.
- Logs never leave the box unless the user downloads the bundle.
