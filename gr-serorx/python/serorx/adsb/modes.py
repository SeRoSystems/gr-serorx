"""Mode S demodulation on a magnitude stream, numpy only.

Preamble candidates and PPM slicing to bytes with a confidence per bit. synthesize() builds the
magnitude samples of a frame for the tests and the fake. Validation lives in crc.py and score.py.

The noise reference is taken from the gaps inside each candidate, following readsb
(https://github.com/wiedehopf/readsb, demod_2400.c, GPL-3.0, copyright Michael Wolf and
Oliver Jowett), which measures its threshold against samples a real preamble leaves quiet. A
reference drawn from the whole block instead is raised by a strong frame and loses a weak one.
"""
import numpy as np

PREAMBLE_US = 8
FRAME_BITS = 112
FRAME_BYTES = FRAME_BITS // 8
PULSES_US = (0.0, 1.0, 3.5, 4.5)
GAPS_US = ((0.5, 1.0), (1.5, 3.5), (4.0, 4.5), (5.0, 8.0))
PULSE_FLOOR_FACTOR = 2.0
NOISE_EPS = 1e-9
ALIGNMENTS = 5


def samples_per_us(samp_rate):
    """Samples per microsecond. The rate must be an even number of MSps."""
    spu = int(round(samp_rate / 1e6))
    if spu < 2 or spu % 2 or abs(spu * 1e6 - samp_rate) > 1:
        raise ValueError(f"Sample rate {samp_rate / 1e6:.2f} MSps is not an even number of MSps")
    return spu


def frame_length(spu, bits=FRAME_BITS):
    """Samples of preamble plus `bits` bits. A data bit lasts one microsecond."""
    return (PREAMBLE_US + bits) * spu


def alignments(count=ALIGNMENTS):
    """Sample offsets to try around a detected peak, centred on zero."""
    half = count // 2
    return tuple(range(-half, half + 1))


def candidates(mag, spu, threshold=3.0):
    """Start indices of preamble candidates, aligned to the peak within half a microsecond.

    A candidate needs its pulse mean above `threshold` times the mean over its own four gaps, and
    each of the four pulses above PULSE_FLOOR_FACTOR times it. The second rule removes the half
    preamble matches 3.5 us either side of a real frame. Short Mode S replies carry the same
    preamble and appear here too.
    """
    half = spu // 2
    n = len(mag) - frame_length(spu) + 1
    if n <= 0:
        return np.zeros(0, dtype=np.int64)
    c = np.concatenate(([0.0], np.cumsum(mag, dtype=np.float64)))
    pulses = []
    for us in PULSES_US:
        off = int(us * spu)
        pulses.append((c[off + half:off + half + n] - c[off:off + n]) / half)
    pulse = sum(pulses) / len(pulses)
    weakest = np.minimum.reduce(pulses)
    gap = np.zeros(n)
    gap_samples = 0
    for start_us, end_us in GAPS_US:
        a, b = int(start_us * spu), int(end_us * spu)
        gap += c[b:b + n] - c[a:a + n]
        gap_samples += b - a
    gap = np.maximum(gap / gap_samples, NOISE_EPS)
    hits = np.flatnonzero((pulse > threshold * gap) & (weakest > PULSE_FLOOR_FACTOR * gap))
    out = []
    next_allowed = 0
    for s in hits:
        if s < next_allowed:
            continue
        s = int(s + np.argmax(pulse[s:min(s + half, n)]))
        out.append(s)
        next_allowed = s + half
    return np.array(out, dtype=np.int64)


def slice_frame(mag, start, spu, bits=FRAME_BITS):
    """(bytes, confidence per bit) for a frame whose preamble starts at `start`.

    A bit is 1 when its first half microsecond carries more energy than its second. The confidence
    is the normalised difference of the two halves, 0 for equal halves and 1 for a clean pulse.
    """
    half = spu // 2
    first = start + PREAMBLE_US * spu
    data = np.asarray(mag[first:first + bits * spu], dtype=np.float64).reshape(bits, spu)
    left = data[:, :half].sum(axis=1)
    right = data[:, half:].sum(axis=1)
    confidence = (np.abs(left - right) / (left + right + 1e-12)).astype(np.float32)
    return np.packbits(left > right).tobytes(), confidence


def df(data):
    return data[0] >> 3


def synthesize(msg, spu, amplitude=1.0):
    """Magnitude samples of one frame: preamble pulses and the PPM bits."""
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(msg), dtype=np.uint8))
    half = spu // 2
    out = np.zeros((PREAMBLE_US + len(bits)) * spu, dtype=np.float32)
    for us in PULSES_US:
        start = int(us * spu)
        out[start:start + half] = amplitude
    for i, bit in enumerate(bits):
        start = (PREAMBLE_US + i) * spu + (0 if bit else half)
        out[start:start + half] = amplitude
    return out
