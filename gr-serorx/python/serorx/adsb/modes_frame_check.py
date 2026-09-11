"""Mode S frame check: DF filter, CRC-24, optional single-bit correction, duplicate suppression."""
import numpy as np
import pmt
from gnuradio import gr

from . import modes
from .modes_slicer import meta_value, stream_clock

DUPLICATE_SECONDS = 0.001


class modes_frame_check(gr.basic_block):
    """Passes frames from `bits` whose DF is accepted and whose CRC is valid.

    `frames` carries the 14 bytes with the metadata plus `df` and `corrected`. `hex` carries the frame as
    upper-case hex text in a byte PDU. `windows` carries the samples of the accepted frame as a float32 PDU
    for display. With correct on, a frame with a bad CRC is retried with its least confident bits flipped
    one at a time. A frame equal to the previous one within DUPLICATE_SECONDS is dropped.
    A message that is not a byte PDU is dropped with one warning.
    """

    def __init__(self, accepted="17,18", correct=True):
        gr.basic_block.__init__(self, name="modes_frame_check", in_sig=None, out_sig=None)
        self._accepted = tuple(int(item) for item in str(accepted).replace(" ", "").split(",") if item)
        self._correct = bool(correct)
        self._in = pmt.intern("bits")
        self._frames = pmt.intern("frames")
        self._hex = pmt.intern("hex")
        self._windows = pmt.intern("windows")
        self.message_port_register_in(self._in)
        self.message_port_register_out(self._frames)
        self.message_port_register_out(self._hex)
        self.message_port_register_out(self._windows)
        self.set_msg_handler(self._in, self._handle)
        self._count = 0
        self._corrected = 0
        self._last = (None, None)
        self._warned = False

    def frame_count(self):
        return self._count

    def corrected_count(self):
        return self._corrected

    def _warn(self, text):
        self.logger.warn(text)

    def _handle(self, msg):
        if not pmt.is_pair(msg) or not pmt.is_u8vector(pmt.cdr(msg)):
            if not self._warned:
                self._warned = True
                self._warn("bits expects byte PDUs from the Mode S PPM Slicer, message dropped")
            return
        meta, data = pmt.car(msg), pmt.cdr(msg)
        frame = bytes(pmt.u8vector_elements(data))
        if len(frame) != modes.FRAME_BYTES or modes.df(frame) not in self._accepted:
            return
        corrected = False
        if modes.crc24(frame) != 0:
            confidence = meta_value(meta, "confidence")
            if not self._correct or confidence is None:
                return
            fixed = modes.correct(frame, np.asarray(confidence, dtype=np.float32))
            if fixed is None or modes.df(fixed) not in self._accepted:
                return
            frame, corrected = fixed, True
        stamp = stream_clock(meta)
        if frame == self._last[0] and stamp - self._last[1] < DUPLICATE_SECONDS:
            return
        self._last = (frame, stamp)
        self._count += 1
        self._corrected += corrected
        text = frame.hex().upper()
        window = pmt.dict_ref(meta, pmt.intern("window"), pmt.PMT_NIL)
        meta = pmt.dict_delete(meta, pmt.intern("window"))
        meta = pmt.dict_add(meta, pmt.intern("df"), pmt.from_long(modes.df(frame)))
        meta = pmt.dict_add(meta, pmt.intern("corrected"), pmt.from_bool(corrected))
        self.message_port_pub(self._frames, pmt.cons(meta, pmt.to_pmt(np.frombuffer(frame, dtype=np.uint8))))
        # Text travels as bytes: a PMT symbol is interned for the life of the process.
        self.message_port_pub(self._hex, pmt.cons(meta, pmt.init_u8vector(len(text), list(text.encode()))))
        if not pmt.is_null(window):
            self.message_port_pub(self._windows, pmt.cons(meta, window))
