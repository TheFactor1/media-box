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
 phone ──► Setup wizard (web)
             │
┌────────────▼───────────────────── mini PC ──────────────────────────────┐
│ PLAYER                                                                   │
│  Kodi 22 (GBM, no compositor) ─ skin ─ Jellyfin for Kodi addon           │
│   ├─ Browse addon (Stremio protocol) ──────────────┐                     │
│   └─ plays .strm from the library ──► resolver ────┤                     │
│  ALSA direct (passthrough) · libcec / USB remote    │                     │
│                                                     ▼                     │
│ HUB (Podman quadlets, bootc bound images)      AIOStreams (user's own:   │
│  Jellyfin: /media/library of .strm + .nfo      self-hosted or hosted)    │
│  resolver: tiny HTTP service, "Keep this" →     ├─ TorBox / debrid       │
│  writes .strm; on play asks AIOStreams for      └─ native usenet         │
│  the best stream right now                                               │
│  [later, "real files" tier: SABnzbd download for "Keep offline"]         │
│ BASE: Fedora bootc · atomic updates · rollback                           │
└──────────────────────────────────────────────────────────────────────────┘
```

The idea that holds it together: **AIOStreams answers "best playable stream for this title, right now"** for both debrid (TorBox and friends) and native usenet, with the user's own filters and health routing. The box never mounts, downloads or indexes anything in v1:

- **Browse** asks AIOStreams for catalogs and streams and plays the URL in Kodi.
- **Keep this** writes a `.strm` (one line: a resolver URL for that IMDb id) plus a `.nfo` into `/media/library`. Jellyfin indexes it in seconds; no bytes move. **Jellyfin stays the single source of truth** for the library, metadata, watch state and the Kodi widgets.
- **Play from the library:** Kodi opens the `.strm`, the resolver asks AIOStreams for today's best stream and redirects, Kodi direct-plays it with HDR and passthrough exactly as it would a file.

What this costs: no permanent copies. If a provider drops a file, the next Play finds another. A later **"real files" tier** ("Keep offline": SABnzbd download to `/media/downloads`) covers the things someone wants forever; the arr/Decypharr/NzbDAV stack from the first draft lives in git history if that tier ever needs mounts.

**Jellyfin and `.strm`:** Jellyfin never probes `.strm` contents at scan time, so library scans are free. Media-info badges (resolution, HDR, audio) come from the resolver's chosen stream (AIOStreams reports them) and are written into the `.nfo`, not probed.

### Component picks
| Layer | Pick | Why | Fallback |
|---|---|---|---|
| Base OS | `ghcr.io/ublue-os/base-main` via [ublue image-template](https://github.com/ublue-os/image-template) | Fedora bootc with codecs and Intel media driver already in; CI + ISO workflow included | `quay.io/fedora/fedora-bootc` + RPM Fusion |
| ISO build | bootc-image-builder (in the template) | Works today | Move to `bootc-installer` / image-builder-cli; osbuild is deprecating the old `iso` type |
| Player | Kodi 22 "Piers" on GBM | 22 rewrote GBM/DRM rendering and added end-to-end HDR (RC as of Oct 2026) | Kodi 21 GBM until 22 is packaged |
| Audio | ALSA direct from Kodi | Most reliable bitstream passthrough; PipeWire not involved in playback | — |
| Jellyfin in Kodi | **Jellyfin for Kodi** (DB sync; direct paths in Hub mode) | Syncs into Kodi's database, so skin widgets (continue watching, new episodes) work natively | JellyCon if sync proves slow on big libraries |
| Subtitles | Stremio subtitle addons through AIOStreams (user-added, e.g. OpenSubtitles) | Same protocol as streams; Kodi loads them per play | Bazarr in the "real files" tier |
| Skin | Arctic Fuse 2 as the base, restyled to **Nuvio's layout** (hero banner, poster rows, one Play button) | Already looks premium; widget-driven home screen; XML so the layout can match Nuvio exactly | Estuary + custom widgets |
| Browse anything | Kodi addon speaking the **Stremio addon protocol** (manifest → catalog/meta/stream JSON); users paste their own addon URLs | Nuvio's browse-and-play-instantly model, through the one player that does HDR + passthrough. No addon URLs shipped. | — |
| Streams (debrid + usenet) | **AIOStreams** (the user's instance; Matt's runs on the EliteMini with TorBox + native usenet) | One endpoint ranks every source with the user's own filters; usenet streaming is built in, so no NzbDAV/AltMount mount and no arr stack on the box | Decypharr + NzbDAV + arrs (first draft, in git history) for a "real files" tier |
| Library entries | `.strm` + `.nfo` files written by the **resolver** (small HTTP service on the box) | Jellyfin and Kodi both treat `.strm` natively; "Keep this" is instant and storage-free | — |
| Requests from the phone | The wizard's web app gets a search box that calls "Keep this" | Jellyseerr is overkill once requests are instant; one search page on the box replaces it | Jellyseerr if the "real files" tier needs queued downloads |
| Containers | Podman quadlets in `/usr/share/containers/systemd`, registered as **bootc logically bound images** (symlinks in `/usr/lib/bootc/bound-images.d/`) | bootc pulls them into `/usr/lib/bootc/storage` as part of `bootc upgrade`, keeps the rollback deployment's copies and GCs the rest, so services update and roll back with the OS. No `AutoUpdate=registry`. Copying images into the OS image at build time failed (lchown in the build's user namespace), and LBIs don't bloat the image. Anaconda doesn't pull LBIs at install, so a fresh install pulls once on first boot (`Pull=missing`). | — |
| Updates | `bootc upgrade` timer + **greenboot** health checks | Staged, applied on reboot while idle, auto-rollback if Kodi or Jellyfin fail to come up | — |
| Data backup | Nightly snapshot of `/var/lib/*` (btrfs snapshot timer, or tar to the data disk) | Rollback protects `/usr`, not `/var`; a corrupt Jellyfin DB would wipe watch state | — |
| Remote access | Tailscale (opt-in in wizard) | Jellyfin outside the home with no port forwarding | — |

### Decided 2026-10-27: Kodi is the engine, not Nuvio or Stremio
Nuvio's official desktop has no Linux build; the NuvioLinux fork is a v0.1 alpha rendering mpv through a GL window. Stremio's Linux shell needs X11/Wayland. In both cases the player sits inside a window on a compositor, and no Linux compositor gives a windowed player HDR output or reliable TrueHD passthrough. Only a player that owns the display (Kodi on GBM) reaches the quality bar. What we want from Nuvio is its layout (→ skin) and its browse-anything model (→ Stremio-protocol addon), both of which fit inside Kodi.

### Existing home server (EliteMini) services, sorted (from `docker ps`, 2026-10-27)
| Bucket | Services | Why |
|---|---|---|
| **Box talks to it, stays on the EliteMini** | aiostreams, aiometadata (+redis), comet (+postgres), stremthru, mdblist-bridge | This is the box's whole streaming backend: Browse *and* library playback resolve through AIOStreams (TorBox + native usenet + health routing). Ship them as an optional "self-hosted streams" hub module later, for users without a server. |
| **"Real files" tier, later** | prowlarr, sabnzbd, flaresolverr | Only needed for "Keep offline" downloads. Not in v1. |
| **Candidate hub module, later** | audiobookshelf | Audiobooks and podcasts on the TV ("Listen" row); Jellyfin does this badly. |
| **Stays on the server, not a TV-box concern** | cwa, shelfmark (+ai/telegram/pairing relays, update, annas-archive-api), audiobooks-green-proxy, audiobooks-sync-guard, storyteller, ollama, cloudflared, filebrowser | Books, LLM, tunnel, file UI. The box reaches them over the LAN or tunnel if it ever needs to. |
| **Replaced by the status screen / not for an appliance** | portainer, dozzle, beszel (+agent), uptime-kuma, scrutiny, watchtower-heartbeat | Ops tooling. beszel-agent and scrutiny's SMART data could feed the status screen later; the rest need docker.sock and have no place on a TV box. |

Remote access: the server uses cloudflared (`*.mattshosted.com`); the box plan says Tailscale. Pick one per household in the wizard, not both.

### Upstream facts that shaped this (checked 2026-10-27)
- **Real-Debrid** removed a large amount of cached content and added filename filtering in June 2026; many users moved to **TorBox**. AIOStreams is provider-agnostic, so the box doesn't care which.
- **NzbDAV** is still tagged alpha; **AltMount** is at v0.3.x. Both would have been needed for a mount-based usenet path; AIOStreams' native usenet makes them unnecessary in v1. How AIOStreams serves usenet bytes (proxied through the host it runs on, most likely) is Phase 0 spike 4.
- **Kodi 22** reached RC in Oct 2026; final not confirmed. Fedora/RPM Fusion packaging of 22 may lag, so a spike decides whether we package it ourselves.

## 4. Hardware

Full research with sources in [docs/HARDWARE.md](docs/HARDWARE.md) (2026-10-27).

| Tier | Hardware | Notes |
|---|---|---|
| 1 (reference) | **Beelink EQ14** (N150, 16 GB, 2× native HDMI 2.0; the box LibreELEC's lead tests on). Runner-up ASUS NUC 14 Essential N150; floor GMKtec G3 Plus (~$150) | N100 and N150 share the same 24-EU Gen12 iGPU: HEVC/VP9/AV1 10-bit decode. N305/N355 only buys Jellyfin transcode headroom. Idle is 10–14 W at the wall, not the 6 W folklore. |
| 2 ("server too") | Intel N355 or Core Ultra, **after a bench test**. AMD (780M-class) demoted: wrong HDR colours on LibreELEC 12/13 (Apr 2025, unfixed) and the AMD TrueHD passthrough bug reopened Sep 2026 | Core Ultra: HDR correct but 8-bit only + AVR resync loss. The EliteMini (AMD) must pass spike 1 before it becomes the combined box. |
| Player mode | Any tier-1 box, 8 GB | Kodi needs ~2 GB. Hub mode wants 16 GB: Jellyfin idles at ~1 GB and transcode buffers are RAM. Plan 1–2 phone transcodes on an N150, not the vendor's 3–4. |

**Known blockers, per model, to test in Phase 0:**
- **Intel Gen12+ drops HD audio at 2160p/23.976 Hz** (TrueHD/DTS-HD), which is our hero use case. Open since Dec 2023, no upstream fix as of Jan 2026. LibreELEC 13 carries a kernel patch capping HDMI to 10-bit, which fixes it on the EQ14; a runtime `max bpc` connector-property script is the alternative. **The image must carry one of them** (spike 9) or tier 1 fails its own target.
- HDR start/stop black screens and Denon AVR resync issues persist on LE13 nightlies (Aug 2026).
- **HDMI:** Alder/Twin Lake-N has native HDMI from the SoC, so LSPCON is not the N-series problem; the trap is a DP→HDMI converter sold as "HDMI 2" (no BT.2020, no bitstream). Check with `edid-decode` and the connector name. No candidate has HDMI 2.1 FRL, so 4K60 HDR is 4:2:0 everywhere; 4K24 is the real target.
- **CEC:** no cheap box has it. The Pulse-Eight adapter can't pass 4K HDR, so it can't sit in the video path. Recommend **Flirc (~$20) or the OSMC RF remote (~£19, wakes from S5)**; CEC only as a spare-input trick.

Start with the EQ14. Ship a short, honest supported list instead of claiming everything.

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
  usr/share/containers/systemd/   # *.container quadlets: jellyfin, resolver
  usr/lib/bootc/bound-images.d/   # symlinks so bootc keeps the quadlets' images with the OS
  usr/lib/systemd/system/var-backup.timer
  usr/lib/greenboot/check/required.d/   # kodi + jellyfin health checks
  usr/share/plymouth/themes/<name>/
  usr/share/<name>/kodi-defaults/       # advancedsettings.xml, skin + addon preinstalls
wizard/                        # small web app: QR on TV → phone setup
kodi-addons/plugin.video.browse/     # Browse anything: Stremio-protocol client, Keep this, In your library
resolver/                            # .strm target: asks AIOStreams for the best stream, redirects; writes .strm/.nfo
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
4. AIOStreams streams in Kodi: time to first frame and seek time on a 4K remux for (a) a TorBox cached stream and (b) a native usenet stream, played as a direct URL. Does HDR and passthrough behave exactly as for a file? Where do the usenet bytes flow (through the EliteMini?) and what does that cost on the LAN?
5. `.strm` round trip: resolver writes `.strm` + `.nfo` → Jellyfin indexes it with the right metadata and badges → Jellyfin for Kodi shows it → Kodi plays it through the resolver redirect, with resume and watched-state working.
6. Remote: test CEC adapter vs a bundled USB remote. Pick one.
7. Bound images: after the first `bootc upgrade`, the quadlets' images are in `/usr/lib/bootc/storage`, start with no network, and survive a rollback.
8. Success rate: for 20 titles across quality presets, does AIOStreams return a playable stream, and how often is the first choice the one that plays?
9. **HD audio at 4K23.976 on Intel Gen12:** reproduce the TrueHD/DTS-HD drop on the stock Fedora kernel, then fix it with either LibreELEC's 10-bit HDMI cap patch (means building our own kernel package into the image) or a boot-time `max bpc=10` DRM connector property. Pick the one that works; the runtime script wins if it does.

**Exit:** a written yes/no per spike. Any "no" on 1, 3, 7 or 9 means rethinking the base before going further.

### Phase 1 — The appliance (player)
- Image boots to Kodi, branded Plymouth splash, no visible console.
- Skin + Jellyfin for Kodi preinstalled and pre-configured from `kodi-defaults`.
- First-sync strategy decided here, not later: initial Jellyfin for Kodi sync on a few-thousand-item library takes minutes and leaves the home screen empty. Either sync in the background with a visible progress state, or use JellyCon for the first session. Measure against the "first play < 10 min" target.
- Remote works out of the box.
- `bootc-update.timer` + greenboot: break an update on purpose, confirm automatic rollback.
- ISO from CI, installable onto NVMe.

**Exit:** on both reference models, cold boot < 20 s, a 4K HDR remux plays from an existing Jellyfin server, and a deliberately broken update rolls back without help.

### Phase 2 — The hub
- Quadlets: Jellyfin and the resolver, both bound images; data under `/var/lib/<name>/`; nightly `/var/lib` backup timer.
- **Resolver** (v0, a few hundred lines, Python stdlib or Go): `GET /play/<imdb id>[/<season>/<episode>]` → asks the configured AIOStreams manifest for streams, picks the best match for the quality preset, 302-redirects Kodi to it; `POST /keep/<imdb id>` → writes `/media/library/<type>/<Title (Year)>/<file>.strm` + `.nfo` with metadata from the meta addon (Cinemeta) and the badges of the chosen stream, then triggers a Jellyfin refresh for that path. Preset mapping: Best → prefer remux/4K/HDR, Balanced → 4K web, Bandwidth saver → 1080p.
- Jellyfin: libraries on `/media/library`, `.strm` enabled, real-time monitoring on (cheap for `.strm`).
- **Browse anything** addon (v0): user pastes the AIOStreams manifest URL (or any Stremio addon URLs) in the wizard; a Browse row and search backed by those catalogs; one Play button that plays the resolver's pick, "Other versions" as a submenu of AIOStreams' list; metadata and art from Cinemeta so pages look like library pages; "Keep this" calls the resolver; "In your library" by IMDb id against Jellyfin. Design rules in EXPERIENCE.md.
- Phone: a search page on the wizard's web app with a "Keep this" button, so requests work from anywhere without the TV.

**Exit:** Keep this on the phone → title in the library on the TV in < 30 s → playing in < 10 s, for both a debrid and a usenet-backed title. Browse a title not in the library → playing in < 10 s.

### v1 features, easiest first
Build in this order; each is done when it works from the remote with no setup beyond the wizard.
| # | Feature | How | Phase |
|---|---|---|---|
| 1 | Ambient mode | Kodi screensaver showing library art + clock; config only | 1 |
| 2 | Trakt sync | Kodi Trakt addon preinstalled; user signs in from the status screen | 1 |
| 3 | Phone as keyboard and remote | Kodi web server + Chorus; QR on the status screen; Kore supported | 1 |
| 4 | Up Next | Kodi Up Next addon; wired by the wizard. (Intro Skipper needs real files to analyse; "real files" tier) | 2 |
| 5 | Quality badges on title pages | Skin reads resolution / HDR type / audio codec from the Jellyfin item | 4 (skin) |
| 6 | Subtitles in Browse anything | Stremio subtitle addons (user-added) through the same protocol | 2 (Browse addon) |
| 7 | Second box auto-pairs | Hub advertises over mDNS; Player wizard shows "Join <hub>" | 3 (wizard) |
| 8 | "In your library" badge in Browse | Match IMDb/TMDB id against Jellyfin; hide streams for owned titles | 2 (Browse addon) |
| 9 | "Keep this" on browsed titles | Button → resolver writes `.strm`; title appears in Recently Added within seconds | 2 (Browse addon) |

### Later (each a real piece of work)
**"Real files" tier**: "Keep offline" downloads via SABnzbd (+ Prowlarr, user's indexers) to `/media/downloads`, with a Requested row showing Downloading → Ready, Bazarr subtitles and Intro Skipper for those files · Live TV & sports (PVR IPTV Simple, user's own M3U/EPG) · Spotify Connect (librespot container; audio handoff with Kodi is the hard bit) · Profiles with parental PIN (Jellyfin users + Kodi profiles) · Trailers (TMDB → YouTube id; YouTube playback in Kodi is fragile) · USB drive auto-import · Config export/import from the wizard · Provider bandwidth meter on the status screen · Self-hosted AIOStreams/StremThru as an optional hub module for users without a server.

Left out on purpose: watch-together / multi-room sync (lots of code, few users); Dolby Vision output (DV profile 8 plays as HDR10 on Intel; true DV needs licensing we can't get).

### Phase 3 — The setup wizard
The only meaningful custom code in the project.
- First boot: Kodi shows a QR code + `http://<name>.local`.
- Phone flow: mode (Hub / Player) → Jellyfin server (Player) or admin account (Hub) → AIOStreams manifest URL (or other Stremio addon URLs) with a "Test" that lists a few streams → quality preset → optional Tailscale / Cloudflare tunnel.
- Reads the API key Jellyfin generates on first start from its config; writes all settings through APIs. The resolver's config is one file the wizard owns.
- Rerunnable from a Kodi menu item.

**Exit:** a non-technical tester gets from fresh box to first play in < 10 min with no keyboard.

### Phase 4 — Polish
- Branded skin fork (Cinema look from the prototype): home rows for Continue Watching, Next Up, Recently Added, Browse. Browse rows come from the Browse addon, not skin config.
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
| Debrid provider crackdowns (RD June 2026) | Fewer cached releases | AIOStreams is provider-agnostic; usenet as second source |
| Library depends on AIOStreams being up | Nothing in the library plays if the server (or hosted instance) is down | Resolver caches the last good stream URL per title for a short time; status screen says "Streams: unreachable"; "real files" tier for anything that must always play |
| AIOStreams usenet bytes route through the host it runs on | Server upload path becomes the bottleneck away from home | Spike 4 measures it; LAN is unaffected |
| No CEC on mini PCs | Clunky first impression | Flirc or OSMC RF remote (Phase 0 item 6); CEC adapters can't carry 4K HDR |
| Intel drops TrueHD/DTS-HD at 4K23.976 | The hero use case has no HD audio | Spike 9: 10-bit HDMI cap via kernel patch or runtime DRM property; the fixes live in LibreELEC's patch tree, so plan to cherry-pick |
| AMD HDR/passthrough bugs | The EliteMini can't be the combined box yet | Tier 2 is Intel until AMD passes spike 1 |
| HDR/passthrough varies per box and port | "It doesn't work on my TV" | Small supported-hardware list, tested per port |
| Skin breaks on Kodi updates | Ugly home screen after update | Pin skin version in the image; update together with Kodi |
| Legal exposure | Project takedown | No sources shipped, ever (section 1) |
| Bound images need a first `bootc upgrade` before they're OS-managed | First boot pulls over the network | Documented; Phase 0 item 7 |
| Initial Kodi library sync is slow | Empty home screen on first boot | Background sync with progress, or JellyCon first session (Phase 1) |
| `/var` data loss | Watch state and settings gone; rollback doesn't help | Nightly `/var/lib` backup |

## 8. Open decisions
1. Name.
2. Remote: Flirc vs OSMC RF remote (Phase 0, item 6). CEC adapters ruled out for the video path by the research.
3. ~~NzbDAV vs AltMount.~~ Neither in v1: AIOStreams' native usenet (decided 2026-10-27).
4. Wizard and resolver language. Suggest Python stdlib or Go single binary; no framework. Same language for both.
5. ~~Do we ever offer on-demand debrid browsing in Kodi?~~ Decided: yes, "Browse anything" in Phase 2 via the Stremio addon protocol, user-supplied addon URLs only.
6. ~~Mount-based library (arrs + Decypharr + NzbDAV) or stream-resolved library?~~ Decided 2026-10-27: stream-resolved via AIOStreams and `.strm`; mounts and arrs become the later "real files" tier.
