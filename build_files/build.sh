#!/usr/bin/bash
set -ouex pipefail

cp -r /ctx/system_files/. /

# kodi-gbm: if RPM Fusion doesn't ship the GBM windowing binary, this fails
# loudly, which is the answer to Phase 0 spike 2 (then we build Kodi ourselves).
dnf5 install -y kodi kodi-gbm libcec rclone fuse3 skopeo

# Player runs as an unprivileged user with a logind session on tty1.
useradd -u 1000 -M -d /var/lib/media -s /usr/sbin/nologin -G audio,video,render,input media

systemctl set-default multi-user.target
systemctl disable getty@tty1.service
systemctl enable kodi.service podman.socket
# Hub mode is on by default; the wizard flips this to player.target later.
systemctl enable hub.target
