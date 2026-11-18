"""SMS PDU coding (3GPP TS 23.040 and 23.038), with no third-party code.

decode() reads an SMS-DELIVER or SMS-STATUS-REPORT as the modem hands it over in PDU mode
(+CMT: ,<len> followed by the hex PDU, service centre address first). It understands the GSM
7-bit default alphabet with its extension table, UCS2 (UTF-16, surrogate pairs included), 8-bit
data, user data headers (concatenation with 8-bit and 16-bit references, ports) and the message
class, so class 0 (flash) messages can be told apart.

encode_submit() builds SMS-SUBMIT PDUs for AT+CMGS, split into concatenated parts when needed.
encode_deliver() builds SMS-DELIVER PDUs; the agent never needs it, the tests and the fake
modem do.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

# GSM 03.38 default alphabet, index = septet value. "\x1b" is the escape to the extension table.
GSM_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅå"
    "Δ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ"
    " !\"#¤%&'()*+,-./"
    "0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNO"
    "PQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmno"
    "pqrstuvwxyzäöñüà"
)
assert len(GSM_BASIC) == 128
GSM_EXT = {0x0A: "\f", 0x14: "^", 0x28: "{", 0x29: "}", 0x2F: "\\", 0x3C: "[", 0x3D: "~", 0x3E: "]", 0x40: "|", 0x65: "€"}
_BASIC_REV = {c: i for i, c in enumerate(GSM_BASIC) if i != 0x1B}
_EXT_REV = {c: i for i, c in GSM_EXT.items()}

ALPHA_GSM7 = "gsm7"
ALPHA_8BIT = "8bit"
ALPHA_UCS2 = "ucs2"


class PduError(ValueError):
    """The PDU is malformed; the message says where."""


# ---------------------------------------------------------------- 7-bit packing


def unpack7(data: bytes, septets: int) -> list[int]:
    """The first `septets` 7-bit values packed little-endian into `data`."""
    value = int.from_bytes(data, "little")
    return [(value >> (7 * i)) & 0x7F for i in range(septets)]


def pack7(septets: list[int], fill_bits: int = 0) -> bytes:
    """Packs 7-bit values, after `fill_bits` zero bits (used after a user data header)."""
    value = 0
    for i, s in enumerate(septets):
        value |= (s & 0x7F) << (fill_bits + 7 * i)
    nbytes = (fill_bits + 7 * len(septets) + 7) // 8
    return value.to_bytes(nbytes, "little") if nbytes else b""


def gsm7_decode(septets: list[int]) -> str:
    out = []
    esc = False
    for s in septets:
        if esc:
            # An unknown extension code shows as the basic character (23.038 6.2.1.1).
            out.append(GSM_EXT.get(s, GSM_BASIC[s] if s != 0x1B else " "))
            esc = False
        elif s == 0x1B:
            esc = True
        else:
            out.append(GSM_BASIC[s])
    return "".join(out)


def gsm7_encode(text: str) -> list[int] | None:
    """Septets for `text`, or None when a character is not in the GSM alphabet."""
    out: list[int] = []
    for ch in text:
        if ch in _BASIC_REV:
            out.append(_BASIC_REV[ch])
        elif ch in _EXT_REV:
            out.extend((0x1B, _EXT_REV[ch]))
        else:
            return None
    return out


# ---------------------------------------------------------------- data coding scheme


def dcs_info(dcs: int) -> tuple[str, int | None, bool]:
    """(alphabet, message class or None, compressed) for a TP-DCS octet."""
    group = dcs >> 4
    if group <= 0x7:  # general data coding (00xx) and automatic deletion (01xx)
        alpha = (dcs >> 2) & 3
        alphabet = {0: ALPHA_GSM7, 1: ALPHA_8BIT, 2: ALPHA_UCS2}.get(alpha, ALPHA_GSM7)
        cls = dcs & 3 if dcs & 0x10 else None
        return alphabet, cls, bool(dcs & 0x20)
    if group in (0xC, 0xD):  # message waiting, discard or store, GSM 7-bit
        return ALPHA_GSM7, None, False
    if group == 0xE:  # message waiting, store, UCS2
        return ALPHA_UCS2, None, False
    if group == 0xF:  # data coding and message class
        return (ALPHA_8BIT if dcs & 0x04 else ALPHA_GSM7), dcs & 3, False
    return ALPHA_GSM7, None, False  # reserved groups: treat as GSM 7-bit


# ---------------------------------------------------------------- addresses and time


_BCD = "0123456789*#abc"


def _bcd_digits(data: bytes, ndigits: int) -> str:
    out = []
    for b in data:
        for nib in (b & 0x0F, b >> 4):
            if nib == 0x0F:
                continue
            out.append(_BCD[nib] if nib < len(_BCD) else "?")
    return "".join(out)[:ndigits]


def decode_address(buf: bytes, pos: int) -> tuple[str, int]:
    """An originating or recipient address at `pos` (length in digits, type, value)."""
    if pos + 2 > len(buf):
        raise PduError("address runs past the end")
    ndigits, toa = buf[pos], buf[pos + 1]
    nbytes = (ndigits + 1) // 2
    raw = buf[pos + 2 : pos + 2 + nbytes]
    if len(raw) < nbytes:
        raise PduError("address value runs past the end")
    ton = (toa >> 4) & 0x07
    if ton == 0x05:  # alphanumeric, GSM 7-bit packed
        addr = gsm7_decode(unpack7(raw, ndigits * 4 // 7))
    else:
        addr = _bcd_digits(raw, ndigits)
        if ton == 0x01 and addr:
            addr = "+" + addr
    return addr, pos + 2 + nbytes


def encode_address(number: str) -> bytes:
    intl = number.startswith("+")
    digits = number.lstrip("+")
    if not digits or any(c not in _BCD for c in digits):
        raise PduError("address must be digits")
    toa = 0x91 if intl else 0x81
    if len(digits) % 2:
        digits += "F"
    body = bytearray()
    for i in range(0, len(digits), 2):
        lo = _BCD.index(digits[i]) if digits[i] != "F" else 0xF
        hi = _BCD.index(digits[i + 1]) if digits[i + 1] != "F" else 0xF
        body.append((hi << 4) | lo)
    return bytes([len(number.lstrip("+")), toa]) + bytes(body)


def _swap_bcd(b: int) -> int:
    return (b & 0x0F) * 10 + (b >> 4)


def decode_scts(raw: bytes) -> int | None:
    """Service centre time stamp (7 octets) as epoch milliseconds, or None when invalid."""
    if len(raw) != 7:
        return None
    try:
        yy, mo, dd, hh, mi, ss = (_swap_bcd(b) for b in raw[:6])
        tzb = raw[6]
        swapped = ((tzb & 0x0F) << 4) | (tzb >> 4)
        quarters = ((swapped & 0x70) >> 4) * 10 + (swapped & 0x0F)
        if swapped & 0x80:
            quarters = -quarters
        tz = _dt.timezone(_dt.timedelta(minutes=15 * quarters))
        when = _dt.datetime(2000 + yy, mo, dd, hh, mi, ss, tzinfo=tz)
        return int(when.timestamp() * 1000)
    except (ValueError, OverflowError):
        return None


def encode_scts(when: _dt.datetime) -> bytes:
    def sw(v: int) -> int:
        return ((v % 10) << 4) | (v // 10)

    off = when.utcoffset() or _dt.timedelta(0)
    quarters = int(off.total_seconds() // 900)
    q = abs(quarters)
    tz = ((q % 10) << 4) | (q // 10) | (0x08 if quarters < 0 else 0)
    return bytes([sw(when.year % 100), sw(when.month), sw(when.day), sw(when.hour), sw(when.minute), sw(when.second), tz])


# ---------------------------------------------------------------- decoding


@dataclass
class Sms:
    kind: str  # "deliver", "status-report" or "submit"
    sca: str = ""
    sender: str = ""
    scts_ms: int | None = None
    pid: int = 0
    dcs: int = 0
    alphabet: str = ALPHA_GSM7
    sms_class: int | None = None
    compressed: bool = False
    text: str = ""
    data: bytes = b""  # the user data after the header (8-bit and UCS2 raw bytes)
    udh: list[tuple[int, bytes]] = field(default_factory=list)
    concat: tuple[int, int, int] | None = None  # (reference, total, sequence)
    ports: tuple[int, int] | None = None
    # Status reports only.
    message_ref: int | None = None
    status: int | None = None

    @property
    def is_class0(self) -> bool:
        return self.sms_class == 0


def _parse_udh(udh: bytes) -> list[tuple[int, bytes]]:
    ies = []
    i = 0
    while i + 2 <= len(udh):
        iei, ln = udh[i], udh[i + 1]
        ies.append((iei, udh[i + 2 : i + 2 + ln]))
        i += 2 + ln
    return ies


def _apply_udh(sms: Sms) -> None:
    for iei, val in sms.udh:
        if iei == 0x00 and len(val) == 3 and val[1] >= 1 and 1 <= val[2] <= val[1]:
            sms.concat = (val[0], val[1], val[2])
        elif iei == 0x08 and len(val) == 4 and val[2] >= 1 and 1 <= val[3] <= val[2]:
            sms.concat = ((val[0] << 8) | val[1], val[2], val[3])
        elif iei == 0x04 and len(val) == 2:
            sms.ports = (val[0], val[1])
        elif iei == 0x05 and len(val) == 4:
            sms.ports = ((val[0] << 8) | val[1], (val[2] << 8) | val[3])


def _decode_user_data(sms: Sms, buf: bytes, pos: int, udhi: bool) -> None:
    if pos >= len(buf):
        raise PduError("no user data length")
    udl = buf[pos]
    ud = buf[pos + 1 :]
    if sms.alphabet == ALPHA_GSM7 and not sms.compressed:
        need = (udl * 7 + 7) // 8
        if len(ud) < need:
            raise PduError(f"user data is {len(ud)} octets, {need} expected")
        septets = unpack7(ud[:need], udl)
        skip = 0
        if udhi:
            udhl = ud[0]
            sms.udh = _parse_udh(ud[1 : 1 + udhl])
            skip = ((udhl + 1) * 8 + 6) // 7
        sms.text = gsm7_decode(septets[skip:])
        sms.data = b""
    else:
        if len(ud) < udl:
            raise PduError(f"user data is {len(ud)} octets, {udl} expected")
        ud = ud[:udl]
        if udhi and ud:
            udhl = ud[0]
            sms.udh = _parse_udh(ud[1 : 1 + udhl])
            ud = ud[1 + udhl :]
        sms.data = bytes(ud)
        if sms.alphabet == ALPHA_UCS2 and not sms.compressed:
            sms.text = ucs2_decode(sms.data)
        else:
            sms.text = "<8-bit data: " + sms.data.hex() + ">"
    _apply_udh(sms)


def ucs2_decode(data: bytes) -> str:
    if len(data) % 2:
        data = data[:-1]
    return data.decode("utf-16-be", errors="replace")


def decode(pdu_hex: str, has_sca: bool = True) -> Sms:
    """Decodes one PDU as received in PDU mode. Raises PduError when it is malformed."""
    try:
        buf = bytes.fromhex(pdu_hex.strip())
    except ValueError as exc:
        raise PduError("not hex") from exc
    pos = 0
    sca = ""
    if has_sca:
        if not buf:
            raise PduError("empty PDU")
        sl = buf[0]
        if sl:
            if 1 + sl > len(buf):
                raise PduError("service centre address runs past the end")
            toa = buf[1]
            sca = _bcd_digits(buf[2 : 1 + sl], (sl - 1) * 2)
            if (toa >> 4) & 7 == 1:
                sca = "+" + sca
        pos = 1 + sl
    if pos >= len(buf):
        raise PduError("no first octet")
    first = buf[pos]
    pos += 1
    mti = first & 0x03
    udhi = bool(first & 0x40)
    if mti == 0x00:  # SMS-DELIVER
        sms = Sms(kind="deliver", sca=sca)
        sms.sender, pos = decode_address(buf, pos)
        if pos + 9 > len(buf):
            raise PduError("PDU ends before the time stamp")
        sms.pid, sms.dcs = buf[pos], buf[pos + 1]
        sms.scts_ms = decode_scts(buf[pos + 2 : pos + 9])
        pos += 9
        sms.alphabet, sms.sms_class, sms.compressed = dcs_info(sms.dcs)
        _decode_user_data(sms, buf, pos, udhi)
        return sms
    if mti == 0x02:  # SMS-STATUS-REPORT
        sms = Sms(kind="status-report", sca=sca)
        if pos >= len(buf):
            raise PduError("no message reference")
        sms.message_ref = buf[pos]
        sms.sender, pos = decode_address(buf, pos + 1)
        if pos + 15 > len(buf):
            raise PduError("status report too short")
        sms.scts_ms = decode_scts(buf[pos : pos + 7])
        sms.status = buf[pos + 14]
        return sms
    if mti == 0x01:  # SMS-SUBMIT (only our own, read back in tests)
        sms = Sms(kind="submit", sca=sca)
        pos += 1  # message reference
        sms.sender, pos = decode_address(buf, pos)
        sms.pid, sms.dcs = buf[pos], buf[pos + 1]
        pos += 2
        vpf = (first >> 3) & 0x03
        pos += {0: 0, 2: 1}.get(vpf, 7)
        sms.alphabet, sms.sms_class, sms.compressed = dcs_info(sms.dcs)
        _decode_user_data(sms, buf, pos, udhi)
        return sms
    raise PduError(f"unsupported message type {mti}")


# ---------------------------------------------------------------- encoding

GSM7_SINGLE = 160
GSM7_PART = 153
UCS2_SINGLE = 70
UCS2_PART = 67


def _split_gsm7(septets: list[int], size: int) -> list[list[int]]:
    parts, cur = [], []
    i = 0
    while i < len(septets):
        unit = septets[i : i + 2] if septets[i] == 0x1B else septets[i : i + 1]
        if len(cur) + len(unit) > size:
            parts.append(cur)
            cur = []
        cur.extend(unit)
        i += len(unit)
    if cur:
        parts.append(cur)
    return parts


def _split_ucs2(text: str, size: int) -> list[bytes]:
    units = text.encode("utf-16-be")
    out, i = [], 0
    step = size * 2
    while i < len(units):
        end = min(i + step, len(units))
        # Never split a surrogate pair: a high surrogate at the cut moves to the next part.
        if end < len(units) and 0xD8 <= units[end - 2] <= 0xDB:
            end -= 2
        out.append(units[i:end])
        i = end
    return out


def _user_data(body: list[int] | bytes, gsm7: bool, udh: bytes) -> tuple[int, bytes]:
    """(UDL, UD) for a part, with an optional user data header (with its length octet)."""
    if gsm7:
        assert isinstance(body, list)
        if udh:
            hs = (len(udh) * 8 + 6) // 7
            fill = hs * 7 - len(udh) * 8
            packed = pack7(body, fill)
            return hs + len(body), udh + packed
        return len(body), pack7(body)
    assert isinstance(body, (bytes, bytearray))
    return len(udh) + len(body), udh + bytes(body)


def _concat_udh(ref: int, total: int, seq: int) -> bytes:
    return bytes([0x05, 0x00, 0x03, ref & 0xFF, total, seq])


def encode_submit(to: str, text: str, ref: int = 0, validity: int = 0xA7) -> list[tuple[int, str]]:
    """SMS-SUBMIT parts for AT+CMGS: a list of (TPDU length, hex PDU with an empty SCA)."""
    septets = gsm7_encode(text)
    gsm7 = septets is not None
    if gsm7:
        bodies: list = [septets] if len(septets) <= GSM7_SINGLE else _split_gsm7(septets, GSM7_PART)
    else:
        raw = text.encode("utf-16-be")
        bodies = [raw] if len(raw) <= UCS2_SINGLE * 2 else _split_ucs2(text, UCS2_PART)
    total = len(bodies)
    if total > 255:
        raise PduError("message too long")
    out = []
    for seq, body in enumerate(bodies, 1):
        udh = _concat_udh(ref, total, seq) if total > 1 else b""
        first = 0x01 | 0x10 | (0x40 if udh else 0)
        udl, ud = _user_data(body, gsm7, udh)
        tpdu = bytes([first, 0x00]) + encode_address(to) + bytes([0x00, 0x00 if gsm7 else 0x08, validity, udl]) + ud
        out.append((len(tpdu), "00" + tpdu.hex().upper()))
    return out


def encode_deliver(
    sender: str,
    text: str | bytes,
    when: _dt.datetime,
    dcs: int | None = None,
    concat: tuple[int, int, int] | None = None,
    sca: str = "+919810051914",
    concat16: bool = False,
) -> str:
    """One SMS-DELIVER PDU (hex, SCA first). `text` bytes are sent as they are (8-bit or UCS2)."""
    if dcs is None:
        dcs = 0x00 if isinstance(text, str) and gsm7_encode(text) is not None else 0x08
    alphabet, _, _ = dcs_info(dcs)
    udh = b""
    if concat:
        ref, total, seq = concat
        if concat16:
            udh = bytes([0x06, 0x08, 0x04, ref >> 8, ref & 0xFF, total, seq])
        else:
            udh = _concat_udh(ref, total, seq)
    if alphabet == ALPHA_GSM7:
        assert isinstance(text, str)
        body: list | bytes = gsm7_encode(text) or []
        udl, ud = _user_data(body, True, udh)
    else:
        body = text if isinstance(text, bytes) else text.encode("utf-16-be")
        udl, ud = _user_data(body, False, udh)
    sca_bytes = encode_address(sca)[1:]  # type and digits, without the digit count
    first = 0x04 | (0x40 if udh else 0)  # no more messages to send
    if sender and not sender.lstrip("+").isdigit():
        septs = gsm7_encode(sender) or []
        packed = pack7(septs)
        oa = bytes([len(packed) * 2, 0xD0]) + packed
    else:
        oa = encode_address(sender)
    tpdu = bytes([first]) + oa + bytes([0x00, dcs]) + encode_scts(when) + bytes([udl]) + ud
    return (bytes([len(sca_bytes)]) + sca_bytes + tpdu).hex().upper()


def tpdu_length(pdu_hex: str) -> int:
    """The length AT+CMGS and +CMT give: octets after the service centre address."""
    buf = bytes.fromhex(pdu_hex)
    return len(buf) - 1 - buf[0]
