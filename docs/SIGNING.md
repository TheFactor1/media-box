# Image signing

`build.yml` and `hub.yml` sign each pushed image by digest with cosign. Until the key is in the repo
secrets, the Sign step only warns and the images go out unsigned. Boxes start **enforcing** signatures only
once the public key is committed at `system_files/etc/pki/containers/media-box.pub` (step 3 below), so the
order matters.

## One-time setup (Matt), in this order

Install cosign first (`winget install Sigstore.Cosign` on Windows, then reopen the terminal). From the repo root:

1. Create the key pair (asks for a password; writes `cosign.key` and `cosign.pub`):
   ```sh
   cosign generate-key-pair
   ```
2. Store the private key and password as repo secrets (PowerShell form; in Bash use `< cosign.key`):
   ```powershell
   Get-Content cosign.key -Raw | gh secret set COSIGN_PRIVATE_KEY
   gh secret set COSIGN_PASSWORD
   ```
   Move `cosign.key` out of the repo now and **back it up offline**: losing it means boxes stop accepting
   updates until each one is reinstalled with a new key.
3. Push to `main` (or run both workflows by hand) and check that both images verify (below). Only then:
4. Commit the public key into the image:
   ```sh
   mkdir -p system_files/etc/pki/containers
   mv cosign.pub system_files/etc/pki/containers/media-box.pub
   git add system_files/etc/pki/containers/media-box.pub && git commit -m "Ship the image signing key"
   ```
   From the next image on, podman refuses unsigned `ghcr.io/thefactor1` images (`/etc/containers/policy.json`,
   written by `build_files/build.sh`), and `mediabox-sigpolicy.service` switches the box's OS updates to
   enforced signatures once.

## Verify

```sh
digest=$(skopeo inspect docker://ghcr.io/thefactor1/media-box:latest --format '{{.Digest}}')
cosign verify --key system_files/etc/pki/containers/media-box.pub ghcr.io/thefactor1/media-box@$digest
cosign verify --key system_files/etc/pki/containers/media-box.pub ghcr.io/thefactor1/mediabox-hub@<digest>
```
On a box: `podman image trust show` lists `ghcr.io/thefactor1` as `sigstoreSigned`.

## Watch for failed scheduled builds

A failing daily build (an RPM Fusion release bump, a new Fedora) means boxes quietly stop getting updates.
GitHub emails you about failed scheduled runs; treat those as urgent.

## Rotating the key

Boxes trust only the keys baked into their current image, and a box that's offline for a while must still
accept whatever image it sees next. So rotate with an overlap:
1. Generate a new pair. Ship **both** public keys: in `build_files/build.sh`'s policy entry use
   `"keyPaths": ["/etc/pki/containers/media-box.pub", "/etc/pki/containers/media-box-next.pub"]` instead of
   `keyPath` (needs containers/image 5.33 or newer; check `podman --version` on the image, and if it's older
   keep signing with the old key for a longer overlap instead).
2. Switch the CI secrets to the new key; every build from now on is signed with it.
3. A few months later, drop the old public key from the image.
