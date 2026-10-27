#!/usr/bin/bash
set -ouex pipefail

cp -r /ctx/system_files/. /

rel=$(rpm -E %fedora)
dnf5 install -y \
  "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${rel}.noarch.rpm" \
  "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${rel}.noarch.rpm"

# RPM Fusion's single kodi binary is built with x11, wayland and gbm backends;
# `kodi --windowing=gbm` picks GBM at runtime (spike 2: yes).
dnf5 install -y kodi libcec rclone fuse3 skopeo podman \
  intel-media-driver libva-intel-media-driver mesa-dri-drivers mesa-va-drivers-freeworld \
  plymouth plymouth-system-theme alsa-utils

systemctl set-default multi-user.target
systemctl disable getty@tty1.service
systemctl enable kodi.service podman.socket
# Hub mode is on by default; the wizard flips this to player.target later.
systemctl enable hub.target
