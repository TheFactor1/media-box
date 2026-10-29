# Own UI instead of Kodi — research (2026-10-29)

Question: can the Fedora 44 bootc box (N150 / Gen12 iGPU, HDMI 2.0 → 4K HDR TV, AVR) drop Kodi for our own web UI (`docs/prototype.html`) plus mpv, and keep HDR10, HD-audio bitstream, refresh matching and remote/CEC?

Evidence tags: **[V]** vendor/project docs · **[C]** merged code (read at master, 2026-10-29) · **[A]** forum/issue anecdote · **[I]** my inference, not tested. Fedora versions are from mdapi/Bodhi, 2026-10-29.

## Verdict

**Yes, but not with gamescope as the session.** The pattern that holds up is the one Kodi-on-GBM already uses: **the player owns the display**. The web UI runs SDR in a small kiosk compositor (cage + Chromium). On play, it hands the screen to **mpv on DRM/KMS** (`--gpu-context=drm`), which sets the HDR metadata, the 10-bit format and the exact refresh mode, and sends bitstream audio straight to ALSA. On exit, the UI comes back. The cost: the in-player OSD is drawn by mpv (Lua/ASS), not HTML, and there is a black HDMI resync at every start and stop. Kodi has that resync today, and it happens anyway on any HDR or refresh switch.

