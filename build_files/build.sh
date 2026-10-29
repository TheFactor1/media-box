#!/usr/bin/bash
set -ouex pipefail

cp -r /ctx/system_files/. /

# RPM Fusion release RPMs pinned by sha256: they carry the repo GPG keys every later package is checked against.
# To bump (new Fedora or a new -N release): pick the newest rpmfusion-{free,nonfree}-release-<fedora>-N.noarch.rpm
# under download1.rpmfusion.org/{free,nonfree}/fedora/updates/<fedora>/x86_64/r/ (or releases/<fedora>/Everything/
# x86_64/os/Packages/r/ if updates has none), take `curl -fsSL <url> | sha256sum`, and change the lines below by PR.
# updates/ drops superseded files, so a 404 here means RPM Fusion shipped a new -N: bump it.
[[ "$(rpm -E %fedora)" == 44 ]] || { echo "Fedora changed: re-pin the RPM Fusion release RPMs in build.sh" >&2; exit 1; }
rpmfusion=https://download1.rpmfusion.org
curl -fsSL -o /tmp/rpmfusion-free-release.rpm "$rpmfusion/free/fedora/updates/44/x86_64/r/rpmfusion-free-release-44-3.noarch.rpm"
curl -fsSL -o /tmp/rpmfusion-nonfree-release.rpm "$rpmfusion/nonfree/fedora/updates/44/x86_64/r/rpmfusion-nonfree-release-44-3.noarch.rpm"
sha256sum -c - <<'EOF'
8af2dbb02e3a72f0961ec79cf1ea3f350719cb830b0f99f59e939389feb34b1c  /tmp/rpmfusion-free-release.rpm
c2606f494b0c4417bf6632f865448da3febb420e19182e47874d8db4c35a8a8e  /tmp/rpmfusion-nonfree-release.rpm
EOF
dnf5 install -y /tmp/rpmfusion-free-release.rpm /tmp/rpmfusion-nonfree-release.rpm
rm /tmp/rpmfusion-free-release.rpm /tmp/rpmfusion-nonfree-release.rpm

# RPM Fusion's single kodi binary is built with x11, wayland and gbm backends;
# `kodi --windowing=gbm` picks GBM at runtime (spike 2: yes).
dnf5 install -y kodi libcec podman \
  intel-media-driver libva-intel-media-driver mesa-dri-drivers mesa-va-drivers-freeworld \
  plymouth plymouth-system-theme alsa-utils firewalld

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

# Only the hub API and its discovery beacon are reachable from the LAN; AIOStreams (3000) and Kodi's web
# server (8080) stay local. The zone's default ssh service stays: sshd is off on release boxes, on for dev ones.
firewall-offline-cmd --add-port=8097/tcp --add-port=8098/udp
[[ "$(firewall-offline-cmd --list-ports | tr ' ' '
' | sort | xargs)" == "8097/tcp 8098/udp" ]]
systemctl enable firewalld.service

systemctl set-default multi-user.target
systemctl disable getty@tty1.service
# SSH is off on release boxes (the dev kickstart turns it back on); disable, not mask, so it can be re-enabled.
systemctl disable sshd.service
if systemctl list-unit-files sshd.socket | grep -q '^sshd.socket'; then systemctl disable sshd.socket; fi
systemctl enable kodi.service

# Signed images: once our public key is in the image (system_files/etc/pki/containers/media-box.pub, see
# docs/SIGNING.md), podman refuses unsigned ghcr.io/thefactor1 images and the box switches its OS updates to
# enforced signatures once. Without the key nothing changes, so building before the key exists is safe.
if [[ -f /etc/pki/containers/media-box.pub ]]; then
  python3 - <<'PY'
import json
p = '/etc/containers/policy.json'
policy = json.load(open(p))
policy.setdefault('transports', {}).setdefault('docker', {})['ghcr.io/thefactor1'] = [{
    'type': 'sigstoreSigned', 'keyPath': '/etc/pki/containers/media-box.pub',
    'signedIdentity': {'type': 'matchRepository'}}]
json.dump(policy, open(p, 'w'), indent=2)
PY
  python3 -c "import json; assert json.load(open('/etc/containers/policy.json'))['transports']['docker']['ghcr.io/thefactor1'][0]['type'] == 'sigstoreSigned'"
  systemctl enable mediabox-sigpolicy.service
fi
# Hub mode is on by default; the wizard flips this to player.target later.
systemctl enable hub.target
