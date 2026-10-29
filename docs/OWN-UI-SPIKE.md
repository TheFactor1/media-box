# Own UI instead of Kodi: VM spike (2026-10-29)

This is the hands-on companion to [OWN-UI-RESEARCH.md](OWN-UI-RESEARCH.md), which covers HDR, passthrough and refresh switching and isn't repeated here. It tests whether `prototype.html` (Look = Space, Layout = Shelf) can run as the TV UI on tty1 and hand off to mpv. Test VM: VMware with a vmwgfx 3D virtual GPU, 2 vCPU, 8 GB, Fedora 44 bootc image. Packages came from `dnf5` inside `bootc usroverlay`; everything was removed again afterwards (see Cleanup).

## Verdict

- **cage + Chromium kiosk + mpv on DRM works end to end on the VM.** The prototype runs full-screen on tty1 as `media` and arrow/Enter/Escape keys navigate it. Play stops the UI and mpv plays on KMS. mpv takes keys, and on `q` the UI comes back on the same screen with the same focus.
- **gamescope can't run on this VM.** That's moot now, since the research drops it.
- **The VM can only partly judge smoothness.** To run at all, cage has to composite in software here, and it reaches about 40 fps in Full motion. The N150 has to re-measure this.

## Versions (F44 repos, 2026-10-29)

kernel 7.2.13, mesa 26.2.5, **cage 0.3.1** (wlroots 0.20.2), **chromium 156.0.8078.47**, **mpv 0.41.0**, gamescope 3.16.29, Xwayland 24.1.14, ffmpeg-free 8.1.3, ydotool 1.0.4. cog/WPE isn't packaged: `dnf install cog` resolves to python3-cogapp. Firefox wasn't tried; Chromium worked.

## What ran

- `spike-ui`: a transient unit shaped like kodi.service: `User=media`, `PAMName=login`, `TTYPath=/dev/tty1`, `StandardInput=tty`. It runs `cage -s -- chromium-browser --ozone-platform=wayland --kiosk --app=http://127.0.0.1:8099/spike.html`.
- `spike.html`: a copy of the prototype with three additions:
  - a document skeleton (see Prototype gotcha);
  - localStorage presets for Space and Shelf;
  - CSS that fills the screen with the TV frame, plus a rAF FPS logger and a key/state logger that POST to the helper.
- `spike-helper`: about 50 lines of Python running as root on 127.0.0.1:8099. It serves the page, collects logs, and owns the handoff:
  - `POST /play {id, resume}`: stop `spike-ui`, then run `systemd-run --wait` mpv on tty1 (same PAM/TTY properties). When mpv exits, start `spike-ui` again.
  - `GET /resume`: return the saved position once.
- mpv command: `mpv --vo=gpu-next --gpu-context=drm --fs test.webm` (a 20 s 1080p24 VP9 test clip from ffmpeg).
- Keys came from `ydotoold` (uinput). Screenshots came from `grim` (wlroots screencopy) and CDP `Page.captureScreenshot`.

## Results

| What | Result |
|---|---|
| gamescope 3.16.29 (default and `--backend drm`) | **Fails**: `vulkan: not a valid physical device` / `Failed to initialize Vulkan`. vmwgfx has no Vulkan driver, and gamescope won't use llvmpipe. Not testable on this VM |
| cage, default GLES2 renderer | Chromium dies: `importing the supplied dmabufs failed` (wlroots dmabuf import on vmwgfx; `drmCloseBufferHandle: EINVAL`). Same via Xwayland (`--ozone-platform=x11`). `WLR_DRM_NO_MODIFIERS=1` doesn't help |
| cage with `WLR_RENDERER=pixman` | **Works.** Chromium keeps GPU raster/WebGL (`ANGLE (VMware, SVGA3D … OpenGL ES 3.1)`); cage composites with the CPU. `--disable-gpu` also works. Every number below uses this VM-only workaround |
| Output mode | 1280x800, the VM's preferred mode; the 16:9 stage is letterboxed. cage has no mode flag, so the box needs `wlr-randr` or a kernel `video=` mode to pin 1080p |
| Session start → page first frame | Cold (new profile): **1.34 s**. Warm: **0.76 s**. Navigation start → first frame on the page: 0.84 s cold, 0.29 s warm |
| Memory (PSS, whole UI) | **583–605 MB**: Chromium 558–580, cage 11 |
| Idle CPU, Full motion | **~108 % of one core**: GPU process 66 %, cage/pixman 26 %, renderer 16 %. Space keeps 132 CSS animations running forever (skyPan, nebula, drift, twinkle) |
| Idle CPU, Calm motion | **5 %** |
| rAF FPS, Full, idle | 42–44 fps (p50 21–22 ms, p95 ~39 ms) |
| rAF FPS, Full, navigating | 38–43 fps. **220–240 ms frames** on screen changes (Home → Title, Back) |
| rAF FPS, Calm, navigating | 47–53 fps. Worst frame 117 ms |
| Keys → UI (uinput → libinput → cage → Chromium) | **Works.** The logged trace moved through tabs, Resume, both card rows; Enter opened Title with Play focused; Escape returned to the card |
| Play → mpv first video frame | **~0.43 s** from Enter. Stopping cage takes 70–540 ms (usually ~110). mpv's first video frame comes 0.21–0.30 s after it starts |
| mpv quit → UI first frame | **1.2–1.5 s**: cage plus a warm Chromium start. Whatever tty1 shows (console text) is visible during the gap |
| Focus restore after playback | **Works.** The page reopens on Title with Play focused, and the back stack and layout are restored |
| Keys → mpv on the VT | **Works only with stdin *and* stdout on the tty.** With `StandardOutput=journal`, mpv ignored all keys. With `StandardOutput=tty`, Space paused/resumed and `q` quit (mpv log: `cycle pause`, `quit`) |

