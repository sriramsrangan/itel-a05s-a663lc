# Call Box Agent

*The on-phone software for the itel A05s running native Debian.*

This directory holds everything that runs on the call box phone once Android is gone: the Python package `callbox`, its systemd units, the networkd files for Wi-Fi with LTE fallback, the configuration templates and the installer. It implements the device side of `../CALLBOX_CONTRACT.md` and repeats, in Python, the bring-up that `../../initramfs/modem-test.sh` verified on hardware in test images t13 to t19. It was written and tested on a Mac against a simulated modem; nothing here has run on the phone yet.

## What Runs

*Seven units, started in order at boot.*

The Android modem tools run unchanged under bionic, then the VBC responder and the agent take over. One process, the agent, owns every AT channel; the command line talks to it through a root-only socket so nothing else touches the modem.

- `callbox-android-prep.service` (oneshot): builds `/dev/block/by-name` from sysfs, points every `*_b` name at the slot a partition, and restores `/dev/__properties__` from `/android/properties.tar.gz`.
- `callbox-modem-control.service`: `/android/bin/modem_control` with `LD_LIBRARY_PATH=/android/lib64` (bionic linker at `/system/bin/linker64`).
- `callbox-cp-diskserver.service`: `/android/bin/cp_diskserver`, started once `/dev/stty_lte2` exists; without it `AT+SFUN=2` fails.
- `callbox-refnotify.service`: `/android/bin/refnotify`, started once `/dev/stty_lte2` exists.
- `callbox-vbc.service`: `python3 -m callbox.vbc`, the VBC responder on `/dev/spipe_lte6`; it opens the pipe only after `/run/callbox/modem-at-ok` appears (the agent writes it when AT answers) and reopens a dead descriptor.
- `callbox-agent.service`: `python3 -m callbox.agent`, restarted by systemd whenever it exits.
- `callbox-wifi-modules.service` (oneshot): mounts `prodnv` at `/mnt/vendor`, loads `wcn_bsp.ko` then `sprd_wlan_combo.ko` from `/lib/modules-extra`, and waits for `wlan0`; a drop-in makes `wpa_supplicant@wlan0.service` wait for it.

## The Agent

*Modem, data, SMS, USSD, SIM toolkit, calls, heartbeats and jobs.*

The agent brings the modem up, keeps LTE data alive, forwards every SMS and long-polls hm-gateway for one call at a time. Each module below maps to one file in `callbox/`.

- `at.py`: AT channels on `/dev/stty_lte0` to `5` (raw, CR terminated, final results OK, CONNECT, ERROR, `+CME ERROR`, `+CMS ERROR`, NO CARRIER, BUSY, NO ANSWER, NO DIALTONE, or a timeout), one reader thread each, reopened when the descriptor dies. Known URCs (`+CMT`, `+CDS`, `+CUSD`, `+SPUSATPROCMDIND`, `^DSCI`, `^CONN`, `+CDUU`, `^CEND`, `+CEREG`, `+CIREG`, `+SPREGFAIL` and others) go to a bus from whichever channel they arrive on; channels 0 to 2 are SIM 1 and 3 to 5 are SIM 2.
- `modem.py`: bring-up as verified (`AT+SPTESTMODE=6,6`, the 45 vendor RIL commands in `ril-init.txt`, `AT+SFUN=2` on channels 1 and 4, `AT+SFUN=4` on channels 1 and 4, wait for `+CEREG` 1 or 5 on SIM 1, then SMS in PDU mode). Health every minute, never during a call: five minutes unregistered triggers a soft re-initialisation; AT silent on three checks makes the agent exit so systemd restarts it with a full bring-up.
- `data.py`: LTE on SIM 1 over channel 2 (`airtelgprs.com`), the address from the unquoted `a.b.c.d.m.m.m.m` field of `AT+CGCONTRDP` as a /32 on `seth_lte0`, default route metric 1000, DNS through `resolvectl`. A watchdog every 30 seconds reconnects after three failed checks.
- `sms.py` and `pdu.py`: PDU mode (`AT+CSMS=0`, `AT+CMGF=0`, `AT+CNMI=2,2,0,0,0`), a full decoder (GSM 7-bit with the extension table, UCS2 with surrogate pairs, 8-bit, concatenation with 8-bit and 16-bit references, class 0), reassembly kept on disk with a ten-minute timeout, a durable spool and retries to `POST /v1/sms/inbound`. Message text is never logged. `send_sms` sends only to the phone's own SIM numbers.
- `ussd.py` and `stk.py`: USSD with `AT+CUSD=1,"...",15` and network-initiated `+CUSD`; SIM toolkit commands answered with a terminal response. Both report metadata only to `/v1/callbox/events`.
- `forwarding.py`: query and change call forwarding with `AT+CCFC`, answering Airtel's consent prompt over USSD. Nothing calls it except `callbox forwarding`, which refuses any change without `--i-mean-it`.
- `calls.py`, `audio.py` and `dtmf.py`: `ATD<number>;` on channel 1, state from `AT+CLCC` on channel 2 plus call URCs, 30-second ring, the job's WAV played twice within 45 seconds, the downlink always recorded and scanned for DTMF with Goertzel, hang-up with `ATH`, then `AT+CHUP`, then `AT+SFUN=5` followed by a re-initialisation.
- `policy.py`: 08:00 to 21:00 IST, 120 calls a day, six a day to one number, 15 minutes apart, persistent ledger of number hashes; refusals end with outcome `refused`.
- `agent.py`: the job loop, results kept on disk until accepted, the heartbeat every 60 seconds in the HMAS shape and the control socket.

