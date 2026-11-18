import datetime as dt

import pytest

from callbox import pdu as P
from callbox.sms import Reassembler

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
WHEN = dt.datetime(2026, 11, 18, 20, 26, 55, tzinfo=IST)
WHEN_MS = int(WHEN.timestamp() * 1000)
OA = "0C91198967452301"  # +919876543210
SCTS = "62118102625522"  # 2026-11-18 20:26:55 +05:30


def test_reference_vector_gsm7():
    # The widely published example: SCA +27381000015, sender 27838890001, "hellohello".
    s = P.decode("07917283010010F5040BC87238880900F10000993092516195800AE8329BFD4697D9EC37")
    assert s.kind == "deliver"
    assert s.sca == "+27381000015"
    assert s.sender == "27838890001"
    assert s.text == "hellohello"
    assert s.sms_class is None


def test_hand_built_ucs2_vector():
    hexpdu = "00" + "04" + OA + "00" + "08" + SCTS + "0A" + "00480069002020B90035"
    s = P.decode(hexpdu)
    assert s.sender == "+919876543210"
    assert s.alphabet == P.ALPHA_UCS2
    assert s.text == "Hi ₹5"
    assert s.scts_ms == WHEN_MS


def test_hindi_and_emoji_round_trip():
    text = "नमस्ते 😀"
    s = P.decode(P.encode_deliver("+919876543210", text, WHEN))
    assert s.text == text and s.alphabet == P.ALPHA_UCS2


def test_class0_general_group():
    s = P.decode("00" + "04" + OA + "00" + "10" + SCTS + "05" + "E8329BFD06")
    assert s.text == "hello"
    assert s.sms_class == 0 and s.is_class0


def test_class0_group_f_and_class1():
    assert P.decode("00" + "04" + OA + "00" + "F0" + SCTS + "05" + "E8329BFD06").sms_class == 0
    assert P.decode("00" + "04" + OA + "00" + "F1" + SCTS + "05" + "E8329BFD06").sms_class == 1
    assert P.decode("00" + "04" + OA + "00" + "00" + SCTS + "05" + "E8329BFD06").sms_class is None


def test_ucs2_class0():
    s = P.decode(P.encode_deliver("+919876543210", "फ्लैश", WHEN, dcs=0x18))
    assert s.sms_class == 0 and s.text == "फ्लैश"


def test_extension_table():
    text = "Price €5 [a] {b} ~ | ^ \\"
    s = P.decode(P.encode_deliver("+919876543210", text, WHEN))
    assert s.alphabet == P.ALPHA_GSM7
    assert s.text == text
    assert P.gsm7_encode("€") == [0x1B, 0x65]


def test_gsm_specials():
    text = "@£$¥èéùìòÇ\nØøÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ ¤¡ÄÖÑÜ§¿äöñüà"
    assert P.decode(P.encode_deliver("+919876543210", text, WHEN)).text == text


def test_eight_bit_data_and_ports():
    s = P.decode(P.encode_deliver("+919876543210", b"\x01\x02\xff", WHEN, dcs=0x04))
    assert s.alphabet == P.ALPHA_8BIT
    assert s.data == b"\x01\x02\xff"
    assert s.text == "<8-bit data: 0102ff>"


def test_alphanumeric_sender():
    s = P.decode(P.encode_deliver("AIRTEL", "Recharge done", WHEN))
    assert s.sender == "AIRTEL"
    assert s.text == "Recharge done"


def test_concatenated_gsm7_with_udh_fill_bits():
    p1 = P.decode(P.encode_deliver("+919876543210", "First part ", WHEN, concat=(0x42, 2, 1)))
    p2 = P.decode(P.encode_deliver("+919876543210", "second part", WHEN, concat=(0x42, 2, 2)))
    assert p1.concat == (0x42, 2, 1) and p1.text == "First part "
    assert p2.concat == (0x42, 2, 2) and p2.text == "second part"


