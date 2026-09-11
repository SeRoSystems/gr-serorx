"""Mode S demodulation on a magnitude stream, numpy only.

Stages, one function each: preamble candidates, PPM slicing to bytes with a confidence per bit, CRC-24,
single-bit correction, DF filter. synthesize() builds a frame's magnitude samples for tests and the fake.
"""
import numpy as np

PREAMBLE_US = 8
FRAME_BITS = 112
FRAME_BYTES = FRAME_BITS // 8
PULSES_US = (0.0, 1.0, 3.5, 4.5)
GAPS_US = ((0.5, 1.0), (1.5, 3.5), (4.0, 4.5), (5.0, 8.0))
FLOOR_FACTOR = 3.0
PULSE_FLOOR_FACTOR = 2.0
CORRECT_BITS = 5
ACCEPTED_DF = (17, 18)


def samples_per_us(samp_rate):
    """Samples per microsecond. The rate must be an even number of MSps."""
    spu = int(round(samp_rate / 1e6))
    if spu < 2 or spu % 2 or abs(spu * 1e6 - samp_rate) > 1:
        raise ValueError(f"Sample rate {samp_rate / 1e6:.2f} MSps is not an even number of MSps")
    return spu


def frame_length(spu):
    """Samples of preamble plus 112 bits."""
    return (PREAMBLE_US + FRAME_BITS) * spu


def candidates(mag, spu, threshold=3.0):
    """Start indices of preamble candidates, aligned to the peak within half a microsecond.

    A candidate has a pulse mean above threshold times the gap mean and above FLOOR_FACTOR times the
    median, and every one of the four pulses above PULSE_FLOOR_FACTOR times the median. The last rule
    removes the half-preamble matches 3.5 us before and after a real frame. A full frame fits after
    every index. Short Mode S replies (DF 0, 4, 5, 11) carry the same preamble and appear too."""
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
    gap /= gap_samples
    median = float(np.median(mag[:n]))
    hits = np.flatnonzero((pulse > threshold * gap) & (pulse > FLOOR_FACTOR * median)
                          & (weakest > PULSE_FLOOR_FACTOR * median))
    out = []
    next_allowed = 0
    for s in hits:
        if s < next_allowed:
            continue
        s = int(s + np.argmax(pulse[s:min(s + half, n)]))
        out.append(s)
        next_allowed = s + half
    return np.array(out, dtype=np.int64)


def slice_frame(window, spu):
    """(14 bytes, confidence per bit) from a frame window of frame_length(spu) samples.

    A bit is 1 when the first half microsecond carries more energy than the second. The confidence is
    the normalised difference of the two halves, 0 for equal halves and 1 for a clean pulse."""
    half = spu // 2
    data = np.asarray(window[PREAMBLE_US * spu:frame_length(spu)], dtype=np.float64).reshape(FRAME_BITS, spu)
    first = data[:, :half].sum(axis=1)
    second = data[:, half:].sum(axis=1)
    bits = first > second
    confidence = (np.abs(first - second) / (first + second + 1e-12)).astype(np.float32)
    return np.packbits(bits).tobytes(), confidence


def crc24(data):
    """Mode S CRC-24 remainder over the whole frame. 0 for a valid DF17 or DF18 frame."""
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0xFFF409
        crc &= 0xFFFFFF
    return crc


def df(data):
    return data[0] >> 3


def correct(data, confidence, max_flips=CORRECT_BITS):
    """The frame with its least confident bit flipped, when one such flip gives a valid CRC. None otherwise.

    Tries the max_flips least confident bits one at a time."""
    for index in np.argsort(confidence)[:max_flips]:
        flipped = bytearray(data)
        flipped[index // 8] ^= 0x80 >> (index % 8)
        if crc24(flipped) == 0:
            return bytes(flipped)
    return None


def check(data, accepted=ACCEPTED_DF):
    """True for a frame with an accepted DF and a valid CRC."""
    return df(data) in accepted and crc24(data) == 0


def synthesize(msg, spu, amplitude=1.0):
    """Magnitude samples of one frame: preamble pulses and 112 PPM bits."""
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
