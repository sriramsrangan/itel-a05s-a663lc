# itel A05s native Debian and telephony handoff

Updated 19 November 2026 at 02:00 IST. The phone has not been seen since 01:16:51 (see Install Attempt). All working artifacts and backups are in this workspace. Start here, then read [device setup notes](debian-setup/README.md) and [the earlier unlock handoff](PHONE_UNLOCK_HANDOFF.md).

## Goal

*A native Debian phone that replaces Android and takes over the Raspberry Pi.*

The handset is to boot headless Debian directly, with Android removed, and take over everything the Pi `homebridge` runs, so Android Go's background killing no longer matters. Debian must place outbound voice calls, send and receive cellular SMS, handle flash messages (class 0 SMS and network-initiated USSD), run mobile data, and keep forwarding incoming calls to Dileep at `+919000000000`. Offload the internal API and queue to the `your-gateway-project` project where feasible; the phone keeps the cellular interface.

- Airtel sees only the modem (same IMEI and SIM under any operating system). Registration, SMS, data and network call forwarding do not depend on Android.
- Outbound VoLTE calls, SMS both ways, LTE data and Wi-Fi with LTE fallback are now verified without Android (below). Call audio for spoken messages is the main open risk.
- Call forwarding on the SIM in slot 1 already works (user, 18 November 2026). Airtel requires consent on a flash prompt to change forwarding; the 12 November 2026 attempts failed for lack of it. Debian needs a forwarding control that sends the code, answers the prompt and checks the status.

## Overnight Task List

*Work for 18 and 19 November 2026; status is updated as each item closes.*

The interfaces between the parties are fixed in [the call box contract](debian-setup/native/rootfs/CALLBOX_CONTRACT.md). Three parallel agents write the code. Only this session drives the phone, so all hardware work stays here. The user also asked that the distribution be preserved for a second A05s of the same model: every build is scripted, and built images are kept with checksums.

1. Helix-messaging call box and SMS endpoints, device key in Secret Manager. Status: done; PRs #22 and #23 merged, `hm-gateway` revision 00040 deployed, secret `callbox-device-key`.
2. HMAS switch to your-gateway-project `/v1/calls`. Status: done; PR #783 merged and deployed. Rollback: Firestore `Configuration/callbox.transport` = `pubsub`. HMAS sends nothing while `CallBox/status` says offline.
3. Phone software in `debian-setup/native/rootfs/callbox-agent/`. Status: written, 82 tests pass against a simulated modem; hardware run pending.
4. Debian trixie arm64 base root filesystem built with `mmdebstrap` in a Lima VM (`debian-setup/native/rootfs/build-base.sh`). Status: done, 123 MB tarball, SHA-256 `49c6326141763a1cbc68f0ec580ce8c6216888fdd1b13d8d32c844dff49545ae`.
5. Uplink audio research in Android before it is removed. Status: done; injection reaches the uplink mixer, network leg unproven (see Call Audio Findings).
6. Boot image that switches root to Debian, proven first from slot b with the root filesystem in RAM. Status: done; see Verified Debian Userland.
7. Reformat userdata, install Debian, move the production image to slot a, keep a known-good image in slot b. Status: first attempt at 01:16 lost contact (see Install Attempt); redesigned install ready (p2).
8. End-to-end tests: Wi-Fi with LTE fallback, SSH, NTP, reboot survival, SMS between the phone's own SIMs, the 121 call with DTMF audio, a server-triggered `kind: test` call to 121. Status: to do.
9. Phone-vault code in git: PR #2 merged to `main` as `35c1e03` on 19 November 2026 (the repository has no CI). Firmware, device binaries, backups, keys and recordings stay excluded.
10. Preserve the distribution for the second phone: images, root filesystem and checksums in `debian-setup/native/dist/`, with install steps. Status: to do.

## Handset And SIMs

*This handset is the HMAS call box phone.*

