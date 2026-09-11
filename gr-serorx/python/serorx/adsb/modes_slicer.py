"""Mode S PPM slicer: frame window PDU to bits PDU with a confidence per bit."""
import time

import numpy as np
import pmt
from gnuradio import gr

from . import modes


def meta_value(meta, key, default=None):
    """A metadata entry as a Python value, default when absent."""
    value = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
    return default if pmt.is_null(value) else pmt.to_python(value)


def stream_clock(meta):
    """Seconds on the stream clock (offset / samp_rate from the preamble stage), else the process clock."""
    offset, rate = meta_value(meta, "offset"), meta_value(meta, "samp_rate")
    if offset is not None and rate:
        return offset / rate
    return time.monotonic()


class modes_slicer(gr.basic_block):
    """Slices every window PDU from `windows` into 112 bits.

    `bits` carries the 14 bytes with the metadata of the window plus `confidence` (float32 per bit) and
    `window` (the samples), so a later stage can show the frame it accepted.
    `confidence` carries the same confidences signed by the bit value, for display.
    A message that is not a float32 PDU is dropped with one warning.
    """

    def __init__(self):
        gr.basic_block.__init__(self, name="modes_slicer", in_sig=None, out_sig=None)
        self._in = pmt.intern("windows")
        self._bits = pmt.intern("bits")
        self._confidence = pmt.intern("confidence")
        self._warned = False
        self.message_port_register_in(self._in)
        self.message_port_register_out(self._bits)
        self.message_port_register_out(self._confidence)
        self.set_msg_handler(self._in, self._handle)

    def _warn(self, text):
        self.logger.warn(text)

    def _handle(self, msg):
        if not pmt.is_pair(msg) or not pmt.is_f32vector(pmt.cdr(msg)):
            if not self._warned:
                self._warned = True
                self._warn("windows expects float32 PDUs from the Mode S Preamble Detector, message dropped")
            return
        meta, data = pmt.car(msg), pmt.cdr(msg)
        spu = int(meta_value(meta, "spu", 12))
        window = np.array(pmt.f32vector_elements(data), dtype=np.float32)
        if len(window) < modes.frame_length(spu):
            return
        frame, confidence = modes.slice_frame(window, spu)
        bits = np.unpackbits(np.frombuffer(frame, dtype=np.uint8)).astype(bool)
        meta = pmt.dict_add(meta, pmt.intern("confidence"), pmt.to_pmt(confidence))
        meta = pmt.dict_add(meta, pmt.intern("window"), data)
        self.message_port_pub(self._bits, pmt.cons(meta, pmt.to_pmt(np.frombuffer(frame, dtype=np.uint8))))
        signed = np.where(bits, confidence, -confidence).astype(np.float32)
        self.message_port_pub(self._confidence, pmt.cons(meta, pmt.to_pmt(signed)))
