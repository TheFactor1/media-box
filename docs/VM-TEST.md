# Testing the image in VMware

VM settings: Fedora 64-bit, UEFI with Secure Boot off, 2 cores, 8 GB, 40 GB disk, bridged network, 3D on with 2 GB graphics memory.

0. Once: give the dev `admin` account a console password. Make its hash in Git Bash (it asks twice; the password itself goes nowhere) and store the hash as a repo secret:
   ```sh
   openssl passwd -6 | gh secret set DEV_ADMIN_PASSWORD_HASH --repo TheFactor1/media-box
   ```
   The password works at the VM's console (and in rescue mode) only; SSH stays key-only. Root stays locked. A dev build without the secret fails rather than shipping a box nobody can log in to at the console.
1. Build the **dev** ISO (Actions → Build ISO → Run workflow → variant `dev`; the release ISO has no login and SSH off), then download it:
   ```powershell
   gh run download <run id> --repo TheFactor1/media-box -n iso-dev -D "$HOME\Downloads\mediabox-iso"
   ```
2. VM → Settings → CD/DVD → Use ISO image file → the `.iso` in that folder, tick **Connect at power on**. Power on.
3. The installer runs unattended (it wipes the VM's virtual disk), reboots, and boots the image. First boot pulls the hub and AIOStreams images, so give it a few minutes on the network.
4. Find the VM's address in the VMware console (it shows on the login screen if Kodi doesn't start), then from the PC:
   ```powershell
   ssh admin@<vm-ip>
   ```

## On the VM (as admin)
| Check | Command | Good result |
|---|---|---|
| Image | `sudo bootc status` | booted from `ghcr.io/thefactor1/media-box:latest` |
| Hub | `systemctl status hub` | active (running) |
| AIOStreams | `systemctl status aiostreams` | active (running) |
| Hub answers | `curl -s localhost:8097/health` | `{"ok": true, ...}` |
| Setup link | `curl -s localhost:8097/setup/link` | a `http://<vm-ip>:8097/setup?n=...` URL; open it on your phone |
| Beacon | from the PC: Player's "Search again" on the onn | finds "Media box" |
| Kodi | `systemctl status kodi` | running, or a GL/EGL error, which is the VM's virtual GPU, not the image |
| Update | `sudo bootc upgrade` | "No update available" or stages one |
| Rollback | `sudo bootc rollback && sudo reboot`, then `sudo bootc status` | booted from the previous deployment |

## Trying our own interface instead of Kodi
Kodi is still the default. On a VM on an image that includes it, swap for this boot:
```sh
sudo systemctl stop kodi && sudo systemctl start mediabox-ui
```
To make it stick: `sudo systemctl disable kodi && sudo systemctl enable mediabox-ui`. Back to Kodi: `sudo systemctl stop mediabox-ui && sudo systemctl start kodi`.

## Checking containers
The hub and AIOStreams images live in bootc's own store, so plain `podman` can't see them and reports them as missing or damaged. Always pass the store:
```sh
sudo podman --storage-opt=additionalimagestore=/usr/lib/bootc/storage ps
```
Even then, `podman system check` reports "layer is missing" for a running container, because it doesn't look across the two stores. That isn't damage. Don't run `--repair` on it.

What a VM can't tell us: HDR, audio passthrough, refresh-rate switching, real Kodi performance. Those need the N150.