- itel A05s `A663LC-GL`, serial `YOURSERIAL`, SC9863A (sharkl3), eMMC, 3.9 GB RAM, Android 13 Go build `A663LC-V450AKLM-TGo-GL-240718V1056`, kernel `5.4.210-android12-9-g8924705f525f-ab676`. Bootloader unlocked; Magisk 31.1 root on `boot_a`.
- Slot 1 is Airtel `+91 74838 00020`: the HMAS calling SIM, the ISPF WhatsApp Business number in Helix Messaging, and the user's choice for forwarding and SMS. Slot 2 is Airtel `+91 70224 69343`.
- HMAS `CallBox/status` has shown `online: false, adbProblem: no device` since its last call at 09:18 IST on 18 November 2026, when this phone was moved to the Mac and factory reset. The Bluetooth pairing with `homebridge` is gone. Escalation calls are offline until the phone is back in service.
- `+91 70224 69343`: one recharge in the 17 November backup, ₹299 on 13 January 2026; last connected call 30 January 2026. The user recharged it on 18 November 2026. TRAI rules (Telecom Consumers Protection Regulations, 2012, regulations 2(aa), 11, 12 and 14): no deactivation for 90 days after the last call, outgoing SMS or data session; then, while the balance exceeds ₹20, up to ₹20 per further 30 days. Incoming SMS does not count; an answered incoming call does.

## What The Pi Runs

*Everything below moves to the phone once Debian is in service.*

- `hmas-callbox` 0.3.0 and the `hmas-callbox-phone` timer (ADB dialling, Bluetooth hands-free audio). Runbook: `~/code/helix-meeting-automation-system/ops/hmas-callbox/README.md`.
- Homebridge with the Atomberg Fan and Panasonic MirAIe AC plugins; nginx.
- User service `sms-forward` (`~/sms-forward/sms_forward.py`).
- Cron every five minutes: `~/helix-network/run.sh` (network monitor) and `~/helix-network/daylight.sh` (office lamp).
- ModemManager is running. Phone backups from 12 and 17 November 2026 are in `~/phone-backup-itel-2026-11-1*/` on the Pi (SMS, call log, contacts, settings).
- Reach it as `homebridge.local` (192.168.0.X) over SSH; `sudo` needs a password.

## Verified Native Boot

*The stock kernel runs our own ramdisk from slot b, and Android recovers on its own.*

- `fastboot boot` does not work on this bootloader: it accepts the upload, fails the boot command and starts stock Android. Test images are flashed to the unused slot b instead.
- Slot b was an older, non-bootable build (every slot b image differed; super metadata marks it the virtual A/B source). Originals of `boot_b`, `vendor_boot_b`, `dtbo_b`, `vbmeta_b` and `misc` are in `backups/itel-a663lc-YOURSERIAL/slot-b-before-native-2026-11-18/` with `SHA256SUMS.txt`. Slot b now holds the stock `vendor_boot`, `dtbo` and `vbmeta`, plus the current test image in `boot_b`.
- Test image t5 (`debian-setup/native/out/test-boot-t5.img`, SHA-256 `059cdd5e2c00e2dbd4b0a6b74c9ede7e3db60248c7ee1813954363a19b312775`): stock kernel, header v4, ramdisk with Debian trixie `busybox-static` 1.37.0-6+b9 and `debian-setup/native/initramfs/init`. The stock vendor ramdisk supplies the modules.
- Proof of execution on 18 November 2026: the Mac enumerated "itel A663LC native test" with serial `PVDEBIAN-t5`, and after the return to Android `/sys/fs/pstore/pmsg-ramoops-0` held the init's own log (`debian-setup/native/out/pstore-t5.txt`).
- In t5: all 95 recovery modules loaded (the list is not in dependency order, so the init retries in passes); the eMMC came up with all 68 partitions; userdata read back as ciphertext (metadata encryption), so Debian must reformat it, not reuse it; `/dev/modem`, `/dev/spipe_lte*`, `sdiag_lte` and `slog_lte` exist.
- Recovery: the init writes the saved slot-a boot control block (`debian-setup/native/initramfs/bcab-slot-a.bin`, 32 bytes at offset 2048 of `misc`) back as its first eMMC write, so any reboot returns to Android. Verified in t5. The bootloader's A/B retry count also returned to Android on its own after t2, t3 and t4, when that step failed. Last resort: the BootROM download route in the unlock handoff.
- To run a test: `debian-setup/native/build-test-boot.sh <id>`, write `out/test-boot-<id>.img` to `boot_b` from rooted Android with `dd` and verify the hash, then `debian-setup/native/run-slot-b-test.sh <id>`. Afterwards read `/sys/fs/pstore/pmsg-ramoops-0` and check that `misc` offset 2048 starts `_a`.

