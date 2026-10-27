# Media mini-PC distro — plan

Working name: **TBD**. Status: plan, nothing built yet. Last checked against upstream: 2026-10-27.

Companion documents: [EXPERIENCE.md](EXPERIENCE.md) (what the user sees, screen by screen) and [DATAFLOW.md](DATAFLOW.md) (paths, ports, request pipeline, what the wizard writes).

## 1. Product

**One sentence:** a mini PC that boots straight into a beautiful TV interface where your Jellyfin library, debrid and usenet all appear as one library, playing the highest-quality release at full fidelity, managed entirely from a remote and a phone.

### "Premium" as measurable targets
| Target | Number |
|---|---|
| Power button → home screen | < 20 s cold boot |
| Press play → first frame (local / debrid cached / usenet stream) | < 2 s / < 5 s / < 10 s |
| Request on phone → playable on TV (cached debrid or usenet) | < 2 min |
| Fresh box → first play, no keyboard | < 10 min |
| Terminals the user ever sees | 0 |
| Updates that leave the box unbootable | 0 (auto-rollback) |
| Output | 4K60, HDR10 / HLG, 10-bit, refresh rate matched to content; TrueHD/Atmos, DTS-HD/DTS:X passthrough |

### Non-goals (v1)
- Netflix / Disney+ / etc. at 4K. Their DRM caps Linux at 720p–1080p; not fixable.
- Dolby Vision. Best effort only (HDR10 fallback layer).
- ARM boards (Pi, Amlogic). CoreELEC owns that space.
- General desktop use, gaming, multi-room sync.
- Built-in torrent client. Debrid only.

### Legal posture (decide once, apply everywhere)
Ship plumbing, never sources. Debrid, usenet, Jellyfin and the *arr apps are legal tools. The distro ships **no** preconfigured indexers, scrapers, torrent addons or provider credentials. The setup wizard asks the user for *their* debrid key, usenet provider and indexers. Docs say nothing about where to find content.

## 2. Two modes, one image

| Mode | What runs | For whom |
|---|---|---|
| **Hub** (default) | Player + full server stack on the box | One-box households |
| **Player** | Player only, pointed at an existing Jellyfin server | People who already self-host |

Player mode is close to LibreELEC + the Jellyfin addon with a better skin; it's nearly free to keep (one systemd target) but Hub mode is the product. Don't lead with Player.

