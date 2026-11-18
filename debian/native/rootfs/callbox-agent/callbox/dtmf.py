"""DTMF detection on the call downlink with the Goertzel algorithm (numpy, vectorised by block).

Blocks of 205 samples at 8 kHz (about 26 ms, scaled for other rates). For each block the eight
DTMF frequencies are measured with the Goertzel recurrence; a block holds a digit when the
strongest low and high tones each carry a clear share of the block's energy, beat the next tone
in their group by 6 dB, and their twist is within 10 dB. A digit is reported once it holds for
two blocks in a row and again only after a gap.
"""

from __future__ import annotations

import numpy as np

LOW = (697, 770, 852, 941)
HIGH = (1209, 1336, 1477, 1633)
KEYS = (("1", "2", "3", "A"), ("4", "5", "6", "B"), ("7", "8", "9", "C"), ("*", "0", "#", "D"))


def goertzel_powers(blocks: np.ndarray, freqs: tuple[int, ...], rate: int) -> np.ndarray:
    """Power at each frequency for each block: blocks (B, N) -> (B, len(freqs))."""
    n = blocks.shape[1]
    w = 2 * np.pi * np.asarray(freqs, dtype=np.float64) / rate
    coeff = 2 * np.cos(w)
    s1 = np.zeros((blocks.shape[0], len(freqs)))
    s2 = np.zeros_like(s1)
    for i in range(n):
        s0 = blocks[:, i : i + 1] + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def block_digits(samples: np.ndarray, rate: int = 8000, min_dbfs: float = -45.0) -> list[str | None]:
    n = max(64, int(round(205 * rate / 8000)))
    x = np.asarray(samples, dtype=np.float64) / 32768.0
    nb = len(x) // n
    if nb == 0:
        return []
    blocks = x[: nb * n].reshape(nb, n)
    energy = np.sum(blocks * blocks, axis=1)
    lo = goertzel_powers(blocks, LOW, rate)
    hi = goertzel_powers(blocks, HIGH, rate)
    norm = np.maximum(energy * n / 2, 1e-12)
    out: list[str | None] = []
    floor = (10 ** (min_dbfs / 20)) ** 2 * n
    for b in range(nb):
        if energy[b] < floor:
            out.append(None)
            continue
        l, h = lo[b] / norm[b], hi[b] / norm[b]
        li, hi_i = int(np.argmax(l)), int(np.argmax(h))
        lsorted, hsorted = np.sort(l), np.sort(h)
        ok = (
            l[li] > 0.15 and h[hi_i] > 0.15
            and lsorted[-2] < l[li] / 4 and hsorted[-2] < h[hi_i] / 4
            and 0.1 < h[hi_i] / l[li] < 10
            and l[li] + h[hi_i] > 0.5
        )
        out.append(KEYS[li][hi_i] if ok else None)
    return out


def detect(samples: np.ndarray, rate: int = 8000) -> str:
    digits = []
    prev = None
    run = 0
    reported = False
    for d in block_digits(samples, rate):
        if d is not None and d == prev:
            run += 1
        else:
            run = 1 if d is not None else 0
            reported = False
        prev = d
        if d is not None and run >= 2 and not reported:
            digits.append(d)
            reported = True
    return "".join(digits)
