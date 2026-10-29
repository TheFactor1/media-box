# Media box

Two front ends, one hub. See [PLAN.md](PLAN.md), [EXPERIENCE.md](EXPERIENCE.md), [DATAFLOW.md](DATAFLOW.md).

## Hub (`hub/`)
Python 3.12 stdlib + SQLite. Tests: `python -I -m unittest discover -s hub -t .`
Run on a Docker host: `cd hub && mkdir -p data && cp hub.example.toml data/hub.toml && docker compose up -d --build`, then open `http://<host>:8097/setup?n=<nonce>` (first boot: get the link with `docker exec hub wget -qO- http://127.0.0.1:8097/setup/link`). The setup page takes a debrid key and approves TVs by their 6-digit code.

## Player (Android TV)
github.com/TheFactor1/tv-player, branch `home-programme`: the Home that talks to the hub. CI builds the APK; sideload with `adb install`.

# Fedora image (Phase 0)

Fedora bootc image that boots straight into Kodi on GBM and carries the hub's
container images inside the OS image. See [PLAN.md](PLAN.md).

## Build locally (Linux, podman)

```bash
sudo podman build -t media-box:latest .
```

## ISO

`disk_config/iso.toml` is the release installer (no login, SSH off). For a test box you can SSH into, put your public key in `disk_config/iso-dev.toml` and build that variant (Actions → Build ISO → variant `dev`); locally, swap the config path below:

```bash
sudo podman run --rm --privileged --security-opt label=type:unconfined_t \
  -v ./disk_config/iso.toml:/config.toml:ro -v ./output:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type anaconda-iso --rootfs btrfs --config /config.toml localhost/media-box:latest
```

The installer wipes the target disk (see the kickstart in `iso.toml`).

## Phase 0 checks this scaffold answers

| Spike | How to check on the box |
|---|---|
| 2 Kodi GBM packaged | `podman build` fails at `dnf5 install kodi-gbm` if not |
| 3 Boots to Kodi, no desktop | `systemctl status kodi` ; nothing on tty1 but Kodi |
| 7 Bound images | After the first `bootc upgrade`, `podman --storage-opt=additionalimagestore=/usr/lib/bootc/storage images` lists the stack with no network; `systemctl status jellyfin`; still there after `bootc rollback` |

Everything else in Phase 0 (HDR, passthrough, usenet/debrid, remote) is done
by hand on this image.
