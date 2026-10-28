# Testing the image in VMware

VM settings: Fedora 64-bit, UEFI with Secure Boot off, 2 cores, 8 GB, 40 GB disk, bridged network, 3D on with 2 GB graphics memory.

1. Download the ISO (run id from the Actions page, "Build ISO"):
   ```powershell
   gh run download <run id> --repo TheFactor1/media-box -n iso -D "$HOME\Downloads\mediabox-iso"
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

What a VM can't tell us: HDR, audio passthrough, refresh-rate switching, real Kodi performance. Those need the N150.
