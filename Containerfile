# Build context: build_files/ and system_files/ are reachable via /ctx
# during RUN steps without ending up in the final image.
FROM scratch AS ctx
COPY build_files /build_files
COPY system_files /system_files

# ublue base-main: Fedora bootc + RPM Fusion + codecs + Intel media driver.
FROM ghcr.io/ublue-os/base-main:latest

RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=cache,dst=/var/cache \
    --mount=type=cache,dst=/var/log \
    --mount=type=tmpfs,dst=/tmp \
    bash /ctx/build_files/build.sh

# Embed the hub's container images so they update and roll back with the OS
# (Phase 0 spike 7). Podman reads this store via additionalimagestores in
# system_files/etc/containers/storage.conf.
# shortcut: only jellyfin for the spike, add the rest of the stack in Phase 2
RUN --mount=type=tmpfs,dst=/run/containers \
    skopeo copy docker://docker.io/jellyfin/jellyfin:latest \
      "containers-storage:[overlay@/usr/lib/containers/storage+/run/containers/storage]docker.io/jellyfin/jellyfin:latest" \
    && rm -rf /usr/lib/containers/storage/overlay/*/work \
    && rm -f /usr/lib/containers/storage/*.lock /usr/lib/containers/storage/overlay/.has_mount_program

RUN bootc container lint
