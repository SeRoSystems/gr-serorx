"""Mode S preamble detector: one PDU per candidate window on a magnitude stream."""
import numpy as np
import pmt
from gnuradio import gr

from . import modes

TAG_TIME = "rx_time"


class modes_preamble(gr.sync_block):
    """Sink for a magnitude stream. Publishes one PDU per preamble candidate on `windows`.

    The PDU data is the frame window (preamble plus 112 bits) as float32. The metadata carries `offset`
    (absolute sample index of the window), `spu` (samples per microsecond), `samp_rate`, and `time`
    (seconds, from the last `rx_time` tag on the stream) when a tag was seen. set_samp_rate follows a
    source whose rate changes at runtime. An `rx_time` tag that is not a (seconds, fraction) tuple is
    ignored with one warning.
    """

    def __init__(self, samp_rate=12e6, threshold=3.0):
        spu = modes.samples_per_us(samp_rate)
        gr.sync_block.__init__(self, name="modes_preamble", in_sig=[np.float32], out_sig=None)
        self._spu = spu
        self._rate = float(samp_rate)
        self._threshold = float(threshold)
        self._frame_len = modes.frame_length(spu)
        self._time_ref = None
        self._candidates = 0
        self._warned = False
        self.set_history(self._frame_len)
        self._port = pmt.intern("windows")
        self.message_port_register_out(self._port)

    def candidate_count(self):
        return self._candidates

    def set_samp_rate(self, samp_rate):
        spu = modes.samples_per_us(samp_rate)
        self._spu, self._rate = spu, float(samp_rate)
        self._frame_len = modes.frame_length(spu)
        self.set_history(self._frame_len)
        self._time_ref = None

    def _warn(self, text):
        self.logger.warn(text)

    def _note_time(self, tag):
        stamp = pmt.to_python(tag.value)
        if isinstance(stamp, tuple) and len(stamp) == 2:
            self._time_ref = (tag.offset, float(stamp[0]) + float(stamp[1]))
        elif not self._warned:
            self._warned = True
            self._warn(f"rx_time tag is not a (seconds, fraction) tuple, ignored: {pmt.write_string(tag.value)}")

    def work(self, input_items, output_items):
        mag = input_items[0]
        spu, rate, frame_len = self._spu, self._rate, self._frame_len
        n = len(mag) - frame_len + 1
        if n <= 0:
            return 0
        first = self.nitems_read(0)
        for tag in self.get_tags_in_range(0, first, first + n, pmt.intern(TAG_TIME)):
            self._note_time(tag)
        base = first - (frame_len - 1)
        for s in modes.candidates(mag, spu, self._threshold):
            self._candidates += 1
            offset = base + int(s)
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("offset"), pmt.from_uint64(max(offset, 0)))
            meta = pmt.dict_add(meta, pmt.intern("spu"), pmt.from_long(spu))
            meta = pmt.dict_add(meta, pmt.intern("samp_rate"), pmt.from_double(rate))
            if self._time_ref is not None and offset >= self._time_ref[0]:
                stamp = self._time_ref[1] + (offset - self._time_ref[0]) / rate
                meta = pmt.dict_add(meta, pmt.intern("time"), pmt.from_double(stamp))
            window = np.array(mag[s:s + frame_len], dtype=np.float32)
            self.message_port_pub(self._port, pmt.cons(meta, pmt.to_pmt(window)))
        return n
