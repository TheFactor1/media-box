# Data flow: paths, ports, who talks to whom

Companion to [PLAN.md](PLAN.md). Hub mode unless stated. Everything runs on the host network namespace; only Jellyfin, Jellyseerr and the wizard are reachable from the LAN.

## Ports
| Service | Port | LAN | Notes |
|---|---|---|---|
| Wizard | 80 | yes | Only while unpaired or when "Set up again" is pressed |
| Jellyfin | 8096 | yes | |
| Jellyseerr | 5055 | yes | Phone requests |
| Sonarr / Radarr | 8989 / 7878 | no (firewall) | Wizard and plugin use them via localhost |
| Prowlarr | 9696 | yes, behind the wizard's auth proxy | Embedded in wizard step 4 for indexers |
| Bazarr | 6767 | no | |
| Decypharr | 8282 | no | qBittorrent-compatible API for the arrs + WebDAV |
| NzbDAV | 3000 | no | SABnzbd-compatible API for the arrs + WebDAV |
| Kodi web server (Chorus + JSON-RPC) | 8080 | yes | Phone remote/keyboard; random per-box password seeded at first boot, shown as a QR on the status screen |
| Tailscale | — | opt-in | `tailscale serve` for Jellyfin when enabled |

## Filesystem
```
/usr/lib/bootc/storage            bound container images, pulled and GC'd by bootc with each OS update
/usr/lib/bootc/bound-images.d/    symlinks to the quadlets whose images bootc must keep
/var/lib/<app>/                   each app's config and database (jellyfin, jellyseerr, sonarr, radarr, prowlarr, bazarr, decypharr, nzbdav)
/var/lib/media/                   Kodi home ($HOME of user media): .kodi/
/var/lib/mediabox/                wizard state, pairing code, mode flag, collected API keys (0600 root)
/mnt/debrid/                      rclone mount of Decypharr's WebDAV     (FUSE, read-only, VFS cache: off)
/mnt/usenet/                      rclone mount of NzbDAV's WebDAV        (FUSE, read-only, VFS cache: off)
/media/library/movies/            symlinks → /mnt/debrid/... or /mnt/usenet/...   ← Jellyfin and Kodi read here
/media/library/tv/
/media/downloads/                 SABnzbd-style fallback: real files when streaming isn't possible
/var/backups/mediabox/            nightly tar of /var/lib/* except caches, 7 kept
```
Kodi (Hub) opens files under `/media/library` directly: Jellyfin for Kodi in direct-path mode, with the Jellyfin library path identical on both sides so no path substitution is needed. Kodi (Player) streams over HTTP from the remote Jellyfin.

All containers run as uid 1000 (`media`) so symlinks, Kodi and the arrs agree on ownership.

## A request, end to end
```
phone ─1─► Jellyseerr ─2─► Radarr ─3─► Prowlarr ─► user's indexers
                               │
                               4  pick release (Recyclarr profile)
                               │
              ┌────────────────┴────────────────┐
         torrent release                  usenet release
              │                                 │
        5a Decypharr                      5b NzbDAV
         (qBittorrent API)               (SABnzbd API)
              │ cached? → symlink in           │ articles present? → file in
              │ /mnt/debrid within seconds     │ /mnt/usenet within seconds
              │ not cached → fail the grab,    │ missing → fail the grab,
              │ Radarr takes the next release  │ Radarr takes the next release
              └────────────────┬────────────────┘
                               6  Radarr "import": creates /media/library/movies/<Title>/<file> as a symlink
                               7  Radarr → Jellyfin "update library" for that path only (Connect → Jellyfin)
                               8  Jellyfin probes the one file (one read from the provider), item appears
                               9  Jellyfin for Kodi receives the websocket event, syncs the one item
                              10  Requests plugin sees Jellyseerr status = available → card turns "Ready"
```
Each numbered hop has a measurable latency; Phase 2's "< 2 min" exit test is the sum.

