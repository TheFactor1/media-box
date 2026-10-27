# What the user sees

Companion to [PLAN.md](PLAN.md). This is the product from the couch. Anything in the code that contradicts this document is a bug in the code.

## Five rules
1. **Never a terminal, never a keyboard.** Everything on the TV works with six remote buttons. Anything that needs typing happens on the phone.
2. **Silence is a bug.** If the box is doing something the user asked for, the TV shows its state. No request ever just disappears.
3. **It never breaks.** Updates roll back on their own. The box boots into Kodi or into a plain-language error screen with a QR code, never into a login prompt.
4. **Setup once.** Sound, display and accounts are set once in the wizard and never asked again unless the user opens settings.
5. **No sources shipped.** The box asks for the user's own accounts and indexers. It never suggests any.

## Remote
Six buttons are required: **up/down/left/right, OK, Back**. Three more are used when present: **Play/Pause, Home, Power**. Long-press OK = context menu. Everything else in Kodi's default keymap is unmapped so a stray button never lands the user in a settings screen.

Power button: screen to standby (HDMI off), box stays up. Hold 3 s: shutdown menu.

## Out of the box
| Step | On the TV | On the phone |
|---|---|---|
| 1 | Branded splash, no text, no boot log | — |
| 2 | "Set up this box" screen: a QR code, the address `http://mediabox.local`, and a 6-digit pairing code | Scan QR → wizard opens, asks for the code |
| 3 | Progress panel mirroring the phone: "Sound: Receiver ✓ · Accounts: 2 of 3 · Library: syncing 412/2,100" | Wizard steps (below) |
| 4 | Home screen | "Done. Grab the remote." |

Target: step 1 to step 4 in under 10 minutes for someone who has their account details to hand.

### Wizard steps (phone)
1. **Mode:** "This box is my media server" (Hub) / "I already run Jellyfin" (Player: enter server address, sign in; skip to 6).
2. **Picture & sound:** "What is the box plugged into?" TV only / Soundbar / AV receiver. Sets passthrough formats. "Match the film's frame rate?" default on. This is the only place these are asked.
3. **Your account:** name + password for Jellyfin and Jellyseerr (same credentials, created in both).
4. **Where films come from** (any, all, or none; each card is optional and can be added later):
   - Debrid: pick TorBox / Real-Debrid / AllDebrid, paste API key, "Test" button.
   - Usenet: provider host, port, SSL, username, password, connections. "Test" button.
   - Indexers: Prowlarr's own add-indexer UI embedded; the wizard adds nothing on its own.
5. **Quality:** Best (remux, needs fast internet) / Balanced (4K web) / Bandwidth saver (1080p). One sentence each. Default Balanced.
6. **Away from home:** "Watch outside the house?" Enables Tailscale, shows the login link. Default off.
7. **Updates:** "Install updates overnight at 4 am" default on; "Tell me on the TV when it happens" default on.

Every step is skippable. Skipped steps appear as a card on the status screen until done.

## Home screen
Rows, top to bottom. Each is a Kodi widget fed by Jellyfin for Kodi or the requests plugin.

| Row | Source | Empty state |
|---|---|---|
| Continue Watching | Jellyfin resume points | hidden |
| Requested | requests plugin (see states below) | hidden |
| Next Up | Jellyfin next episodes | hidden |
| Recently Added | Jellyfin | "Nothing yet. Ask for something on your phone." with the QR |
| Movies / Shows | Jellyfin libraries | — |
| Trending *(Phase 4, cut first)* | Jellyseerr discover | hidden |

Top-right: one small status dot. Green = all fine. Amber = something needs attention (pressing it opens the status screen). Never red on the home screen; failures live on the status screen with words, not colours.

## Asking for something
Phone: Jellyseerr. Search, tap, done. No approval step for the admin.

TV, Requested row, one card per request:

| State | Card text | Triggered by |
|---|---|---|
| Looking | "Looking for a good copy…" | Request created, no grab yet |
| Fetching (debrid) | "Fetching · ready in moments" | Decypharr reports cached |
| Downloading | "Downloading · 43 %" | NzbDAV / Decypharr progress |
| Ready | Normal poster, "New" badge; card leaves the row on first play | Jellyfin has the item |
| Stuck | "Couldn't find a good copy. Try a lower quality?" with OK = retry at next profile down | No grab within 15 min, or all grabs failed |

Timing target for a cached debrid or usenet-stream title: Looking → Ready in under 2 minutes, and the card changes state within 10 s of the backend.

## Playback
- Direct play always for the TV. No transcoding on the player path. HDR10/HLG passed through; TV switches mode; refresh rate matches the file.
- Subtitles: off by default for the user's language, forced subs on, foreign-audio-forced handled by Bazarr's defaults. One setting in the status screen: "Subtitles: Off / On / Only when needed".
- OSD: Kodi's, restyled by the skin. Seek with left/right (10 s / 30 s), up/down for chapters.
- Stream stalls (FUSE read slow): spinner for up to 8 s, then "Buffering from your provider…" text. After 30 s: "This copy is slow. Try another?" → re-request.

## Status screen ("About this box")
Reachable from the home menu and the status dot.
- **Services:** Jellyfin, requests, debrid, usenet, indexers, remote access; each "Working" / "Not set up" (→ phone QR) / "Problem: <one sentence>".
- **Storage:** used / free on the data disk, in GB, plus "Library is on your providers, not this disk" when applicable.
- **Updates:** channel, current version, last update date, "Check now".
- **Report a problem:** collects `journalctl -b`, service logs and the Kodi log into one file, shows a QR to download it from the phone. Nothing leaves the box unless the user sends it.
- **Set up again:** reopens the wizard pairing screen.

## Updates
Downloaded in the background, applied on the next reboot in the overnight window if the box is idle (nothing playing, no download running). Toast on the next home screen: "Updated overnight." If greenboot rolls back: toast "An update didn't work and was undone. Nothing to do." and a card on the status screen with the version that failed.

## When things go wrong
| Situation | What the user sees |
|---|---|
| Kodi crashes | Restarts in 2 s; home screen returns. Three crashes in 5 min → error screen with QR |
| No network at boot | Home screen loads (library is cached); status dot amber; "No internet" on the status screen |
| Provider key expired | Request cards go "Stuck"; status screen: "Debrid: key not accepted. Fix on your phone" + QR |
| Data disk full | Status amber; downloads pause; "Storage full" with the biggest items listed |
| Update broke boot | Automatic rollback (see Updates) |
| Everything broke | Error screen: plain sentence, QR to the logs, "Hold power to restart" |

## Player mode differences
No Requested row unless a Jellyseerr address is given in the wizard. No storage or provider cards. Everything else identical.
