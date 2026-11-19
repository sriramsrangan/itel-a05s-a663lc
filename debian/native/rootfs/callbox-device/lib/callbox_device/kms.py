"""Minimal DRM/KMS client over raw ioctls on /dev/dri/card0 (no libdrm, no compiler on the phone).

Why: the itel A05s panel (icnl9916, sprd_drm) has its backlight set by DSI commands from the panel
driver. With no KMS client the display pipeline is never initialised by Debian ("dpu is not
initialized"), the panel is left in the state the bootloader put it in, and the kernel's brightness
and bl_power writes go nowhere. Becoming DRM master and setting a mode makes the kernel bring the
DPU, the DSI host and the panel up properly; after that the connector's DPMS property turns the
panel off (display off, sleep in, backlight off) and on again.

Framebuffers are owned by the open file: if the process exits, the kernel removes the framebuffer
and the screen goes dark. So the caller keeps one Display object for its whole life.
"""
import ctypes
import fcntl
import mmap
import os
import struct

DRM_IOCTL_BASE = ord("d")
DPMS_ON, DPMS_OFF = 0, 3
DRM_MODE_CONNECTED = 1
DRM_MODE_TYPE_PREFERRED = 1 << 3


def _iowr(nr, size):
    return (3 << 30) | (size << 16) | (DRM_IOCTL_BASE << 8) | nr


def _io(nr):
    return (DRM_IOCTL_BASE << 8) | nr


