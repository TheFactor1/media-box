# Build context: build_files/, system_files/ and kodi-addons/ are reachable via /ctx
# during RUN steps without ending up in the final image.
FROM scratch AS ctx
COPY build_files /build_files
COPY system_files /system_files
COPY kodi-addons /kodi-addons

# Plain Fedora bootc + RPM Fusion. (ublue base-main uses negativo17, whose
# ffmpeg conflicts with the RPM Fusion ffmpeg that Kodi is built against.)
# On a tag, not a digest, on purpose: the daily build must pick up Fedora security updates.
FROM quay.io/fedora/fedora-bootc:44

RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=cache,dst=/var/cache \
    --mount=type=cache,dst=/var/log \
    --mount=type=tmpfs,dst=/tmp \
    bash /ctx/build_files/build.sh

# Logically bound images (spike 7): bootc pulls every image named by a quadlet
# linked here into /usr/lib/bootc/storage during `bootc upgrade`, keeps the
# rollback deployment's copies, and GCs the rest. Copying images into the OS
# image at build time fails (lchown in the build's user namespace), and
# Anaconda doesn't pull LBIs at install, so a fresh install pulls once on
# first boot via Pull=missing; updates are atomic from then on.
RUN mkdir -p /usr/lib/bootc/bound-images.d \
    && for f in /usr/share/containers/systemd/*.container; do \
         ln -s "$f" /usr/lib/bootc/bound-images.d/; \
       done

RUN bootc container lint