## Findings and gotchas

1. **The prototype isn't a standalone page.** It's an Artifact body with no doctype, charset or `[hidden]{display:none}` (the Artifact host adds those).
   - Served raw, every screen is visible at once.
   - The empty `#ov` overlay (`display:grid` beats the `hidden` attribute) blurs the whole screen through `backdrop-filter`.
   - Text shows mojibake (`Â·`).
   - The real UI page must ship `<!doctype html><meta charset=utf-8>` and an explicit `[hidden]{display:none!important}`.
2. **Space's ambient animations cost a core** on the VM's software path; Calm costs 5 %. On the N150, measure Full motion at 1080p with the GLES2 renderer. If it still costs a lot, pause sky/nebula animations after the first idle minute: TV-idle power and fan noise matter.
3. **Screen-change hitches (220–240 ms)** come from rebuilding the DOM on Home → Title and Back. A VM can't tell how much of that is real; check them on the N150 before tuning.
4. **Resume race.** The page fetches `/resume` asynchronously.
   - In 1 of 3 runs it painted Home and then jumped to Title.
   - Fix: the hub writes the resume state into the page it serves, or the page holds its first paint until `/resume` answers.
   - localStorage isn't reliable for this because Chromium is killed.
5. **The gap after playback shows the tty1 text console**, not black. The box needs a silent console (`quiet`, `vt.global_cursor_default=0`, `loglevel=3`, no getty on tty1). Or use the research's option (a): UI on tty1, mpv on tty2 with `chvt`, so cage keeps running. That should also cut the return from ~1.3 s to a VT switch, but it's **untested here**.
6. **mpv's VT input is a stopgap, not the answer.**
   - Terminal input gives plain keys only. Escape is ambiguous (ESC-sequence delay), and media keys (KEY_PLAYPAUSE, rc-cec) never reach a tty.
   - mpv's default ESC only leaves fullscreen.
   - After the first cage/mpv cycle, tty1's keyboard was left in **K_OFF** ("Disabled") mode once. A launcher would have to reset it (`kbd_mode -u`) before mpv.
   - The real fix is the evdev → `--input-ipc-server` bridge from the research, which reads the remote's device directly and maps Back → `quit`.
7. **Modifiers block navigation.** The prototype ignores any keydown with Ctrl/Alt/Meta held. Once, VMware's console injected stray Meta/Ctrl/Alt events and navigation stalled. A Flirc mapped to plain keys is fine, but don't map remote buttons to modifier combos.
8. **dnf scriptlets in a usroverlay write to persistent /etc.**
   - nss-mdns ran `authselect enable-feature with-mdns4`, which changed the nsswitch `hosts` line.
   - chromium's %post ran `semanage fcontext -a`, adding 3 local fcontexts and rebuilding the policy store.
   - A reboot doesn't undo either. VM-TEST.md should warn about this.

## Not tested on the VM

- Hub stream URLs (`/play/{id}?json=1`): the spike used a local file.
- Firefox kiosk.
- The tty2/`chvt` handoff.
- Chromium memory growth over hours.
- Audio.

## Still needs the N150 + HDR TV

- **Real compositor path:** cage's GLES2 renderer on i915 (no pixman). Then re-measure FPS in Full motion at 1080p60, idle CPU/power, and first frame. Pin the UI mode to 1080p on a 4K TV.
- **Handoff on real HDMI:** each mode change adds the TV's resync (often 1–3 s) to the 0.43 s and ~1.3 s above. Measure with a stopwatch, and compare stop/start against tty2 + `chvt`.
- **HDR10, 10-bit, refresh matching (23.976/24/25/50/60) and TrueHD/DTS-HD passthrough with mpv on DRM:** see OWN-UI-RESEARCH.md. The VM has no EDID (`Unable to get EDID blob`), no HDR and no audio sink.
- **Remote input:** Flirc / OSMC RF / Pulse-Eight rc-cec keycodes through libinput to Chromium, and the evdev → mpv IPC bridge while mpv runs.

## Cleanup done

- Stopped and removed the `spike-*` units and deleted `/tmp/spike`.
- Ran `authselect disable-feature with-mdns4` and `semanage fcontext -d` (x3). Restored the two SELinux store files from `/usr/etc` and deleted the 4 files semanage created. Every authselect, pam.d and selinux file now matches `/usr/etc`.
- Deleted the package-owned /etc files that aren't in the image: chromium, mpv, openal, cups, at-spi, `org.selinux.conf`.
- Started Kodi, then rebooted, which dropped the overlay; the spike packages are no longer installed. Ran `ldconfig`, so `/etc/ld.so.cache` matches the image again.
- After the reboot, `kodi` and `hub` are active. Nothing in `/var/lib/hub` or `/var/lib/media` was touched, and nothing in the repo was committed.
