"""Mode S demodulation block: complex samples in, validated frames out as PDUs."""
import numpy as np
import pmt
from gnuradio import gr

from . import demod, modes

TAG_TIME = "rx_time"


class modes_demod(gr.sync_block):
    """Decodes Mode S frames from a complex baseband stream.

    Magnitude is taken inside the block, so no magnitude block belongs in front of it. Frames
    leave on `frames` as a PDU: the 14 or 7 bytes, and a meta with the downlink format, the
    address, the score, the repaired bit count, the alignment, the signal level, the stream
    offset, the sample rate and the time.

    Accepted formats are 0, 4, 5, 11, 16, 17, 18, 20 and 21. The short replies overlay the address
    on their parity field and are validated against the addresses of extended squitters heard in
    the last minute, so they appear only once their aircraft has been seen.
    """

    def __init__(self, samp_rate, correct_bits=1, alignments=modes.ALIGNMENTS,
                 short_replies=True, threshold=3.0, icao_ttl=60.0):
        gr.sync_block.__init__(self, name="modes_demod", in_sig=[np.complex64], out_sig=None)
        self._correct_bits = int(correct_bits)
        self._alignments = int(alignments)
        self._short_replies = bool(short_replies)
        self._threshold = float(threshold)
        self._icao_ttl = float(icao_ttl)
        self._count = 0
        self._last_offset = -1
        self._port = pmt.intern("frames")
        self.message_port_register_out(self._port)
        self._configure(samp_rate)

    def _configure(self, samp_rate):
        self._spu = modes.samples_per_us(samp_rate)
        self._rate = float(samp_rate)
        self._demod = demod.Demod(self._spu, self._correct_bits, self._alignments,
                                  self._short_replies, self._icao_ttl, self._threshold)
        self.set_history(modes.frame_length(self._spu) + 2 * self._demod.guard + 1)

    def set_samp_rate(self, samp_rate):
        """Reconfigures for a new rate. The address filter starts empty again."""
        self._configure(samp_rate)

    def frame_count(self):
        return self._count

    def work(self, input_items, output_items):
        samples = input_items[0]
        # The buffer opens with the history, which lies before the first unconsumed sample.
        first = self.nitems_read(0) - (self.history() - 1)
        mag = np.abs(samples).astype(np.float32)
        for frame in self._demod.process(mag, first, self._clock(first, len(samples))):
            # Work calls overlap by the history, so a frame near a buffer end is offered twice.
            if frame.offset <= self._last_offset:
                continue
            self._last_offset = frame.offset
            self._publish(frame)
        return len(samples) - (self.history() - 1)

    def _clock(self, first, n):
        """Seconds for the duplicate window and the filter expiry, from rx_time or the offset."""
        # The first buffer opens with zero padding at negative indices, which carry no tags.
        start = max(first, 0)
        for tag in self.get_tags_in_range(0, start, max(first + n, start), pmt.intern(TAG_TIME)):
            value = tag.value
            if pmt.is_tuple(value) and pmt.length(value) == 2:
                # rx_time carries the seconds as a uint64, which pmt.to_double refuses.
                seconds, fraction = pmt.to_python(value)
                return float(seconds) + float(fraction)
        return max(first, 0) / self._rate

    def _publish(self, frame):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("df"), pmt.from_long(frame.df))
        if frame.icao is not None:
            meta = pmt.dict_add(meta, pmt.intern("icao"), pmt.from_long(frame.icao))
        meta = pmt.dict_add(meta, pmt.intern("score"), pmt.from_long(frame.score))
        meta = pmt.dict_add(meta, pmt.intern("errors"), pmt.from_long(frame.errors))
        meta = pmt.dict_add(meta, pmt.intern("alignment"), pmt.from_long(frame.alignment))
        meta = pmt.dict_add(meta, pmt.intern("level"), pmt.from_double(frame.level))
        meta = pmt.dict_add(meta, pmt.intern("offset"), pmt.from_uint64(max(frame.offset, 0)))
        meta = pmt.dict_add(meta, pmt.intern("samp_rate"), pmt.from_double(self._rate))
        meta = pmt.dict_add(meta, pmt.intern("spu"), pmt.from_long(self._spu))
        meta = pmt.dict_add(meta, pmt.intern("time"), pmt.from_double(frame.clock))
        self._count += 1
        self.message_port_pub(self._port,
                              pmt.cons(meta, pmt.init_u8vector(len(frame.data),
                                                               list(frame.data))))
