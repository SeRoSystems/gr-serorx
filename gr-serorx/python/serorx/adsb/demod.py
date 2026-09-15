"""Mode S decoding on a magnitude buffer: detect, slice at several alignments, keep the best.

The acceptance loop follows readsb (https://github.com/wiedehopf/readsb), demodulate2400 in
demod_2400.c, GPL-3.0, copyright Michael Wolf and Oliver Jowett. The five phase correlators of
that file are absent: they compensate for 1.2 samples per bit at 2.4 MSps, and a bit is a whole
number of samples here.

numpy only, no GNU Radio import and no PDU. The block is a shell around this.
"""
import numpy as np

from . import modes, score

DUPLICATE_SECONDS = 1e-3


def skip_samples(bits, spu):
    """Samples to advance after a decode: five sixths of the data bits.

    A frame whose preamble lands on the tail of the previous one is still found, because the last
    bits of the first frame are often the only part the collision destroys.
    """
    return bits * spu * 5 // 6


class Frame:
    """One accepted frame."""

    __slots__ = ("data", "df", "icao", "score", "errors", "alignment", "level", "offset",
                 "clock", "confidence")

    def __init__(self, data, df, icao, score, errors, alignment, level, offset, clock,
                 confidence):
        self.data = data
        self.df = df
        self.icao = icao
        self.score = score
        self.errors = errors
        self.alignment = alignment
        self.level = level
        self.offset = offset
        self.clock = clock
        self.confidence = confidence

    def __repr__(self):
        addr = "unknown" if self.icao is None else f"{self.icao:06X}"
        return f"Frame(df={self.df}, icao={addr}, score={self.score}, {self.data.hex()})"


class Demod:
    """Detection, alignment search, scoring and repair over magnitude buffers.

    `process` holds no sample state, so the caller passes overlapping buffers. The address filter
    and the last accepted frame do span calls.
    """

    def __init__(self, spu, correct_bits=1, alignments=modes.ALIGNMENTS, short_replies=True,
                 icao_ttl=score.TTL, threshold=3.0):
        self._spu = int(spu)
        self._depth = int(correct_bits)
        self._offsets = modes.alignments(int(alignments))
        self._accepted = score.SHORT_REPLIES if short_replies else score.LONG_ONLY
        self._threshold = float(threshold)
        self._icao = score.IcaoFilter(icao_ttl)
        self._last = None
        self._last_clock = 0.0
        self.guard = max(self._spu // 2, max(abs(offset) for offset in self._offsets))

    def process(self, mag, offset, clock):
        """Every frame in `mag`. `offset` is the stream index of its first sample."""
        self._icao.expire(clock)
        out = []
        skip_until = 0
        for start in modes.candidates(mag, self._spu, self._threshold):
            start = int(start)
            if start < skip_until:
                continue
            best = None
            for shift in self._offsets:
                read = self._read(mag, start + shift)
                if read is not None and (best is None or read[0].score > best[0].score):
                    best = read + (shift,)
            if best is None or best[0].score < 0:
                continue
            result, confidence, shift = best
            frame = self._accept(result, confidence, shift, mag, start, offset, clock)
            if frame is not None:
                out.append(frame)
            skip_until = start + skip_samples(len(result.data) * 8, self._spu)
        return out

    def _read(self, mag, start):
        """(result, confidence) for one alignment. None where a whole long frame does not fit."""
        if start < 0 or start + modes.frame_length(self._spu) > len(mag):
            return None
        data, confidence = modes.slice_frame(mag, start, self._spu)
        return score.evaluate(data, 112, self._icao, self._depth, self._accepted), confidence

    def _accept(self, result, confidence, shift, mag, start, offset, clock):
        # Every frame carries its own time, so two identical frames far apart inside one buffer
        # are both kept.
        at = clock + start / (self._spu * 1e6)
        if self._last == result.data and at - self._last_clock < DUPLICATE_SECONDS:
            return None
        self._last = result.data
        self._last_clock = at
        if result.df in (11, 17, 18) and result.icao is not None:
            self._icao.add(result.icao)
        bits = len(result.data) * 8
        first = start + shift + modes.PREAMBLE_US * self._spu
        window = np.asarray(mag[first:first + bits * self._spu], dtype=np.float64)
        level = float(np.mean(window * window)) if window.size else 0.0
        return Frame(result.data, result.df, result.icao, result.score, result.errors, shift,
                     level, offset + start, at, confidence[:bits])
