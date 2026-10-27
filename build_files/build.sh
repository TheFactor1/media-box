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

chmod +x /usr/libexec/mediabox/kodi-seed

# Kodi talks to ALSA directly; keep the media user's session from starting PipeWire on the HDMI device.
systemctl --global mask pipewire.socket pipewire.service pipewire-pulse.socket pipewire-pulse.service wireplumber.service

# Jellyfin for Kodi, its repo, and its dependency tree from the Kodi "piers" repo,
# pinned by sha256. Kodi updates them from those repos afterwards.
mkdir /tmp/kodi-addons
while read -r sum url; do
  zip="/tmp/kodi-addons/${url##*/}"
  curl -fsSL -o "$zip" "$url"
  echo "$sum  $zip" | sha256sum -c -
  python3 -m zipfile -e "$zip" /usr/share/kodi/addons/
done <<'EOF'
7409807249c9cc43c6b3b0df3fb9baa380bc62afcc8ad464444a95261a437ba6 https://kodi.jellyfin.org/repository.jellyfin.kodi.zip
58cc72c0eedc624a98a255f5c95c64b6e0f6f906c9666d0893fee152eb4fb8b1 https://repo.jellyfin.org/files/client/kodi/py3/plugin.video.jellyfin/plugin.video.jellyfin-2.2.0+py3.zip
30fab89549032cfcf8926da6f2f0ca8775bfd1a0b8ed199ea7a89fb14c124967 https://mirrors.kodi.tv/addons/piers/script.module.requests/script.module.requests-2.31.0.zip
cbc08498e4b39a12f246eb0e721f47ba653a92706c5fea5684fee3b2318695cf https://mirrors.kodi.tv/addons/piers/script.module.urllib3/script.module.urllib3-2.2.3.zip
a1267f062f960fb51103b4c6609d385b33a36acc3d74b27e2a4f2cbf66b10f98 https://mirrors.kodi.tv/addons/piers/script.module.idna/script.module.idna-3.10.0.zip
63bd8c75819256e39095ed0651ae7a2206e12adbf88d527582b4803b8b2a9824 https://mirrors.kodi.tv/addons/piers/script.module.chardet/script.module.chardet-5.1.0.zip
661463021711c9cd3e1a7dd0b63fbbf07b2eb253e07a6835e3e69ae3f353fbc4 https://mirrors.kodi.tv/addons/piers/script.module.certifi/script.module.certifi-2023.5.7.zip
cc17745aa3e37ebd6709ab1042ee43405c992402b5f1d67eea260a99f51a9360 https://mirrors.kodi.tv/addons/piers/script.module.dateutil/script.module.dateutil-2.8.2.zip
15fa60d61fb067d4e81233109fc28c02310e3374798b2a88d9f399abf22958ef https://mirrors.kodi.tv/addons/piers/script.module.six/script.module.six-1.16.0+matrix.1.zip
4de56d48fd80d963e48afa810e11d9fea2c44e023d511859a50ae693d771cd86 https://mirrors.kodi.tv/addons/piers/script.module.websocket/script.module.websocket-1.6.4.zip
943aaf77ecca936258ee49a20b294eb2995f7f118599e8b89d537be77e74e646 https://mirrors.kodi.tv/addons/piers/script.module.typing_extensions/script.module.typing_extensions-4.7.1.zip
EOF
# Kodi adds add-ons it finds on disk as disabled unless the manifest lists them
# (CAddonDatabase::SyncInstalled); optional="true" enables them but lets the user disable them.
for id in repository.jellyfin.kodi plugin.video.jellyfin script.module.{requests,urllib3,idna,chardet,certifi,dateutil,six,websocket,typing_extensions}; do
  sed -i "s|</addons>|  <addon optional=\"true\">$id</addon>\n</addons>|" /usr/share/kodi/system/addon-manifest.xml
done
grep -q '>plugin.video.jellyfin</addon>' /usr/share/kodi/system/addon-manifest.xml

systemctl set-default multi-user.target
systemctl disable getty@tty1.service
systemctl enable kodi.service podman.socket
# Hub mode is on by default; the wizard flips this to player.target later.
systemctl enable hub.target
