# Call Box Contract

*What the device layer and the call box app may rely on from each other on the phone.*

The phone runs two separately released, separately signed bundles. The device layer (`callbox-device/`, this repository) owns boot and hardware; the call box app (`apps/callbox-agent` in helix-messaging, with its own contract for the gateway) owns jobs, calls, SMS, recordings and updates. This file fixes the seam between them. A change to anything below needs a release of both sides, in the order given under Changing The Seam.

## Paths

*Each side writes only its own.*

The device layer writes only these paths, and the app only reads them.

- `/opt/callbox-device/releases/<version>/` and `/opt/callbox-device/current` (a link to one release).
- `/etc/inittab`: the device release's `inittab`. The previous one is kept as `/etc/inittab.callbox-previous`.
- `/etc/callbox/device`: a link to `/opt/callbox-device/current/etc/callbox/device`, holding `audio.toml` (the hardware audio recipe) and `audio_params/` (`audiodsp`, `cvs`, `audioarm`, `dspcodec` and `SHA256SUMS`).

The app writes only `/opt/callbox`, `/var/lib/callbox`, `/run/callbox` and its own settings in `/etc/callbox` (`agent.toml`, `audio.toml`, `release.pub`, `device.key`). Its updater is the one process that installs device releases, so it also switches `/opt/callbox-device/current`, `/etc/callbox/device` and `/etc/inittab`, always from a signed device bundle.

## Services

*Started by the device layer, supplied by the app.*

- The device `inittab` starts the app's services as `callbox-svc vbc /usr/bin/python3 -m callbox.vbc`, `callbox-svc agent /usr/bin/python3 -m callbox.agent` and `callbox-svc updater /usr/bin/python3 -m callbox.updater`. `callbox-svc` sets `PYTHONPATH=/opt/callbox/current`.
- It also starts `mdev`, `syslogd`, `klogd`, `modem-control`, `cp-diskserver`, `refnotify`, `wpa`, `dhcp-wlan0`, `wifi-route`, `sshd`, `chronyd` and `screen-timeout`. The app's updater refuses a device bundle whose `inittab` lacks any of these, or a `sysinit` or `shutdown` entry.
- The modem's AT channels (`/dev/stty_lte0` to `5`) and VBC pipes (`/dev/spipe_lte4` and `6`) belong to the app once the modem tools have started.

## Audio Recipe

*Hardware facts in the device layer, behaviour in the app.*

`/etc/callbox/device/audio.toml` holds only these keys: `mute`, `before_call`, `after_call`, `on_answer` (with `{route}`), `routes` (per call type `volte-wb`, `volte` and `cs`), `voice_pcm`, `voice_pcm_rate`, `voice_pcm_owner`, `playback`, `playback_rate`, `playback_channels`, `capture` and `capture_rate`. The app's own settings (`uplink_verified`, `capture_stall_seconds` and `call_type_wait_seconds`) are in `/etc/callbox/audio.toml`; any device key in that file is ignored while `/etc/callbox/device/audio.toml` exists.

## Versions And Heartbeats

*How the gateway sees both bundles.*

- Both bundles have a `VERSION` (`x.y.z`). The device bundle also has `KIND` (`callbox-device`).
- The app reports its own version as `agentVersion` (`debian-<version>`) and the device layer's as `deviceVersion` in every heartbeat, and writes both to `/run/callbox/heartbeat-ok` when the gateway accepts one. The updater's 5-minute watchdog reads that file for either kind of release.

## Changing The Seam

*Device first when the app needs something new, app first when the device stops providing something.*

1. A new path, key or service the app will use: release the device layer with it first, then the app that reads it, falling back while it is absent.
2. Something the device layer will stop providing: release the app that no longer needs it first, then the device layer without it.