class ModeInfo(ctypes.Structure):
    _fields_ = [
        ("clock", ctypes.c_uint32),
        ("hdisplay", ctypes.c_uint16), ("hsync_start", ctypes.c_uint16),
        ("hsync_end", ctypes.c_uint16), ("htotal", ctypes.c_uint16), ("hskew", ctypes.c_uint16),
        ("vdisplay", ctypes.c_uint16), ("vsync_start", ctypes.c_uint16),
        ("vsync_end", ctypes.c_uint16), ("vtotal", ctypes.c_uint16), ("vscan", ctypes.c_uint16),
        ("vrefresh", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("type", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
    ]


class CardRes(ctypes.Structure):
    _fields_ = [
        ("fb_id_ptr", ctypes.c_uint64), ("crtc_id_ptr", ctypes.c_uint64),
        ("connector_id_ptr", ctypes.c_uint64), ("encoder_id_ptr", ctypes.c_uint64),
        ("count_fbs", ctypes.c_uint32), ("count_crtcs", ctypes.c_uint32),
        ("count_connectors", ctypes.c_uint32), ("count_encoders", ctypes.c_uint32),
        ("min_width", ctypes.c_uint32), ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32), ("max_height", ctypes.c_uint32),
    ]


class GetConnector(ctypes.Structure):
    _fields_ = [
        ("encoders_ptr", ctypes.c_uint64), ("modes_ptr", ctypes.c_uint64),
        ("props_ptr", ctypes.c_uint64), ("prop_values_ptr", ctypes.c_uint64),
        ("count_modes", ctypes.c_uint32), ("count_props", ctypes.c_uint32),
        ("count_encoders", ctypes.c_uint32), ("encoder_id", ctypes.c_uint32),
        ("connector_id", ctypes.c_uint32), ("connector_type", ctypes.c_uint32),
        ("connector_type_id", ctypes.c_uint32), ("connection", ctypes.c_uint32),
        ("mm_width", ctypes.c_uint32), ("mm_height", ctypes.c_uint32),
        ("subpixel", ctypes.c_uint32), ("pad", ctypes.c_uint32),
    ]


class GetEncoder(ctypes.Structure):
    _fields_ = [
        ("encoder_id", ctypes.c_uint32), ("encoder_type", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32), ("possible_crtcs", ctypes.c_uint32),
        ("possible_clones", ctypes.c_uint32),
    ]


class Crtc(ctypes.Structure):
    _fields_ = [
        ("set_connectors_ptr", ctypes.c_uint64), ("count_connectors", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32), ("fb_id", ctypes.c_uint32),
        ("x", ctypes.c_uint32), ("y", ctypes.c_uint32), ("gamma_size", ctypes.c_uint32),
        ("mode_valid", ctypes.c_uint32), ("mode", ModeInfo),
    ]


class CreateDumb(ctypes.Structure):
    _fields_ = [
        ("height", ctypes.c_uint32), ("width", ctypes.c_uint32), ("bpp", ctypes.c_uint32),
        ("flags", ctypes.c_uint32), ("handle", ctypes.c_uint32), ("pitch", ctypes.c_uint32),
        ("size", ctypes.c_uint64),
    ]


class MapDumb(ctypes.Structure):
    _fields_ = [("handle", ctypes.c_uint32), ("pad", ctypes.c_uint32), ("offset", ctypes.c_uint64)]


class FbCmd(ctypes.Structure):
    _fields_ = [
        ("fb_id", ctypes.c_uint32), ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
        ("pitch", ctypes.c_uint32), ("bpp", ctypes.c_uint32), ("depth", ctypes.c_uint32),
        ("handle", ctypes.c_uint32),
    ]


class GetProperty(ctypes.Structure):
    _fields_ = [
        ("values_ptr", ctypes.c_uint64), ("enum_blob_ptr", ctypes.c_uint64),
        ("prop_id", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("name", ctypes.c_char * 32),
        ("count_values", ctypes.c_uint32), ("count_enum_blobs", ctypes.c_uint32),
    ]


class ConnectorSetProperty(ctypes.Structure):
    _fields_ = [("value", ctypes.c_uint64), ("prop_id", ctypes.c_uint32),
                ("connector_id", ctypes.c_uint32)]


IOCTL_SET_MASTER = _io(0x1E)
IOCTL_DROP_MASTER = _io(0x1F)
IOCTL_MODE_GETRESOURCES = _iowr(0xA0, ctypes.sizeof(CardRes))
IOCTL_MODE_SETCRTC = _iowr(0xA2, ctypes.sizeof(Crtc))
IOCTL_MODE_GETENCODER = _iowr(0xA6, ctypes.sizeof(GetEncoder))
IOCTL_MODE_GETCONNECTOR = _iowr(0xA7, ctypes.sizeof(GetConnector))
IOCTL_MODE_GETPROPERTY = _iowr(0xAA, ctypes.sizeof(GetProperty))
IOCTL_MODE_SETPROPERTY = _iowr(0xAB, ctypes.sizeof(ConnectorSetProperty))
IOCTL_MODE_ADDFB = _iowr(0xAE, ctypes.sizeof(FbCmd))
IOCTL_MODE_CREATE_DUMB = _iowr(0xB2, ctypes.sizeof(CreateDumb))
IOCTL_MODE_MAP_DUMB = _iowr(0xB3, ctypes.sizeof(MapDumb))


def _ioctl(fd, req, arg=None):
    if arg is None:
        fcntl.ioctl(fd, req, 0)
    else:
        fcntl.ioctl(fd, req, arg, True)  # the kernel fills the structure in place
    return arg


def _array(ctype, n):
    arr = (ctype * max(n, 1))()
    return arr, ctypes.addressof(arr)


def fill_rows(width, height, pitch, bands):
    """XRGB8888 pixels: bands is a list of (first_row, rgb) in row order, each band running to the
    next one. Returns the bytes for the whole buffer."""
    out = bytearray()
    for i, (start, rgb) in enumerate(bands):
        end = bands[i + 1][0] if i + 1 < len(bands) else height
        row = struct.pack("<I", (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]) * width
        row += b"\0" * (pitch - len(row))
        out += row * max(0, min(end, height) - start)
    return bytes(out)


class Display:
    """One DSI connector driven by this process as DRM master."""

    def __init__(self, path="/dev/dri/card0"):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        self.connector_id = self.crtc_id = self.fb_id = self.dpms_prop = None
        self.mode = None
        self._map = None

    def close(self):
        if self._map is not None:
            self._map.close()
            self._map = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    # discovery -------------------------------------------------------------------------------
    def resources(self):
        res = CardRes()
        _ioctl(self.fd, IOCTL_MODE_GETRESOURCES, res)
        crtcs, crtcs_p = _array(ctypes.c_uint32, res.count_crtcs)
        conns, conns_p = _array(ctypes.c_uint32, res.count_connectors)
        encs, encs_p = _array(ctypes.c_uint32, res.count_encoders)
        res2 = CardRes(crtc_id_ptr=crtcs_p, connector_id_ptr=conns_p, encoder_id_ptr=encs_p,
                       count_crtcs=res.count_crtcs, count_connectors=res.count_connectors,
                       count_encoders=res.count_encoders)
        _ioctl(self.fd, IOCTL_MODE_GETRESOURCES, res2)
        return (list(crtcs[:res2.count_crtcs]), list(conns[:res2.count_connectors]),
                list(encs[:res2.count_encoders]))

    def connector(self, cid):
        c = GetConnector(connector_id=cid)
        _ioctl(self.fd, IOCTL_MODE_GETCONNECTOR, c)  # counts (and a probe of the modes)
        modes, modes_p = _array(ModeInfo, c.count_modes)
        props, props_p = _array(ctypes.c_uint32, c.count_props)
        vals, vals_p = _array(ctypes.c_uint64, c.count_props)
        encs, encs_p = _array(ctypes.c_uint32, c.count_encoders)
        c2 = GetConnector(connector_id=cid, modes_ptr=modes_p, count_modes=c.count_modes,
                          props_ptr=props_p, prop_values_ptr=vals_p, count_props=c.count_props,
                          encoders_ptr=encs_p, count_encoders=c.count_encoders)
        _ioctl(self.fd, IOCTL_MODE_GETCONNECTOR, c2)
        return {
            "id": cid, "type": c2.connector_type, "connection": c2.connection,
            "encoder_id": c2.encoder_id,
            "modes": [modes[i] for i in range(min(c.count_modes, c2.count_modes))],
            "props": dict(zip(props[:c2.count_props], vals[:c2.count_props])),
            "encoders": list(encs[:c2.count_encoders]),
        }

    def encoder(self, eid):
        e = GetEncoder(encoder_id=eid)
        _ioctl(self.fd, IOCTL_MODE_GETENCODER, e)
        return e

    def property_name(self, pid):
        p = GetProperty(prop_id=pid)
        _ioctl(self.fd, IOCTL_MODE_GETPROPERTY, p)
        return p.name.decode(errors="replace")

    # control ---------------------------------------------------------------------------------
    def set_master(self):
        _ioctl(self.fd, IOCTL_SET_MASTER)

    def setup(self, bands=None):
        """Pick the first connector with a mode, light it with a dumb framebuffer filled with
        bands (see fill_rows), and remember the DPMS property for on() and off()."""
        self.set_master()
        crtcs, conns, _ = self.resources()
        for cid in conns:
            info = self.connector(cid)
            if not info["modes"]:
                continue
            crtc = None
            for eid in info["encoders"]:
                enc = self.encoder(eid)
                for i, cr in enumerate(crtcs):
                    if enc.possible_crtcs & (1 << i):
                        crtc = cr
                        break
                if crtc:
                    break
            if crtc is None and crtcs:
                crtc = crtcs[0]
            self.connector_id, self.crtc_id = cid, crtc
            preferred = [m for m in info["modes"] if m.type & DRM_MODE_TYPE_PREFERRED]
            self.mode = (preferred or info["modes"])[0]
            for pid in info["props"]:
                if self.property_name(pid) == "DPMS":
                    self.dpms_prop = pid
            break
        if self.connector_id is None:
            raise OSError("no connector with a mode on " + self.path)
        w, h = self.mode.hdisplay, self.mode.vdisplay
        dumb = CreateDumb(width=w, height=h, bpp=32)
        _ioctl(self.fd, IOCTL_MODE_CREATE_DUMB, dumb)
        fb = FbCmd(width=w, height=h, pitch=dumb.pitch, bpp=32, depth=24, handle=dumb.handle)
        _ioctl(self.fd, IOCTL_MODE_ADDFB, fb)
        self.fb_id = fb.fb_id
        mp = MapDumb(handle=dumb.handle)
        _ioctl(self.fd, IOCTL_MODE_MAP_DUMB, mp)
        self._map = mmap.mmap(self.fd, dumb.size, mmap.MAP_SHARED,
                              mmap.PROT_READ | mmap.PROT_WRITE, offset=mp.offset)
        pixels = fill_rows(w, h, dumb.pitch, bands or [(0, (0, 0, 0))])
        self._map[:len(pixels)] = pixels
        self.modeset()

    def modeset(self):
        conn = ctypes.c_uint32(self.connector_id)
        c = Crtc(set_connectors_ptr=ctypes.addressof(conn), count_connectors=1,
                 crtc_id=self.crtc_id, fb_id=self.fb_id, mode_valid=1, mode=self.mode)
        _ioctl(self.fd, IOCTL_MODE_SETCRTC, c)

    def dpms(self, value):
        if self.dpms_prop is None:
            raise OSError("connector has no DPMS property")
        p = ConnectorSetProperty(value=value, prop_id=self.dpms_prop,
                                 connector_id=self.connector_id)
        _ioctl(self.fd, IOCTL_MODE_SETPROPERTY, p)

    def off(self):
        self.dpms(DPMS_OFF)

    def on(self):
        self.dpms(DPMS_ON)


def describe(path="/dev/dri/card0"):
    """Read-only summary (no master, no modeset) for diagnosis."""
    d = Display(path)
    try:
        crtcs, conns, encs = d.resources()
        out = {"crtcs": crtcs, "encoders": encs, "connectors": []}
        for cid in conns:
            info = d.connector(cid)
            out["connectors"].append({
                "id": cid, "type": info["type"], "connection": info["connection"],
                "encoder_id": info["encoder_id"], "encoders": info["encoders"],
                "modes": ["%dx%d@%d %s" % (m.hdisplay, m.vdisplay, m.vrefresh,
                                           m.name.decode(errors="replace")) for m in info["modes"]],
                "props": {d.property_name(p): v for p, v in info["props"].items()},
            })
        return out
    finally:
        d.close()


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), indent=1))