## Files And Settings

*Where everything lives on the phone.*

The installer puts the code under `/opt/callbox` and never overwrites settings that already exist. The device key is written only by provisioning.

- `/opt/callbox/callbox/`: the package, with `ril-init.txt` beside it.
- `/opt/callbox/sbin/`: `callbox-prepare-android.sh`, `callbox-wait-dev.sh`, `callbox-wifi-modules.sh` and the `callbox` wrapper (linked from `/usr/local/bin/callbox`).
- `/etc/systemd/system/`: the seven units and `wpa_supplicant@wlan0.service.d/callbox.conf`.
- `/etc/systemd/network/20-wlan0.network`: DHCP on `wlan0` with `RouteMetric=100`.
- `/etc/systemd/network/10-seth-lte.network`: keeps networkd away from `seth_lte*`.
- `/etc/tmpfiles.d/callbox.conf`: `/run/callbox` and `/var/lib/callbox`, mode 700.
- `/etc/callbox/agent.toml`: base URL, key file, `live` (false by default), caps, `transcribe`, SIM numbers, APN and the other settings listed in the template.
- `/etc/callbox/audio.toml`: mute commands, mixer commands before and after calls, playback and capture commands and rates, and `uplink_verified` (false by default).
- `/etc/callbox/device.key`: the hm-gateway device key, mode 600, never printed or logged.
- `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`: generated elsewhere; read by `wpa_supplicant@wlan0`.
- `/var/lib/callbox/`: `ledger.json`, `sms-spool/`, `sms-spool/rejected/`, `sms-parts/` and `pending-results/`, all mode 600.
- `/run/callbox/`: `modem-at-ok` and `control.sock`.

## Command Line

*Every command goes through the running agent.*

With `--direct` (agent stopped) a command opens the modem itself; `--bring-up` runs the full start-up first.

- `callbox status` prints the heartbeat with modem, SIM, data, Wi-Fi and spool details.
- `callbox sms send --sim 1 --to +919000000002 --text "test"` works only between the phone's own SIMs.
- `callbox ussd --sim 1 '*121#' [--then 1]` prints the reply on the terminal; logs and events carry no text.
- `callbox test-call 121 --dtmf 1` records the 121 menu, plays the tones into the uplink, records again, uploads both halves for transcripts and prints a verdict; any number other than 121 is refused.
- `callbox forwarding query|set|disable --sim 1 [--number ...] --i-mean-it` changes forwarding only with the flag.

## Decisions

*Choices made without the owner, overnight.*

Each of these can be changed in one place if the owner disagrees.

1. The agent is dry run unless `live = true`, as the Pi was, and live jobs end `failed` with detail "no speech" until `uplink_verified = true` in `audio.toml`; test jobs to 121 are exempt, because they are how the uplink is verified.
2. Calls to 121 are allowed only for kind `test`; they count towards the daily cap and quiet hours, but their own limits are 20 a day and two minutes apart so audio tests can be repeated.
3. The downlink of every answered call is analysed on the phone for DTMF; it is uploaded for a transcript only when `transcribe` allows (default: test calls only).
4. A job with a valid request id but invalid fields is answered `refused`, so every claimed job gets exactly one result.
5. Before each USSD the agent sets `AT+CSCS="IRA"` on the command channel, because the vendor RIL leaves channel 1 in HEX.
6. The VBC responder completes short reads up to a deadline instead of reopening at once; replies and the open-after-AT rule are as in `modem-test.sh`.
7. `ATE0` is sent on channels 1, 2, 4 and 5 after the RIL replay; echoed lines are ignored anyway.
8. ModemManager is masked by the installer, because it would open the AT channels.

## Unverified On Hardware

*Everything here passed against the simulator only.*

The bring-up, SMS, data and call control repeat verified shell steps; the items below were never seen on this handset.

- Uplink playback (`aplay -D hw:0,1`) and any mixer set-up it needs; downlink capture under Debian rather than Android.
- USSD character set handling, Airtel's forwarding consent prompt, class 0 SMS delivery and the SIM toolkit terminal response command `AT+SPUSATTERMINAL`.
- Which channel carries `+CMT`, `+CUSD` and call URCs under Debian (the agent listens on all six).
- The `^DSCI` state numbering beyond what t17 showed, and `AT+SFUN=5` as a hang-up fallback.
- systemd ordering for the bionic tools, the property area restore and `tar` in the real root filesystem.

## Tests

*Run on any machine with Python 3.11 or later, numpy, requests and pytest.*

The tests replace the modem with six pseudo-terminal pairs that answer like the SC9863A did, and hm-gateway with a local HTTP server.

- Run `python3 -m pytest -q tests` in this directory.
- `tests/fakemodem.py` simulates AT replies, URCs, calls, USSD, forwarding consent and `AT+CMGS`.
- PDU vectors include a published reference, hand-built UCS2, class 0, concatenated (8-bit and 16-bit references, a surrogate pair split across parts) and status reports.
