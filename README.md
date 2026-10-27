# Media mini-PC image (Phase 0)

Fedora bootc image that boots straight into Kodi on GBM and carries the hub's
container images inside the OS image. See [PLAN.md](PLAN.md).

## Build locally (Linux, podman)

```bash
sudo podman build -t media-box:latest .
```

## ISO

Put your SSH public key in `disk_config/iso.toml`, then:

```bash
sudo podman run --rm --privileged --security-opt label=type:unconfined_t \
  -v ./disk_config/iso.toml:/config.toml:ro -v ./output:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type anaconda-iso --config /config.toml localhost/media-box:latest
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