## Verified Modem Bring-Up

*Test image t13 registers SIM 1 on Airtel LTE with IMS, without Android.*

- Android's own `modem_control`, `cp_diskserver` and `refnotify` run unchanged under bionic from the test ramdisk: `/system/bin/linker64`, 22 libraries in `/android/lib64` (copied from this handset by `debian-setup/native/resolve-android-libs.py`) and a read-only copy of Android's property area. Script: `debian-setup/native/initramfs/modem-test.sh`.
- `modem_control` loads and verifies the modem through Trusty and starts `pm_sys`; that also feeds the power-chip watchdog, which ended the reset at about 285 seconds (t6 ran to its 540-second mark).
- `cp_diskserver` (NV item service on `spipe_lte1`) must run; without it `AT+SFUN=2` returns `+CME ERROR: 3`. Android starts it through `ctl.start`.
- Booting from slot b makes the tools use slot b's older modem and NV, which carry a placeholder IMEI; Airtel then rejects with cause 6 (illegal ME) and gives emergency service only. The test links every `*_b` partition name to the slot a partition. With that, the IMEI matches Android's (by hash).
- AT channels: `stty_lte0` to `stty_lte2` serve SIM 1 (0 carries unsolicited events), `stty_lte3` to `stty_lte5` serve SIM 2. Sequence: replay the vendor RIL's 45 start-up commands (`debian-setup/native/initramfs/ril-init.txt`, from an `strace` of `urild` on 18 November 2026; `AT+SPASENGMD` fails on Android too), `AT+SPTESTMODE=6,6`, `AT+SFUN=2` on channels 1 and 4, then `AT+SFUN=4` on channel 1.
- Result on 18 November 2026: both SIMs `CPIN: READY`, SIM 1 `+CEREG: 2,1` and `+CREG: 2,1` on LTE, Airtel 40445, `+CIREG: 2,1` (IMS registered). The IMS stack runs in the modem, so VoLTE calls may work over plain AT.
- Android lab method (seconds per experiment): `setprop ctl.stop vendor.ril-daemon`, send AT with `/data/local/tmp/atlab.sh`, then `setprop ctl.start vendor.ril-daemon`. If the SIMs were switched off, reboot Android to restore them.

## Verified Telephony And Data

*Test images t14 to t17 used SIM 1 with no Android running.*

The modem's own IMS stack carries VoLTE, so plain AT commands place calls once one small audio control service is answered. SMS and LTE data need nothing from Android beyond the modem bring-up above.

