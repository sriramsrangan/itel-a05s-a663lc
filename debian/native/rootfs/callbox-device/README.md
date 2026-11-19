# Call Box Device Layer

*Everything the itel A05s needs to boot Debian and bring its hardware up for the call box app.*

The call box phone runs two separately released parts. This directory is the device layer: busybox init, the start-up and service scripts, the modem tools' bring-up, Wi-Fi, the screen timeout, the hardware audio recipe and the stock audio tuning tables. The call box app (jobs, calls, SMS, recordings, the self-updater and the gateway contract) lives in the helix-messaging repository as `apps/callbox-agent` and is released from there. Both parts are signed with the same Ed25519 release key and installed on the phone by the app's updater; this repository never holds the signing key.

## Boundary

*What the device layer gives the app, and what it never touches.*

The app depends on the device layer only through fixed paths. Anything not listed here belongs to the app.

- `/etc/inittab`: the device layer's `inittab`. It starts every service through `callbox-svc`, including the app's three as `python3 -m callbox.vbc`, `python3 -m callbox.agent` and `python3 -m callbox.updater`, with `PYTHONPATH=/opt/callbox/current` (set by `callbox-svc`). `tests/test_init.py` checks the full list of services.
- `/etc/callbox/device/audio.toml`: the hardware audio recipe: sound cards, PCM devices, mixer controls (such as `Virt Output Switch` and `VBC Switch`), the `saudiolte` routes per call type, and sample rates and channels. The app reads only the device keys from here and takes its own settings (`uplink_verified`, `capture_stall_seconds`, `call_type_wait_seconds`) from `/etc/callbox/audio.toml`.
- `/etc/callbox/device/audio_params/`: the four stock tuning tables the modem downloads at modem start (`audiodsp`, `cvs`, `audioarm` and `dspcodec`, from the stock firmware's `odm/etc/audio_params`), checked by `SHA256SUMS`. The app's VBC service serves them to the modem.
- `/etc/callbox/device` is a link to `/opt/callbox-device/current/etc/callbox/device`, so a device release switches the recipe and the tables with it. `callbox-sysinit` repairs the link at boot.
- The app's own files (`/opt/callbox`, `/etc/callbox/agent.toml`, `/etc/callbox/audio.toml`, `/etc/callbox/release.pub`, `/etc/callbox/device.key` and `/var/lib/callbox`) are never written by the device layer.

## How It Starts

*Busybox init, because this kernel cannot run systemd.*

The kernel has no devtmpfs and no `CONFIG_FHANDLE`, so neither systemd nor udev can run. Busybox init reads `/etc/inittab`, runs `callbox-sysinit` once, then starts every service through `callbox-svc`, which logs to syslog under the service's name, pauses after an exit and lets init start it again.

- `sysinit`: `callbox-sysinit` mounts `/proc`, `/sys`, `/dev` (tmpfs, filled by `mdev -s`), `/run` and `/tmp`, writes `/etc/resolv.conf`, makes SSH host keys when missing, repairs the `/etc/callbox/device` link, builds the partition links and Android's property area (`callbox-prepare-android.sh`), pins the Wi-Fi MAC from `/etc/callbox/wifi-mac` and loads the Wi-Fi drivers (`callbox-wifi-modules.sh`).
- `respawn`, each under `callbox-svc <name>`: `mdev`, `syslogd`, `klogd`, `modem-control` (`/android/bin/modem_control`), `cp-diskserver` and `refnotify` (both started once `/dev/stty_lte2` exists, through `callbox-after-modem`), the app's `vbc`, `agent` and `updater`, `wpa` and `dhcp-wlan0` (once `wlan0` exists, through `callbox-after-wlan`; `callbox-udhcpc` gives Wi-Fi route metric 100), `wifi-route`, `sshd`, `chronyd` and `screen-timeout`.
- `screen-timeout`: `callbox-screen-timeout` is the display's KMS client (`lib/callbox_device/kms.py`, raw DRM ioctls). It sets the panel's mode and switches the connector's DPMS property, because without a KMS client the panel ignores brightness writes; it falls back to the backlight sysfs if KMS fails. The screen goes off after `TIMEOUT_SECONDS` without a key press (default 10, set in `/etc/callbox/screen.conf`); any key turns it on.
- `once`: `callbox-boot-report` writes the state, with the app and device versions, to pmsg every minute for the first 15 minutes, so a boot that cannot be reached over the network can still be read after a reboot.
- `shutdown`: `callbox-shutdown` hangs up any call. `restart` runs busybox init again.
- A service is paused by creating `/run/callbox/disabled-<name>`; `callbox-svc` then only waits.

## Files On The Phone

*One directory per release, and a link to the current one.*

- `/opt/callbox-device/releases/<version>/`: one device release, exactly as its bundle holds it.
- `/opt/callbox-device/current`: a link to `releases/<version>`, switched in one rename.
- `/etc/callbox/device`: a link to `current/etc/callbox/device`.
- `/etc/inittab`: the current device release's `inittab`; the one before is kept as `/etc/inittab.callbox-previous`.
- Local settings that stay on the phone and are not part of any release: `/etc/callbox/screen.conf` and `/etc/callbox/wifi-mac`.

## Releases

*Built and tested here, signed and published by helix-messaging.*

This repository is public, so the signing key never comes near it. The route is:

1. Every push and pull request that changes this directory runs the workflow `Device bundle`: the tests, then the bundle built twice and compared byte for byte, then uploaded as an unsigned artefact with its SHA-256 in the run summary.
2. To release, raise `VERSION`, merge to main, then run helix-messaging's workflow `Call box device release` with this repository's full commit SHA (and, optionally, the SHA-256 from step 1). It checks that the commit is on main, builds the bundle again from that commit in a job with no cloud access, checks the bytes, and only then, in a separate job that never runs code from this repository, checks the bundle's layout, signs it with the release key in Secret Manager and publishes it to the release store under the `device/` prefix.
3. The phone's updater (in the app) asks for device releases every 15 minutes, checks the size, SHA-256, signature and every file against `MANIFEST`, installs only while the app is idle, switches `/opt/callbox-device/current` and `/etc/inittab`, and restarts the phone. Without a heartbeat from the new boot within 5 minutes it switches back and restarts again.

A device release changes boot-time behaviour, so it is always applied by a restart. Raise `VERSION` with every change; a version already published with other bytes is refused.

## Building, Installing And Testing

*Each command runs from this directory.*

- `./test.sh` runs every test: the init entries, the scripts, the bundle, the stock tables and the screen timeout's KMS layouts.
- `./build-bundle.sh [DIR]` writes `DIR/callbox-device-<VERSION>.tar.gz` (default `dist/`), byte for byte the same for the same sources.
- `sh install.sh --root DIR` installs this release into an image being built (`../make-rootfs.sh` does this). `sh install.sh` on the phone, as root, is for recovery only; the app's updater installs device releases.

The image build installs this layer from the checkout, then the app from a signed app release (see `../../DISTRIBUTION.md`).