Same OS image. The mode is just which systemd target is enabled (`hub.target` pulls in the server containers; `player.target` doesn't). Chosen in the wizard, changeable later.

## 3. Architecture

```
 phone ──► Setup wizard / Jellyseerr (web)
             │
┌────────────▼───────────────────── mini PC ──────────────────────────────┐
│ PLAYER                                                                   │
│  Kodi 22 (GBM, no compositor) ─ skin ─ Jellyfin for Kodi addon           │
│  ALSA direct (passthrough) · libcec / USB remote · greenboot health      │
│                                                                          │
│ HUB (Podman quadlets, part of the image)                                 │
│  Jellyseerr ─► Sonarr / Radarr (+ Recyclarr quality profiles)            │
│                   │ torrent releases          │ usenet releases          │
│                   ▼                           ▼                          │
│               Decypharr ──► debrid        NzbDAV ──► usenet provider      │
│               (WebDAV/rclone mount)       (WebDAV/rclone mount)          │
│                   └────────► /media/library (symlinks) ◄──┘              │
│                                     │                                    │
│  Prowlarr (user's indexers)      Jellyfin ◄──────────────────────────────┤
│                                  (one library, metadata, watch state)    │
│ BASE: Fedora bootc (ublue base image) · atomic updates · rollback        │
└──────────────────────────────────────────────────────────────────────────┘
```

The idea that holds it together: debrid and usenet never stream to Kodi directly. Both are mounted as files and symlinked into a normal library, so **Jellyfin is the single source of truth** and Kodi is just an excellent Jellyfin client. Nothing about the player knows where a file came from.

**Playback path differs by mode.** In Hub mode Kodi opens files from the mount path directly (Jellyfin for Kodi "direct paths"), so playback and seeking skip the Jellyfin HTTP hop: Kodi → FUSE → rclone → NzbDAV/Decypharr → provider. In Player mode the mount isn't local, so Kodi streams over HTTP from Jellyfin. Phase 0 measures seek latency both ways.

**Jellyfin never scans the mounts.** Probing a file on a FUSE mount downloads from the provider, so a library scan of thousands of titles costs real bandwidth and rate limits. Library scanning and real-time monitoring are off; Sonarr and Radarr notify Jellyfin per item on import.

### Component picks
| Layer | Pick | Why | Fallback |
|---|---|---|---|
| Base OS | `ghcr.io/ublue-os/base-main` via [ublue image-template](https://github.com/ublue-os/image-template) | Fedora bootc with codecs and Intel media driver already in; CI + ISO workflow included | `quay.io/fedora/fedora-bootc` + RPM Fusion |
| ISO build | bootc-image-builder (in the template) | Works today | Move to `bootc-installer` / image-builder-cli; osbuild is deprecating the old `iso` type |
| Player | Kodi 22 "Piers" on GBM | 22 rewrote GBM/DRM rendering and added end-to-end HDR (RC as of Oct 2026) | Kodi 21 GBM until 22 is packaged |
| Audio | ALSA direct from Kodi | Most reliable bitstream passthrough; PipeWire not involved in playback | — |
| Jellyfin in Kodi | **Jellyfin for Kodi** (DB sync; direct paths in Hub mode) | Syncs into Kodi's database, so skin widgets (continue watching, new episodes) work natively | JellyCon if sync proves slow on big libraries |
| Subtitles | **Bazarr** | Forced and foreign-language subs are where streamed remuxes fall short; Bazarr fetches them per item | — |
| Requests in Kodi | Small Kodi plugin against the Jellyseerr API | No Jellyseerr addon exists; needed for the Requested row and for pending-request feedback on the TV | Cut the Trending row if short on time |
| Skin | Arctic Fuse 2 as the base, restyled to **Nuvio's layout** (hero banner, poster rows, one Play button) | Already looks premium; widget-driven home screen; XML so the layout can match Nuvio exactly | Estuary + custom widgets |
| Browse anything | Kodi addon speaking the **Stremio addon protocol** (manifest → catalog/meta/stream JSON); users paste their own addon URLs | Nuvio's browse-and-play-instantly model, through the one player that does HDR + passthrough. No addon URLs shipped. | — |
| Debrid | **Decypharr** | Multi-provider (Real-Debrid, TorBox, AllDebrid…), qBittorrent API so the arrs talk to it natively | Zurg (Real-Debrid only) |
| Usenet streaming | **NzbDAV** or **AltMount** (bake-off in Phase 0) | Both: WebDAV mount + SABnzbd-compatible API, stream before download | SABnzbd fast download, play when done |
| Automation | Sonarr, Radarr, Prowlarr, Recyclarr | Recyclarr applies "best quality" profiles so users never tune them | — |
| Requests | Jellyseerr | The phone-side "I want this" button | — |
| Containers | Podman quadlets in `/usr/share/containers/systemd`, **container images embedded in the OS image** (`podman pull --root /usr/lib/containers/storage` in the Containerfile + `additionalimagestores`) | Only embedded images actually update and roll back with the OS; otherwise Podman pulls on first boot and `AutoUpdate` runs outside the atomic update. Costs several GB of image size. No `AutoUpdate=registry`. | Pull at first boot if embedding proves unworkable (loses the rollback guarantee for services; say so in docs) |
| Updates | `bootc upgrade` timer + **greenboot** health checks | Staged, applied on reboot while idle, auto-rollback if Kodi or Jellyfin fail to come up | — |
| Data backup | Nightly snapshot of `/var/lib/*` (btrfs snapshot timer, or tar to the data disk) | Rollback protects `/usr`, not `/var`; a corrupt Jellyfin DB would wipe watch state | — |
| Remote access | Tailscale (opt-in in wizard) | Jellyfin outside the home with no port forwarding | — |

### Decided 2026-10-27: Kodi is the engine, not Nuvio or Stremio
Nuvio's official desktop has no Linux build; the NuvioLinux fork is a v0.1 alpha rendering mpv through a GL window. Stremio's Linux shell needs X11/Wayland. In both cases the player sits inside a window on a compositor, and no Linux compositor gives a windowed player HDR output or reliable TrueHD passthrough. Only a player that owns the display (Kodi on GBM) reaches the quality bar. What we want from Nuvio is its layout (→ skin) and its browse-anything model (→ Stremio-protocol addon), both of which fit inside Kodi.

### Upstream facts that shaped this (checked 2026-10-27)
- **Real-Debrid** removed a large amount of cached content and added filename filtering in June 2026; many users moved to **TorBox**. That's why the debrid layer is Decypharr (multi-provider), not Zurg (RD-only). The wizard should present TorBox and RD as equals.
- **NzbDAV** is still tagged alpha (image last updated 2026-05-27). **AltMount** is at v0.3.x, with FUSE read-ahead work and Stremio integration. Neither is "done," so usenet streaming has a download fallback.
- **Kodi 22** reached RC in Oct 2026; final not confirmed. Fedora/RPM Fusion packaging of 22 may lag, so a spike decides whether we package it ourselves.

## 4. Hardware

| Tier | Hardware | Notes |
|---|---|---|
| 1 (reference) | Intel N100 / N150 / N305 (Alder/Twin Lake-N), 16 GB RAM, 512 GB NVMe | HEVC 10-bit + AV1 decode, Quick Sync for transcoding to remote clients |
| 2 | AMD Ryzen 7000/8000 mini PCs | Good VA-API; verify HDR + passthrough per model |
| Player mode only | Same, 8 GB RAM | — |

**Hardware gotchas to test per model:**
- **CEC:** most mini PCs have **no HDMI-CEC**. Options: a Pulse-Eight USB-CEC adapter, or bundle/recommend a USB RF or Bluetooth remote. Decide in Phase 0. This affects the "premium" feel more than anything else.
- **HDMI via LSPCON:** many N100 boxes drive one HDMI port through a DP→HDMI converter. HDR and passthrough can fail on that port. The supported-hardware list records which port works.
- **Audio:** verify TrueHD/Atmos and DTS:X bitstream via an AVR on each model.

Start with **two** reference models. Ship a short, honest supported list instead of claiming everything.

## 5. Repo layout

```
Containerfile                 # FROM ublue base-main; install kodi, libcec, rclone; copy files
build_files/
  build.sh                    # dnf installs, enable units
system_files/
  usr/lib/systemd/system/
    kodi.service              # kodi --windowing=gbm as user 'media', on tty1
    hub.target / player.target
    bootc-update.timer
  usr/share/containers/systemd/   # *.container quadlets: jellyfin, decypharr, nzbdav, sonarr, radarr, prowlarr, bazarr, jellyseerr
  usr/lib/containers/storage/     # embedded container images (pulled at image build)
  usr/lib/systemd/system/var-backup.timer
  usr/lib/greenboot/check/required.d/   # kodi + jellyfin health checks
  usr/share/plymouth/themes/<name>/
  usr/share/<name>/kodi-defaults/       # advancedsettings.xml, skin + addon preinstalls
wizard/                        # small web app: QR on TV → phone setup
kodi-addons/plugin.video.requests/   # Requested / Trending rows + pending-request status, via Jellyseerr API
disk_config/iso.toml
.github/workflows/build.yml, build-disk.yml   # from the template
```

## 6. Phases

Each phase ends with an exit test on **real hardware**. VMs only prove the boot flow; HDR, passthrough and CEC need a TV and an AVR.

### Phase 0 — Spikes (1–2 weeks)
Answer the questions that could kill the design before building anything.
1. Kodi GBM on reference box #1: 4K HDR10 remux plays with HDR on the TV, TrueHD/Atmos bitstreams to the AVR, refresh rate switches 23.976 ↔ 60.
2. Kodi 22 availability for Fedora: packaged, or do we build it? Does the RPM Fusion build include GBM windowing at all? (LibreELEC compiles its own.)
3. bootc image from the ublue template boots to Kodi on tty1 with no desktop.
4. NzbDAV vs AltMount: time to first frame and seek time on a 60 GB remux, on the same provider, measured **both** via direct path from the mount and via Jellyfin HTTP. Pick one.
5. Decypharr with TorBox and Real-Debrid: cached-release success rate for 20 titles.
6. Remote: test CEC adapter vs a bundled USB remote. Pick one.
7. Embedded container images: quadlets start from `/usr/lib/containers/storage` via `additionalimagestores` with no network, and survive a `bootc upgrade` + rollback.
8. Sonarr/Radarr per-item Jellyfin import notification works on symlinked FUSE paths with library scanning off.

**Exit:** a written yes/no per spike. Any "no" on 1, 3 or 7 means rethinking the base before going further.

### Phase 1 — The appliance (player)
- Image boots to Kodi, branded Plymouth splash, no visible console.
- Skin + Jellyfin for Kodi preinstalled and pre-configured from `kodi-defaults`.
- First-sync strategy decided here, not later: initial Jellyfin for Kodi sync on a few-thousand-item library takes minutes and leaves the home screen empty. Either sync in the background with a visible progress state, or use JellyCon for the first session. Measure against the "first play < 10 min" target.
- Remote works out of the box.
- `bootc-update.timer` + greenboot: break an update on purpose, confirm automatic rollback.
- ISO from CI, installable onto NVMe.

**Exit:** on both reference models, cold boot < 20 s, a 4K HDR remux plays from an existing Jellyfin server, and a deliberately broken update rolls back without help.

### Phase 2 — The hub
- Quadlets for the whole stack with embedded images, all data under `/var/lib/<name>/`, nightly `/var/lib` backup timer.
- Library layout + symlink paths wired between Decypharr, NzbDAV, the arrs, Bazarr and Jellyfin.
- Jellyfin: library scanning and real-time monitoring **off**; Sonarr/Radarr notify Jellyfin per item on import.
- Recyclarr presets: "Best (remux)", "Balanced (web-dl 4K)", "Bandwidth saver (1080p)".
- Jellyseerr pre-linked to Sonarr/Radarr/Jellyfin.
- Requests plugin for Kodi (v0): a "Requested" row showing each request's state (searching, downloading, ready). Without it the TV is silent until a title appears, which for uncached debrid or a slow usenet import can be a long wait.
- **Browse anything** addon (v0): user pastes Stremio addon manifest URLs in the wizard; a Browse row and search backed by those catalogs; one Play button that picks the best cached stream for the quality preset, "Other versions" as a submenu; metadata and art from a meta addon (Cinemeta) so pages look like library pages; direct play of the stream URL in Kodi. Design rules in EXPERIENCE.md.

**Exit:** request a cached movie on the phone → playing on the TV in < 2 min, through both debrid and usenet, with its status visible on the TV while pending. Browse a title not in the library → playing in < 10 s.

### v1 features, easiest first
Build in this order; each is done when it works from the remote with no setup beyond the wizard.
| # | Feature | How | Phase |
|---|---|---|---|
| 1 | Ambient mode | Kodi screensaver showing library art + clock; config only | 1 |
| 2 | Trakt sync | Kodi Trakt addon preinstalled; user signs in from the status screen | 1 |
| 3 | Phone as keyboard and remote | Kodi web server + Chorus; QR on the status screen; Kore supported | 1 |
| 4 | Up Next + skip intro/credits | Jellyfin Intro Skipper plugin + Kodi Up Next addon; wired by the wizard | 2 |
| 5 | Quality badges on title pages | Skin reads resolution / HDR type / audio codec from the Jellyfin item | 4 (skin) |
| 6 | Subtitles in Browse anything | Stremio subtitle addons (user-added) through the same protocol | 2 (Browse addon) |
| 7 | Second box auto-pairs | Hub advertises over mDNS; Player wizard shows "Join <hub>" | 3 (wizard) |
| 8 | "In your library" badge in Browse | Match IMDb/TMDB id against Jellyfin; hide streams for owned titles | 2 (Browse addon) |
| 9 | "Keep this" on browsed titles | Button → Jellyseerr request; title moves from Browse to Requested row | 2 (Browse addon) |

### Later (each a real piece of work)
Live TV & sports (PVR IPTV Simple, user's own M3U/EPG) · Spotify Connect (librespot container; audio handoff with Kodi is the hard bit) · Profiles with parental PIN (Jellyfin users + Kodi profiles) · Trailers (TMDB → YouTube id; YouTube playback in Kodi is fragile) · USB drive auto-import (udev + Radarr manual import) · Config export/import from the wizard · Provider bandwidth meter on the status screen.

Left out on purpose: watch-together / multi-room sync (lots of code, few users); Dolby Vision output (DV profile 8 plays as HDR10 on Intel; true DV needs licensing we can't get).

### Phase 3 — The setup wizard
The only meaningful custom code in the project.
- First boot: Kodi shows a QR code + `http://<name>.local`.
- Phone flow: mode (Hub / Player) → Jellyfin server (Player) or admin account (Hub) → debrid provider + key → usenet provider + indexers (into Prowlarr) → quality preset → optional Tailscale.
- Reads the API keys each app generates on first start from its config file; writes all settings through the app's API. No templating of config files beyond that.
- Rerunnable from a Kodi menu item.

**Exit:** a non-technical tester gets from fresh box to first play in < 10 min with no keyboard.

### Phase 4 — Polish
- Branded skin fork: home rows for Continue Watching, New, Requested, Trending. Requested and Trending come from the requests plugin (Jellyseerr API), not skin config; Trending is the first thing to cut if time is short.
- Power: HDMI standby on idle, wake on remote, TV on/off via CEC where present.
- Status screen in Kodi: storage, service health, provider status, update state, in plain language.
- Audio/video setup screen: detect AVR capabilities and set passthrough options once.

### Phase 5 — Release
- Signed images (cosign), stable + testing update channels.
- Website: supported hardware list, install guide, FAQ.
- Bug report button in Kodi that collects logs from `journalctl` into one shareable file.

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Kodi 22 not packaged for Fedora | Lose the new GBM/HDR stack | Phase 0 spike; package it ourselves or start on 21 |
| Debrid provider crackdowns (RD June 2026) | Fewer cached releases | Multi-provider via Decypharr; usenet as second source |
| Usenet streaming tools are alpha | Stalls, failed seeks | Download fallback via SABnzbd API path; pick the winner in Phase 0 |
| No CEC on mini PCs | Clunky first impression | Decide on adapter or bundled remote in Phase 0 |
| HDR/passthrough varies per box and port | "It doesn't work on my TV" | Small supported-hardware list, tested per port |
| Skin breaks on Kodi updates | Ugly home screen after update | Pin skin version in the image; update together with Kodi |
| Legal exposure | Project takedown | No sources shipped, ever (section 1) |
| Jellyfin probes on FUSE mounts download from providers | Bandwidth, rate limits, bans | Scanning off; per-item import notifications from the arrs (Phase 0 item 8, Phase 2) |
| Embedded container images don't work with quadlets / bootc | Services update outside the atomic update; first boot needs internet | Phase 0 item 7; fallback is first-boot pull with the guarantee dropped from docs |
| Initial Kodi library sync is slow | Empty home screen on first boot | Background sync with progress, or JellyCon first session (Phase 1) |
| `/var` data loss | Watch state and settings gone; rollback doesn't help | Nightly `/var/lib` backup |

## 8. Open decisions
1. Name.
2. Remote: CEC adapter vs bundled USB/Bluetooth remote (Phase 0, item 6).
3. NzbDAV vs AltMount (Phase 0, item 4).
4. Wizard language. Suggest Python stdlib or Go single binary; no framework.
5. ~~Do we ever offer on-demand debrid browsing in Kodi?~~ Decided: yes, "Browse anything" in Phase 2 via the Stremio addon protocol, user-supplied addon URLs only.