- SMS: text mode (`AT+CSMS=0`, `AT+CMGF=1`, `AT+CNMI=2,2,0,0,0`, `AT+CMGS`). Messages went both ways between the handset's two SIMs on 18 November 2026 at 20:26:55 and 20:28:06 IST, and each arrived as an unsolicited `+CMT`.
- Data: `AT+CGDCONT=1,"IP","airtelgprs.com",...`, `AT+CGACT=1,1`, `AT+CGDATA="M-ETHER",1`, then the address from `AT+CGCONTRDP` (unquoted, `a.b.c.d.m.m.m.m`) on `seth_lte0`. Ping 0% loss, DNS and HTTP 204 passed. HTTPS is untested only because busybox `wget` has no TLS.
- Calls: `ATD<number>;` on `stty_lte1`, state from `AT+CLCC` on `stty_lte2` plus `^DSCI`, `^CONN`, `+CDUU: "ANSWERED"` and `^CEND`, hang-up with `ATH`. In t17, 121 connected in 6 seconds; the approved call to +91 98807 10471 alerted for 18 seconds, was answered and was hung up by the script within 2 seconds, as the user confirmed.
- Calls need a responder on `/dev/spipe_lte6` (the VBC audio control protocol Android's audio HAL serves). Without it `ATD` freezes every AT channel. Messages are `VBC\0`, a 32-bit command and a 32-bit total length; the reply is command plus 1 (11 takes a fixed 12-byte reply, 1 takes battery voltage in mV, 14 replies 14 with 26). The responder must open the pipe after the modem answers AT, and reopen it if the descriptor dies. Implementation: `vbc_responder` in `debian-setup/native/initramfs/modem-test.sh`.
- Calls are switched off in the test script unless `/test-calls` exists in the ramdisk, so test runs make no calls by default.

## Verified Wi-Fi

*Test image t19 joined the house Wi-Fi and fell back to LTE when Wi-Fi went down.*

The integrated WCN chip works with Android's own modules and firmware. It is 2.4 GHz only: every network seen was on channels 1 to 8.

- Pieces: `wcn_bsp.ko` and `sprd_wlan_combo.ko` from vendor_dlkm; firmware in `/lib/firmware` (`wcnmodem.bin`, `gpsgl.bin`, `gpsbd.bin` and the four `connectivity_*.ini` files from `/odm/firmware`, kept in `debian-setup/native/android-modem/wcn-fw/`); `prodnv` mounted at `/mnt/vendor` for the calibration backup and Bluetooth data; Alpine 3.22 `wpa_supplicant` 2.11 and `iw` 6.9 under the musl loader.
- t18 lacked the GNSS and calibration files, and `wlan0` never appeared. With them, in t19, `wlan0` appeared 9 seconds after loading the modules, the scan found 10 networks, and the handset joined Second Floor (WPA2) in 1 second.
- Routes: Wi-Fi default route metric 100, LTE metric 1000. Ping and HTTP 204 went over Wi-Fi; with `wlan0` down, HTTP 204 went over `seth_lte0`.
- Passwords come from the Mac Keychain (service `your-wifi-keychain`) at build time; `debian-setup/native/make-wpa-conf.py` writes only derived keys into the image. The image and build directory are excluded from git. Only Second Floor is confirmed. First Floor and Ground Floor entries use the MLO passwords as guesses, and those floors broadcast "First Floor (Legacy)" and "Ground Floor MLO", which are not yet configured.

## Verified Debian Userland

*Debian trixie runs on the handset from RAM, booted from slot b, with every call box service.*

The stock kernel lacks `devtmpfs` and `CONFIG_FHANDLE`, so systemd and udev cannot run (systemd froze, then the power-chip watchdog reset the phone). Decision, 19 November 2026: the call box uses busybox `init` with `/etc/inittab`, `mdev -df` for device nodes (the modem channels appear only after the modem starts), and a small wrapper (`callbox-svc`) that logs each service to syslog and restarts it. Services: `mdev`, `syslogd`, `klogd`, `modem_control`, `cp_diskserver`, `refnotify`, the VBC responder, the call box agent, `wpa_supplicant`, `udhcpc` on `wlan0`, a Wi-Fi route watcher, `sshd` and `chronyd`. Files: `debian-setup/native/rootfs/extra/`.

- Boot chain: `build-debian-boot.sh` (stock kernel, ramdisk from `initramfs/init-debian`). Modes `ram` (fetch the root filesystem over Wi-Fi into RAM), `install` (as `ram`, then Debian copies itself to userdata) and `disk` (mount userdata). The ramdisk hands Debian a tmpfs `/dev` filled by `mdev`, then runs `switch_root` to busybox `init`.
- Root filesystem: `rootfs/build-base.sh` (mmdebstrap in the Lima VM) and `rootfs/make-rootfs.sh` (adds the Android modem pieces, Wi-Fi firmware, the agent, SSH key, Wi-Fi keys and the device key). Overlay files must go under `usr/lib`, not `lib`, or they replace Debian's `/lib` symlink and `/sbin/init` cannot load.
- Results on 19 November 2026 (root filesystem r5, RAM boot from slot b): SSH over Wi-Fi with the Mac key; both SIMs registered (home), IMS on SIM 1; LTE data on `seth_lte0` (HTTP 204, ping); NTP in sync through chrony (12 ms); uptime over 27 minutes with no watchdog reset.
- Fallback test: with `wlan0` down, HTTP 204, ping, DNS and HTTPS all went over LTE. The default Wi-Fi route did not return by itself after the link came back (udhcpc restores it only at renewal); `callbox-wifi-route` now puts it back within seconds.
- SMS: SIM 2 sent to SIM 1 at 01:00:56; SIM 1 received it 4 seconds later; your-gateway-project stored metadata only (SIM, sender, time, length, keyed hash) and emailed the text at 01:01:02.
- Fixes found on hardware: the agent's LTE probe used TCP port 53, which Airtel drops, so it tore down a working link every two minutes, and the reactivated context then carried no traffic. The probe is now an HTTP 204 check, and the agent reconnects only when the context is down or after ten failed probes. This modem ends the SMS prompt with a carriage return (`\r\n> \r`); the agent now accepts it. DNS stays on public resolvers (1.1.1.1, 8.8.8.8), which answer over both links.
- Calls are not yet tested under Debian: the agent allows calls only from 08:00 to 21:00 IST, including tests.
- Security: at 00:10 the device key was printed into this session's terminal output by a mistaken redirection. It was revoked in your-gateway-project, Secret Manager version 1 was disabled, and a new key (version 2) was issued straight into Secret Manager and checked (HTTP 204).

## Install Attempt

*The first disk install lost contact with the phone.*

At 01:16 the install image (p1) booted from slot b and fetched the root filesystem at 01:16:51. Nothing has been seen since: no SSH, no heartbeat, nothing on USB. The likely cause is the power-chip watchdog: that installer formatted userdata and unpacked to the eMMC before Debian started `modem_control`, which is what feeds the watchdog, so a slow unpack would reset the phone after about 285 seconds. After a reset the bootloader would start Android on slot a, whose userdata is now ext4.

- Redesign (p2): the install image boots exactly like the RAM test, and Debian itself formats userdata and copies itself across (`callbox-install-to-disk`) once the modem services run.
- If the phone does not come back, it needs a person: hold volume down and power for fastboot, then `fastboot --set-active=b` with `debian-boot-p2-install.img` in `boot_b` (or reflash the stock or Magisk `boot_a` and `fastboot -w` for Android).
- All 277 original unlock backups, the five slot b originals and the firmware archive verified before the install (19 November 2026, 01:05).

## Morning Recovery

*How the boot-looping phone was brought back on 19 November 2026.*

The phone was found in a boot loop on slot a. After the reset during the p1 install, the bootloader started Android on a userdata that was now partly written ext4. Android could not mount it and never reached recovery, and the key combinations for recovery and fastboot did nothing. The only way in left was Unisoc BootROM download mode (Volume Down while connecting USB). From there the plan was to write the p2 install image to `boot_a`.

- 07:30: `spd-bootrom ... write_part boot_a debian-boot-p2-install.img` loaded FDL1 and FDL2, then START_DATA got 0x89 (`BSL_REP_DOWN_DEST_ERROR`). Nothing was written. The kit tool returns quietly on a refused start, so the next command then timed out. The image is exactly the partition size (0x4000000), so later attempts used a copy trimmed to its content, rounded to 4 KB (`debian-boot-p2-install-trim.img`, 0x2d6e000 bytes, SHA-256 `16b35294350db12e…`).
- 07:35 to 07:41: three more attempts. One device did not answer its first control transfer, in one FDL2 did not answer after EXEC, and one was still running when it was stopped. Nothing was written.
- Decision: the owner ran the write from the Codex session that built the recovery kit. The trimmed install image was written to `boot_a` and read back for verification. The working loader sequence is to be recorded here for the second handset.
- 08:00:55: the p2 install image booted from slot a, fetched the root filesystem over Wi-Fi (phone at 192.168.0.X) and answered SSH at 08:03 with `modem_control` running and LTE up. `callbox-install-to-disk` reported "DONE: userdata holds Debian (424 MB)". A read-only check of userdata showed the full tree, `inittab` and busybox present, and neither the RAM-test flag nor the install flag copied across.
- The pstore record from before that boot (`build/pstore-0803/`, bit-damaged but readable) holds the p1 messages: "formatting userdata as ext4" then "FAILED: mke2fs: Error relocating ... libblkid.so.1: econf_readConfig: symbol not found". The Alpine mke2fs in the p1 ramdisk could not load its libraries, so p1 stopped at the format step and its fail step rebooted the phone. This corrects the watchdog explanation in Install Attempt. Why Android then looped on slot a is not shown by the record.
- Caution: in Debian, `/dev/block/by-name/boot_b` points at `boot_a` (`mmcblk0p34`), like every `_b` name, for the modem tools. Write boot images by partition number from sysfs PARTNAME: `boot_a` is `mmcblk0p34`, `boot_b` is `mmcblk0p35`, `misc` is `mmcblk0p3`, `userdata` is `mmcblk0p68`.
- 08:05: SSH to 192.168.0.X stopped answering before the disk image was written to `boot_a`. Codex had written the p2 disk image and reset the phone. Debian booted from userdata (heartbeats over LTE from 08:08) but was not found on Wi-Fi.
- p3 disk image (`debian-boot-p3-disk.img`, trimmed copy SHA-256 `4d8aa5355e26b431…`): in disk mode the ramdisk now loads `wcn_bsp` and `sprd_wlan_combo` with prodnv mounted read-only, as the RAM boot does, and logs a missing `wlan0` instead of failing. Codex wrote it to `boot_a` (read back over SSH: match). It booted at 08:16 on Wi-Fi. Codex's own record of its steps is in `DEBIAN_FLASH_2026-11-19.md`. It wrote the p2 disk image over SSH at about 08:05 and then looked for the phone only at 192.168.0.X. In the p2 disk boot's log `wlan0` came up, so that boot most likely had Wi-Fi at an address nobody checked (see the MAC entry below). The p3 change is kept as a harmless safeguard, but it is not proven to have been needed.
- Real cause of the "no Wi-Fi" boots: the Wi-Fi driver invents a new MAC on every boot (prodnv has no `wifimac.txt`), so each boot got a new address (.24, .84, then .9), and the phone was being looked for at the old one. Fix: `/etc/callbox/wifi-mac` (`02:00:00:00:00:01`) is applied by `callbox-sysinit` before `wpa_supplicant` starts. A reboot at 08:29:46 returned on that MAC at 192.168.0.X in 70 seconds. The main router (Archer BE400) now reserves 192.168.0.X for it. The router's library call fails on this firmware. What works is the web UI's own request: AES-encrypted JSON `{"operation":"insert","new":[{mac, ip, comment, enable}],"index":0}` posted as a JSON body to `admin/dhcps?form=reservation`. A second handset needs its own MAC in `wifi-mac`.
- 08:20, over SSH, each backed up first and read back after writing: `boot_b` (`mmcblk0p35`, previously the p1 installer, saved to `build/boot_b-before-0820.img`) now holds `debian-boot-p2-ram.img` (`c92001f4…`). `logo` (`mmcblk0p11`) now shows the Debian open use logo in place of the itel screen. It was built from the phone's own logo container (six gzip BMPs, 720 by 1612, 24-bit), changing only `4.bmp`. The original is in `debian-setup/firmware/logo/logo-partition-phone-backup.bin`, and the bootloader shows the new screen.
- Mistake caught: a copy over SSH sent an empty `callbox-sysinit` that still passed `sh -n` and replaced the original. It was restored before any reboot and checked by size, content and MD5. Copies to the phone now check the byte count before replacing a file.

## Call Audio Findings

*Speech injection reaches the modem's uplink mixer; the network leg still needs a listener.*

The modem sound card (`saudiolte`, whose card number changes between boots, so address it by name) has its own mixer with eight controls: `PCM PLAYBACK Route`, `PCM CAPTURE Route` (0 downlink, 1 uplink, 2 both), `PCM LOOP Enable`, `PCM LOOP Type`, `PCM MIXER Route` (0 to 2), `PCM APPTYPE`, `UL MIXERGAIN` and `DL MIXERGAIN` (0 to 16534). Results from 121 calls in the Android lab on 18 November 2026, with the vendor RIL stopped:

- Downlink: `hw:CARD=saudiolte,DEV=0` capture at 8000 Hz with `PCM CAPTURE Route` 0 records the far end; Cloud Speech-to-Text (en-IN) transcribes the 121 menu word for word.
- Uplink injection: with `PCM MIXER Route` 2 and `UL MIXERGAIN` 16384, audio written to `hw:CARD=saudiolte,DEV=1` (or `DEV=0`) appears in the uplink capture (`PCM CAPTURE Route` 1) at full level. Device 2 does not. The devices run at 16000 Hz: an 8000 Hz file plays at double speed, so feed 16000 Hz mono.
- Starting playback ends a capture already running (read error); restart the capture after playback starts.
- 121 accepts keys sent with `AT+VTS` (pressing 1 at 30 seconds opened the postpaid menu) but ignores DTMF played as audio, as VoLTE menus commonly do. So 121 cannot prove that injected speech reaches the far end. That proof needs a listener on a real call. Until then HMAS calls from the phone are flagged as possibly silent.
- Menu keys for interactive polls therefore arrive from callers as in-band tones only if the network passes them; the agent detects both in-band tones (Goertzel on the downlink) and speech ("say 2") through speech-to-text.
- The vendor HAL also sends `AT+SSAM`, `AT+CMUT` (uplink mute), `AT+SPAPAUDMODE` and `AT+SPVLOOP`; none were needed for these results.
- Scripts and recordings: `debian-setup/native/audio-lab/uplab2.sh` and `audio-lab/uplab/` (private, not in git).

## Open Problems

*Each needs a hardware test before Android is replaced.*

1. Call audio: uplink injection for text-to-speech and interactive polls ("press 1 or say 1"), and downlink capture under Debian for speech-to-text and DTMF.
2. USSD and Airtel's forwarding consent prompt (`AT+CUSD`), class 0 SMS and SIM toolkit display text (`+SPUSATPROCMDIND`): not yet tested.
3. Remote access: this kernel has no NCM, ECM or ACM gadget functions, only vendor RNDIS (macOS has no driver), generic serial and FunctionFS. Now that Wi-Fi works, SSH over Wi-Fi is the simplest route; `adbd` over FunctionFS is the fallback. Until then, results come back through pmsg after a reboot.
4. Debian root filesystem: not built. No `debootstrap` on the Mac or in Homebrew. userdata (56 GB) becomes the root once Android is replaced; `cache` (64 MB) is unused. Needs systemd units for the modem stack, the VBC responder and Wi-Fi, and NTP.
5. HMAS: replace the ADB dial path with AT dialling and events, and move the service, Homebridge, `sms-forward` and the cron jobs from the Pi.

## Firmware

*The exact stock archive is the recovery source of record.*

- `debian-setup/firmware/A663LC-V450AKLM-TGo-GL-240718V1056.zip`, 7,620,577,250 bytes, SHA-256 `a724b866ffbe34fd4f3e64232419a01ecd03e7894acdf50875e9c3d734d699bc`, from the [GsmNex listing](https://gsmnex.com/index.php?a=downloads&b=file&id=961). Extracted `boot.img`, `vendor_boot.img`, `dtbo.img`, the vbmeta set and modem images are beside it; the firmware's vbmeta digest matches the handset's.
- `magisk-patched-boot.img` (SHA-256 `df301f66d538cde84e370dc7d824a7534bdf9e71f7079654e0658824951db38c`) is what `boot_a` holds.
- The original unlock backups still verify against `PHONE_UNLOCK_SHA256SUMS.txt`. Keep backups private; the firmware directory is excluded through `.git/info/exclude`.