| Feature | gamescope session (proposed) | cage UI + mpv on DRM (recommended) | Evidence |
|---|---|---|---|
| HDR10 PQ/BT.2020 + static metadata | **Caveat → likely broken on Intel.** HDR10 output only needs the connector's `Colorspace` + `HDR_OUTPUT_METADATA` (i915 has both), but gamescope's colour pipeline (`drm_supports_color_mgmt`) needs AMD-only plane properties, so on Intel all HDR is shader-composited. No Intel HDR report found; HDR regressions are open (#2018, Nov 2025; #2524, Oct 2026) | **Works with caveat.** mpv's DRM HDR metadata was merged Nov 2024 (0.40). Fixes landed Feb–Oct 2026 and are **only in master**, not in Fedora's 0.41.0. No TV test report found | [C] gamescope, [C] mpv, [A] gamescope issues |
| 10-bit to the TV | gamescope picks an `XRGB2101010` primary plane, so 10-bit composition survives. Direct scanout of HDR on Intel is unlikely (no plane colour ops) | `--drm-format=xrgb2101010` (the EGL driver must support it; manual warning) | [C], [V] mpv vo.rst |
| TrueHD/Atmos, DTS-HD MA bitstream | Independent of the compositor | **Works:** ALSA direct (`--audio-spdif=…`). PipeWire HBR is **unreliable**: a 2026 report on PipeWire 1.6.4 had TrueHD/DTS-HD fail through PipeWire in both mpv and Kodi, while ALSA worked | [V] mpv, [A] Kodi PR #28116 |
| Intel 2160p23.976 HD-audio dropouts | Unaffected by the player choice: it is the HDMI link. **We still need the 10-bit cap kernel patch** (HARDWARE.md §6) | Same | [A] LibreELEC |
| Refresh match 23.976/24/25/50/59.94 | **Blocked.** `drm_set_refresh(int)` takes whole Hz and `find_mode` matches the integer `vrefresh`, so 23.976 can't be told apart from 24. External-display mode switching is off by default (`drm_allow_dynamic_modes_for_external_display=false`) and is driven by Steam's `GAMESCOPE_DYNAMIC_REFRESH_EXTERNAL` atom | **Works.** `--drm-mode=3840x2160@23.976` is parsed as a float and matched against the EDID modes | [C] both |
| Kiosk UI | Works (Chromium via XWayland or `--expose-wayland`) | **Works:** `cage -s -- chromium --kiosk --ozone-platform=wayland` | [V] cage, [I] performance |
| Remote (Flirc / OSMC RF) | Keys arrive via libinput | UI: libinput through cage. mpv on a VT gets only tty keys; **media keys need an evdev → mpv-IPC bridge** (small daemon) | [I] |
| CEC | n/a | Pulse-Eight: in-kernel `pulse8-cec` + `inputattach` → `/dev/cecX` plus an rc-cec input device (keys as evdev, no libcec daemon needed). The adapter can't carry 4K HDR video (HARDWARE.md §3), so it has to hang off a spare TV input | [C] kernel, [V] Pulse-Eight |
| Subs PGS/ASS, tracks, chapters, resume | mpv native | mpv native; resume through hub `/resume` over JSON IPC | [V] mpv |

## Findings per question

**1. HDR10.** gamescope's flags (master `src/main.cpp`): `--hdr-enabled`, `--hdr-sdr-content-nits`, `--hdr-itm-enabled`, `--hdr-itm-sdr-nits`, `--hdr-itm-target-nits`, `--hdr-debug-force-output`, `--sdr-gamut-wideness`, `--disable-color-management`. Inside gamescope, clients get HDR **only through its Vulkan WSI layer** (`ENABLE_GAMESCOPE_WSI=1`, private `gamescope-swapchain` protocol). gamescope uses `wp_color_management_v1` and frog only as a *nested client* (WaylandBackend), not as a server. So mpv needs `--gpu-api=vulkan --target-colorspace-hint=yes`. mpv documents the hint as supported on "Wayland, D3D11 and winvk" contexts, and its gamescope recipe dates from 2023 ([mpv #13070](https://github.com/mpv-player/mpv/issues/13070), Dec 2023) [A]. Upstream gamescope is 3.17.0; Fedora 44 ships 3.16.29. Recent issues: [#2018](https://github.com/ValveSoftware/gamescope/issues/2018) (3.16.17 broke HDR on Fedora 43, 2025-11-20, open), [#2524](https://github.com/ValveSoftware/gamescope/issues/2524) (HDR clipped to SDR on 3.17.0, 2026-10-28, open) [A]. Code: [DRMBackend.cpp](https://github.com/ValveSoftware/gamescope/blob/master/src/Backends/DRMBackend.cpp) `SupportsHDR10()` and `drm_supports_color_mgmt()` [C].
mpv on DRM: [PR #15106](https://github.com/mpv-player/mpv/pull/15106) "context_drm_gl: add support for hdr metadata", merged 2024-11-04. The author had no HDR display ("somebody please actually test"). Later fixes in `video/out/opengl/context_drm_egl.c`: `baad29c1f` set_color before the first commit (2026-02-28), `c55bdc328` (2026-03-02), `1eb51ef98` and `7b3a22a15` keep allow_modeset until the colour commit (2026-10-09). All of them are after [v0.41.0](https://github.com/mpv-player/mpv/releases/tag/v0.41.0) (2025-12-21), so **we ship mpv master or 0.42** [C]. `drm_common.c` writes `Colorspace=BT2020_RGB` and an `HDR_OUTPUT_METADATA` blob only if the EDID advertises PQ/BT.2020 [C]. A maintainer says the metadata path can't work under Vulkan (vo `displayvk`), so use the OpenGL/EGL DRM context ([PR #10762 thread](https://github.com/mpv-player/mpv/pull/10762)) [A].
**Handoff:** mpv needs DRM master. Option a: the UI compositor runs on tty1 and mpv in a unit on tty2. `chvt 2` makes logind/libseat take master from cage. mpv then opens the card, and its VT switcher returns on exit (the `vo_drm` VT-pipe hang was fixed 2026-10-11) [C]. Option b: stop the UI unit and restart it after playback (simpler, but a 2–4 s Chromium cold start) [I].

**2. Audio.** mpv: `--ao=alsa --audio-device=alsa/hdmi:CARD=PCH,DEV=<n> --audio-spdif=ac3,eac3,dts-hd,truehd --audio-exclusive=yes`. `dts-hd` already includes `dts` ([manual](https://github.com/mpv-player/mpv/blob/master/DOCS/man/options.rst)) [V]. PipeWire's IEC958 type list includes AC3, DTS, EAC3, TrueHD and DTS-HD ([iec958-types.h](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/spa/include/spa/param/audio/iec958-types.h)), and mpv's `ao_pipewire.c` maps TrueHD/DTSHD to it [C]. The sink's `iec958.codecs` must be set via WirePlumber. In practice HBR fails ([Kodi PR #28116](https://github.com/xbmc/xbmc/pull/28116): opened 2026-04-02, still open; a commenter on PipeWire 1.6.4 says DTS-HD/TrueHD fail in mpv and Kodi, ALSA works) [A]. Fedora 44 has PipeWire 1.6.2. → **Use ALSA and keep WirePlumber off the HDMI PCM** (a rule disabling that ALSA card), the same choice PLAN.md made for Kodi. gamescope plays no part in audio. The 23.976 dropout is an i915 HDMI-link issue at >10 bpc, so the **kernel 10-bit cap patch** is still required. mpv does not set `max bpc`; a runtime alternative is a tiny libdrm `max bpc=10` setter run before mpv (test whether it survives the master change) [I].

**3. Refresh.** gamescope: see table; flags `-r/--nested-refresh` (game frame limit), `--generate-drm-mode cvt|fixed`, `-O/--prefer-output HDMI-A-1`, and no per-video switching for external displays [C]. SteamOS/Bazzite switch only via Steam setting the root atom; with the convar off for external displays, a TV stays at its boot mode [C, I]. mpv: no mode switching on Wayland or X11. On DRM, the mode is chosen at VO init, so a Lua `on_preloaded` hook can read the video track's `demux-fps` and set `drm-mode` before the VO opens. That avoids an extra ffprobe; to be spiked [I]. Add `--video-sync=display-resample` for the leftover drift [V].

**4. Kiosk browser.** Fedora 44: **chromium 156.0.8078.47** (stable 2026-10-23), **firefox 150**, **cage 0.3.1** ([v0.3.1](https://github.com/cage-kiosk/cage/releases/tag/v0.3.1), 2026-06-30, `-s` allows VT switching), **qt6-qtwebengine 6.11.2**. **cog / wpewebkit are not packaged**, so WPE means building it ourselves (no) [V]. Chromium on Wayland composites `backdrop-filter`/`transform` on the GPU. The prototype uses 14 `backdrop-filter` blurs (5–22 px), 181 transforms and 54 keyframes, so **render the UI at 1080p60** (`wlr-randr` in cage, or `--force-device-scale-factor`) and measure; 4K blur on 24 EUs is the likely frame-drop spot. Expect roughly 400–700 MB RSS [I]. Focus question: cage shows one app, and mpv isn't inside it, so there is no stacking or focus problem.

**5. Existing projects.** [Jellyfin State of the Fin 2026-05-24](https://jellyfin.org/posts/state-of-the-fin-2026-05-24/): Jellyfin Desktop 3.0 (in development) **dropped Qt/QtWebEngine + embedded libmpv** for CEF layered over standalone-mpv's `gpu-next` pipeline, to get native HDR on Wayland. They also cite QtWebEngine memory leaks [V]. Lesson: embedded libmpv (render API inside WPE/Qt) loses HDR, because the host's surface, not mpv, owns the colorspace, so **don't embed** [I from V]. JD 2.0 (Qt 6 + MpvQt) shipped 2025-12-14 ([release](https://github.com/jellyfin/jellyfin-desktop/releases/tag/v2.0.0)). JD 3 needs a colour-managed Wayland compositor (KWin-class), which is plan B below. No project found that does exactly "web UI + mpv on DRM handoff".

**6. CEC.** No CEC on the tier-1 boxes (HARDWARE.md §3). Pulse-Eight: the kernel driver `pulse8-cec` needs `inputattach --pulse8-cec /dev/ttyACM0` (systemd unit + udev rule in the [kernel CEC docs](https://docs.kernel.org/admin-guide/media/cec.html)) [V]. `CEC_CAP_DEFAULTS` includes `CEC_CAP_RC`, and cec-core registers an `rc-cec` keymap ([cec.h](https://github.com/torvalds/linux/blob/master/include/media/cec.h), [pulse8-cec.c](https://github.com/torvalds/linux/blob/master/drivers/media/cec/usb/pulse8/pulse8-cec.c)), so TV-remote keys arrive as evdev keys for cage/Chromium and the bridge [C]. Configure with `cec-ctl --playback` (v4l-utils 1.32.0 in F44). libcec 7.1.1 is in F44 too, but it isn't needed for keys [V]. The adapter's 4K60 8-bit 4:2:0 limit means **no video through it**: it goes on a spare TV input, CEC bus only (untested trick) [V/A].

**7. What we rebuild vs Kodi.** mpv gives us natively: libass ASS, PGS/VobSub/SRT, external subs (`sub-add` over IPC), audio/sub track selection and delays, chapters, `--start=` resume, display-resample, VA-API (`--hwdec=vaapi`), JSON IPC (`--input-ipc-server`) [V]. We must write: (a) the launcher/handoff unit, (b) the evdev → IPC input bridge, (c) an OSD matching the UI design in mpv Lua (start from uosc/ModernZ-style scripts; HTML can't sit over DRM video), (d) the refresh hook, (e) resume/IntroDB/next-stream logic talking to the hub (already planned for Kodi), (f) audio-device/passthrough settings screen in the UI. That is roughly the "Kodi addon + skin" budget moved into JS + Lua, plus about 3 small daemons. Kodi's audio engine fallbacks (passthrough → PCM decode when the AVR lacks a codec) become an `--audio-spdif` list built from the AVR's ELD (`/proc/asound/card*/eld*`) [I].

## Recommended architecture

```
tty1: media-ui.service   cage -s -- chromium --kiosk --ozone-platform=wayland --app=http://127.0.0.1:<hub>/ui
                         (UI at 1920x1080@60 SDR; talks to hub API; "Play" = POST hub /play)
tty2: mpv@.service       chvt 2; mpv --vo=gpu-next --gpu-api=opengl --gpu-context=drm \
                           --drm-connector=HDMI-A-1 --drm-format=xrgb2101010 --drm-mode=<WxH@fps> \
                           --target-colorspace-hint=yes --hwdec=vaapi --video-sync=display-resample \
                           --ao=alsa --audio-device=alsa/hdmi:CARD=PCH,DEV=<n> --audio-exclusive=yes \
                           --audio-spdif=ac3,eac3,dts-hd,truehd --input-ipc-server=/run/media/mpv.sock \
                           --script=/usr/share/mediabox/mpv/{refresh,osd,hub}.lua <url>
                         ExecStopPost: chvt 1
input-bridge.service     evdev (Flirc/OSMC/rc-cec) → mpv IPC while mpv runs; cage gets keys natively otherwise
pulse8-cec@.service      inputattach --pulse8-cec (optional)
kernel                   + LibreELEC 10-bit HDMI cap patch (already a Phase 0 spike)
WirePlumber              rule: ignore the HDMI ALSA card (mpv owns it)
```
Packages (F44): `cage chromium mpv(master build or COPR) libplacebo ffmpeg(RPM Fusion, not ffmpeg-free) linuxconsoletools v4l-utils libva-intel-media-driver`. Drop: kodi, gamescope.
**Plan B** if VT handoff proves flaky: a **KWin 6.6** session (`kwin_wayland --drm --exit-with-session=…`, F44 has 6.6.4) with HDR on (`kscreen-doctor output.HDMI-A-1.hdr.enable`). mpv then runs as a Wayland client over Chromium using `wp_color_management_v1`, with the HTML OSD over the video, and refresh switching via `kscreen-doctor …mode`. Costs: the KDE stack in the image, the UI rendered SDR-in-PQ, the GUI permanently 4K HDR (4:2:0 at 60 Hz on 18 Gbps). Untested on Intel [I]. sway 1.12 has HDR10, but F44 ships sway 1.11 (no HDR) [V].

## Spike plan (ordered by what kills the idea)

| # | Spike | Kills the idea if… | VM? |
|---|---|---|---|
| 1 | mpv master on DRM: HDR10 PQ remux, `--drm-format=xrgb2101010`. TV info menu shows HDR10/BT.2020 at 10/12-bit; `drm_info` shows the metadata blob; ×20 start/stop TV-direct and via AVR | no HDR flag or wrong colours that Kodi GBM gets right on the same box | **No** (vmwgfx has no HDR props) |
| 2 | TrueHD Atmos + DTS-HD MA via ALSA from mpv, 30 min at 2160p23.976 with and without the 10-bit cap | dropouts persist with the cap (then Kodi has the same problem; not specific to this stack) | No |
| 3 | `--drm-mode=3840x2160@23.976` and the Lua `on_preloaded` hook picks 23.976/24/25/50/59.94 correctly (TV info + `drm_info`) | mode chosen too late (VO already open) → fall back to ffprobe/hub metadata before launch | Partly (hook logic yes, modes no) |
| 4 | VT handoff cage ⇄ mpv: 50 cycles, Chromium survives, input returns, no black screen | cage/i915 loses the output after VT return → option b (restart UI) or plan B | **Yes** (vmwgfx + VT switch) |
| 5 | Input bridge: Flirc/OSMC keys and media keys reach mpv on tty2 and Chromium on tty1 | — (only engineering) | Yes (virtual keyboard); remotes on N150 |
| 6 | Chromium kiosk on N150 at 1080p60: prototype fps with blur during idle, memory, cold start time | <50 fps on main screens → trim blur (CSS fallback) | Plumbing yes; perf **No** |
| 7 | mpv Lua OSD matching the prototype (seek bar, tracks, subs, next-up) | — (design cost) | Yes |
| 8 | Pulse-Eight on a spare TV input: `cec-ctl --playback`, keys via rc-cec, TV power on/standby | — (CEC is optional; RF remote is the default) | No |
| 9 | Plan B only if 4 fails: KWin HDR session on Intel with mpv `waylandvk` | — | No |

## Open risks

1. **mpv's DRM HDR path is young and lightly tested**: merged by an author without an HDR display, fixed again in Oct 2026, master-only. We ship a git mpv and could hit regressions. This is the **single biggest risk**: if spike 1 fails, the only HDR players left are Kodi GBM or a KWin session (untested on Intel).
2. The Intel 23.976 HD-audio bug needs our own kernel patch whatever the player (unchanged from the Kodi plan).
3. HDMI resync time per play/stop through the AVR (HARDWARE.md §6 #2) is user-visible; this design has as many mode changes as Kodi, not fewer.
4. The OSD is a second UI codebase (Lua/ASS) next to the HTML UI; keeping them visually consistent is ongoing work.
5. A git mpv + RPM Fusion ffmpeg inside a bootc image means pinning, a COPR or our own build, and greenboot checks for `mpv --version`.
6. gamescope stays wrong for this job until it exposes non-AMD colour pipelines and fractional external refresh. Revisit only if upstream changes `drm_set_refresh` to mHz and enables external dynamic modes.
