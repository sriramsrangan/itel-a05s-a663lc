# Stock firmware (itel A05s / A663LC)

The exact stock ROM for this build is **`A663LC-V450AKLM-TGo-GL-240718V1056`**. The full
archive is about 7.1 GiB, too large for the git tree and for a single GitHub asset, so it is
published as split parts on the **Releases** page of this repository.

Source of record: the public GsmNex listing for this model. The archive is itel's proprietary
ROM and is re-hosted here only to recover this exact device.

## Download and reassemble

Download every `*.part-*` asset and the stock boot image from the latest release, then:

```sh
# Reassemble the full archive (parts are plain byte splits, in order)
cat A663LC-V450AKLM-TGo-GL-240718V1056.zip.part-* > A663LC-V450AKLM-TGo-GL-240718V1056.zip

# Verify (must match the digest in SHA256SUMS.txt)
shasum -a 256 -c SHA256SUMS.txt
```

## Checksums

Full archive, after reassembly:

```
a724b866ffbe34fd4f3e64232419a01ecd03e7894acdf50875e9c3d734d699bc  A663LC-V450AKLM-TGo-GL-240718V1056.zip
```

Split parts and the stock boot image are listed in `SHA256SUMS.txt` in this folder; the same
file is attached to the release.

## What the archive contains

The standard Unisoc PAC set for this model: `boot.img`, `vendor_boot.img`, `dtbo.img`, the
`vbmeta*` images, and the modem and DSP binaries (`SC9600_sharkl3_pubcp_modem.bin` and the
`sharkl3_*` files). The native Debian distribution in `debian/` reuses the modem and Wi-Fi
pieces from this archive. The stock `boot.img` is attached to the release on its own for
convenience, for returning the phone to a normal Android boot.