def test_concatenated_hand_built_8bit_reference():
    # UDHI set (0x44), UDH 050003CC0201, then "hi" after one fill bit: UDL = 7 header septets + 2.
    hexpdu = "00" + "44" + OA + "00" + "00" + SCTS + "09" + "050003CC0201" + "D069"
    s = P.decode(hexpdu)
    assert s.concat == (0xCC, 2, 1)
    assert s.text == "hi"


def test_concatenated_16bit_reference():
    s = P.decode(P.encode_deliver("+919876543210", "abc", WHEN, concat=(0x1234, 3, 2), concat16=True))
    assert s.concat == (0x1234, 3, 2) and s.text == "abc"


def test_reassembly_out_of_order_and_surrogate_split(tmp_path):
    r = Reassembler(str(tmp_path))
    raw = "ab😀".encode("utf-16-be")  # 0061 0062 D83D DE00
    part1 = P.decode(P.encode_deliver("+919876543210", raw[:6], WHEN, dcs=0x08, concat=(7, 2, 1)))
    part2 = P.decode(P.encode_deliver("+919876543210", raw[6:], WHEN, dcs=0x08, concat=(7, 2, 2)))
    assert r.add(1, part2) is None
    done = r.add(1, part1)
    assert done.text == "ab😀"
    assert done.received_at_ms == WHEN_MS
    assert done.parts == 2
    assert list(tmp_path.iterdir()) == []


def test_reassembly_timeout_marks_missing_parts(tmp_path):
    r = Reassembler(str(tmp_path), timeout_s=60, clock=lambda: 1000.0)
    p = P.decode(P.encode_deliver("+919876543210", "only one", WHEN, concat=(9, 3, 1)))
    assert r.add(1, p, now_ms=1_000_000) is None
    assert r.expire(now_ms=1_030_000) == []
    out = r.expire(now_ms=1_061_000)
    assert len(out) == 1
    assert out[0].text == "only one[part 2 of 3 missing][part 3 of 3 missing]"


def test_reassembly_survives_restart(tmp_path):
    p1 = P.decode(P.encode_deliver("+919876543210", "one ", WHEN, concat=(3, 2, 1)))
    p2 = P.decode(P.encode_deliver("+919876543210", "two", WHEN, concat=(3, 2, 2)))
    assert Reassembler(str(tmp_path)).add(2, p1) is None
    done = Reassembler(str(tmp_path)).add(2, p2)
    assert done.text == "one two" and done.sim == 2


def test_status_report():
    # MTI 2, MR 0x21, RA +919876543210, SCTS, DT, status 0.
    hexpdu = "00" + "06" + "21" + OA + SCTS + SCTS + "00"
    s = P.decode(hexpdu)
    assert s.kind == "status-report" and s.message_ref == 0x21 and s.status == 0


def test_submit_single_and_long():
    parts = P.encode_submit("+919000000002", "hello")
    assert parts == [(19, "0011000C911907226439340000A705E8329BFD06")]
    long = "x" * 200
    parts = P.encode_submit("+919000000002", long, ref=5)
    assert len(parts) == 2
    texts = [P.decode(h).text for _, h in parts]
    assert "".join(texts) == long
    assert P.decode(parts[0][1]).concat == (5, 2, 1)
    assert all(n == P.tpdu_length(h) for n, h in parts)


def test_submit_ucs2_long_never_splits_surrogates():
    text = "😀" * 40  # 80 UTF-16 units: needs two parts of at most 67
    parts = P.encode_submit("+919000000002", text)
    assert len(parts) == 2
    joined = b"".join(P.decode(h).data for _, h in parts)
    assert joined.decode("utf-16-be") == text
    for _, h in parts:
        P.decode(h).data.decode("utf-16-be")  # each part decodes on its own


def test_escape_never_split_across_parts():
    text = "a" * 152 + "€" + "b" * 10
    parts = P.encode_submit("+919000000002", text)
    assert "".join(P.decode(h).text for _, h in parts) == text


def test_malformed():
    for bad in ("", "zz", "0004", "00040C9119"):
        with pytest.raises(P.PduError):
            P.decode(bad)
