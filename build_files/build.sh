#!/usr/bin/bash
set -ouex pipefail

cp -r /ctx/system_files/. /

rel=$(rpm -E %fedora)
dnf5 install -y \
  "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${rel}.noarch.rpm" \
  "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${rel}.noarch.rpm"

# RPM Fusion's single kodi binary is built with x11, wayland and gbm backends;
# `kodi --windowing=gbm` picks GBM at runtime (spike 2: yes).
dnf5 install -y kodi libcec podman \
  intel-media-driver libva-intel-media-driver mesa-dri-drivers mesa-va-drivers-freeworld \
  plymouth plymouth-system-theme alsa-utils

chmod +x /usr/libexec/mediabox/kodi-seed

# Kodi talks to ALSA directly; keep the media user's session from starting PipeWire on the HDMI device.
systemctl --global mask pipewire.socket pipewire.service pipewire-pulse.socket pipewire-pulse.service wireplumber.service

# Our Kodi front end (kodi-addons/): the hub add-on as is, and skin.mediabox built from this Kodi's own
# Estuary plus our overlay, so Estuary fixes arrive with every Kodi update.
cp -r /ctx/kodi-addons/plugin.video.mediabox /usr/share/kodi/addons/
python3 /ctx/kodi-addons/build-skin.py /usr/share/kodi/addons/skin.estuary /usr/share/kodi/addons/skin.mediabox
# Kodi adds add-ons it finds on disk as disabled unless the manifest lists them
# (CAddonDatabase::SyncInstalled); optional="true" enables them but lets the user disable them.
for id in skin.mediabox plugin.video.mediabox; do
  sed -i "s|</addons>|  <addon optional=\"true\">$id</addon>\n</addons>|" /usr/share/kodi/system/addon-manifest.xml
  grep -q ">$id</addon>" /usr/share/kodi/system/addon-manifest.xml
done

# Hub, discovery beacon, AIOStreams (Adjust sources) and Kodi's web remote must be reachable on the LAN.
if command -v firewall-offline-cmd >/dev/null; then
  firewall-offline-cmd --add-port=8097/tcp --add-port=8098/udp --add-port=3000/tcp --add-port=8080/tcp
fi

systemctl set-default multi-user.target
systemctl disable getty@tty1.service
systemctl enable kodi.service podman.socket
# Hub mode is on by default; the wizard flips this to player.target later.
systemctl enable hub.target