**Release ordering decision:** both download clients are enabled in Radarr/Sonarr. Debrid first (instant when cached), usenet second. An uncached debrid grab is failed immediately by Decypharr (setting), so the arr moves on to usenet without waiting. If a user only configured one source, only that client exists.

**Step 6 is spike 8.** Decypharr creates its own symlinks and reports that path, so the arr's "move" is a symlink rename. For NzbDAV the plan is the same shape, but the exact import mode (symlink vs. the arr trying to copy a FUSE file, which would download it) has to be verified. A copy here silently breaks the whole design.

## Status feeds for the Requested row
The Kodi requests plugin polls every 10 s, localhost only:
- `GET jellyseerr/api/v1/request?filter=unavailable` → cards and titles.
- `GET radarr/api/v3/queue`, `sonarr/api/v3/queue` → "Looking / Fetching / Downloading %" from `status`, `sizeleft`, `trackedDownloadState`.
- Card disappears when Jellyseerr marks the request `available` and Kodi has played it once (Jellyfin playstate).
- "Stuck" after 15 min with no queue entry or after a grab fails with no remaining releases (`trackedDownloadStatus = warning`).

## Jellyfin settings the image fixes
- Libraries: `/media/library/movies`, `/media/library/tv`. Real-time monitoring **off**, scheduled scan task **disabled**, "enable chapter image extraction" **off**, trickplay **off** for FUSE paths. Every one of these would otherwise read whole files from the provider.
- Hardware transcoding: QSV via `/dev/dri`, for remote clients only.
- Per-item refresh comes from the arrs (step 7) and from Bazarr on subtitle download.

## Wizard writes
The wizard reads each app's generated API key from its config file after first start, then uses only APIs:
| App | What the wizard sets |
|---|---|
| Jellyfin | Admin user, libraries, the settings above, Jellyfin for Kodi device auth |
| Jellyseerr | Link to Jellyfin, Radarr/Sonarr with quality profiles and root folders, user |
| Radarr / Sonarr | Root folders, download clients (Decypharr, NzbDAV) with priorities, Prowlarr sync, Jellyfin connect, Recyclarr-managed profiles |
| Prowlarr | App sync to Radarr/Sonarr; the user adds indexers in Prowlarr's own UI |
| Decypharr | Provider + key, "fail uncached" on, symlink mode, library path |
| NzbDAV | Provider host/creds, mount path |
| Bazarr | Languages from the user's locale, Radarr/Sonarr link |
| Kodi | Passthrough settings from step 2, Jellyfin for Kodi server + direct paths, hub/player mode |
| systemd | `hub.target` or `player.target` enabled, Tailscale, update timer window |

## Updates and health
- `bootc-update.timer`: `bootc upgrade --check` hourly; stage the image; reboot only in the window and only if Kodi reports nothing playing (JSON-RPC `Player.GetActivePlayers` empty) and the arr queues are idle.
- greenboot required checks: `kodi.service` active 30 s after boot, `jellyfin.service` healthy on `:8096/health` (Hub), `/media/library` readable. Any failure → `bootc rollback` and reboot. Toast on next home screen.
- `/var` backup: nightly `tar` of `/var/lib/*` minus `*/cache` and `*/metadata` to `/var/backups/mediabox`, keep 7.

## Security
- No service except Jellyfin, Jellyseerr and the wizard is reachable from the LAN (nftables rule shipped in the image).
- The wizard runs only while unpaired or after "Set up again" on the TV; the pairing code is shown on the TV, so physical access to the TV is the credential.
- Provider keys live in each app's config under `/var/lib/<app>` (0700, uid 1000) and in `/var/lib/mediabox` (0600 root) for re-runs of the wizard. Nothing is sent anywhere except to the provider it belongs to.
- SSH: off by default; the wizard has an "Advanced → enable SSH with this public key" card.
- Logs never leave the box unless the user downloads the bundle.
