import threading

import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

from gnuradio.serorx.adsb import crc, modes, modes_demod

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION = "8D40621D58C382D690C8AC2863A7"
VELOCITY = "8D485020994409940838175B284F"
ADDRESS = 0x4840D6
SAMP_RATE = 12e6
SPU = 12


def overlaid(df, addr, payload=b"\x00\x19\x38"):
    body = bytes([df << 3]) + payload
    return (body + (crc.checksum(body + b"\x00" * 3, 56) ^ addr).to_bytes(3, "big")).hex().upper()


def stream_with(frames, length=60_000, seed=5, amplitude=0.4):
    rng = np.random.default_rng(seed)
    signal = (rng.normal(0, 0.014, length)
              + 1j * rng.normal(0, 0.014, length)).astype(np.complex64)
    for start, text in frames:
        frame = modes.synthesize(text, SPU, amplitude)
        signal[start:start + len(frame)] += frame.astype(np.complex64)
    return signal


def rx_time_tag(seconds=236_264, fraction=0.25, offset=0):
    """An rx_time tag in the UHD shape grx_source emits: (uint64 seconds, double fraction)."""
    tag = gr.tag_t()
    tag.offset = offset
    tag.key = pmt.intern("rx_time")
    tag.value = pmt.make_tuple(pmt.from_uint64(seconds), pmt.from_double(fraction))
    return tag


def run_block(signal, tags=(), timeout=20.0, **kwargs):
    """Runs the block over signal and returns the frame messages.

    The run is bounded: a Python block whose work raises loses its thread, and the flowgraph then
    never finishes. Waiting with a deadline turns that into a failure instead of a hung test.
    """
    top = gr.top_block()
    source = blocks.vector_source_c(signal.tolist(), False, 1, list(tags))
    block = modes_demod(SAMP_RATE, **kwargs)
    sink = blocks.message_debug()
    top.connect(source, block)
    top.msg_connect(block, "frames", sink, "store")
    top.start()
    waiter = threading.Thread(target=top.wait, daemon=True)
    waiter.start()
    waiter.join(timeout)
    finished = not waiter.is_alive()
    top.stop()
    top.wait()
    if not finished:
        raise AssertionError(f"the flowgraph ran longer than {timeout:g} s, a block thread ended early")
    return [sink.get_message(i) for i in range(sink.num_messages())]


def meta_of(msg, key):
    value = pmt.dict_ref(pmt.car(msg), pmt.intern(key), pmt.PMT_NIL)
    return None if pmt.is_null(value) else pmt.to_python(value)


def payload(msg):
    return bytes(pmt.u8vector_elements(pmt.cdr(msg))).hex().upper()


class qa_modes_blocks(gr_unittest.TestCase):
    def test_decodes_a_frame_from_a_complex_stream(self):
        messages = run_block(stream_with([(5000, KLM1023)]))
        self.assertEqual(len(messages), 1)
        self.assertEqual(payload(messages[0]), KLM1023)
        self.assertEqual(meta_of(messages[0], "df"), 17)
        self.assertEqual(meta_of(messages[0], "icao"), ADDRESS)
        self.assertEqual(meta_of(messages[0], "samp_rate"), SAMP_RATE)
        self.assertEqual(meta_of(messages[0], "spu"), SPU)
        self.assertEqual(meta_of(messages[0], "offset"), 5000)
        self.assertEqual(meta_of(messages[0], "errors"), 0)
        self.assertGreater(meta_of(messages[0], "score"), 0)
        self.assertGreater(meta_of(messages[0], "level"), 0.0)
        self.assertIn(meta_of(messages[0], "alignment"), (-2, -1, 0, 1, 2))

    def test_frames_across_many_work_calls(self):
        # Well past one buffer, so the stream is cut into several work calls and the frames sit
        # at different places inside them.
        places = [(7_000, KLM1023), (60_000, POSITION), (123_456, VELOCITY),
                  (199_000, KLM1023)]
        messages = run_block(stream_with(places, length=260_000, seed=9))
        self.assertEqual([payload(m) for m in messages], [text for _, text in places])
        self.assertEqual([meta_of(m, "offset") for m in messages],
                         [start for start, _ in places])

    def test_no_frames_in_noise(self):
        self.assertEqual(run_block(stream_with([], length=200_000, seed=11)), [])

    def test_short_reply_after_a_squitter(self):
        messages = run_block(stream_with([(5000, KLM1023), (30_000, overlaid(4, ADDRESS))]))
        self.assertEqual([meta_of(m, "df") for m in messages], [17, 4])

    def test_short_replies_off(self):
        messages = run_block(stream_with([(5000, KLM1023), (30_000, overlaid(4, ADDRESS))]),
                             short_replies=False)
        self.assertEqual([meta_of(m, "df") for m in messages], [17])

    def test_set_samp_rate_resizes_the_history(self):
        block = modes_demod(SAMP_RATE)
        self.assertEqual(block.history() - 1, modes.frame_length(SPU) + 2 * (SPU // 2))
        block.set_samp_rate(6e6)
        self.assertEqual(block.history() - 1, modes.frame_length(6) + 2 * (6 // 2))

    def test_rejects_an_odd_rate(self):
        with self.assertRaises(ValueError):
            modes_demod(5e6)

    def test_frame_count(self):
        block = modes_demod(SAMP_RATE)
        self.assertEqual(block.frame_count(), 0)


    def test_rx_time_tag_drives_the_clock(self):
        """The seconds of rx_time are a uint64, which pmt.to_double refuses. The block reads it anyway."""
        signal = stream_with([(5_000, KLM1023)])
        tagged = run_block(signal, tags=[rx_time_tag()])
        self.assertEqual([payload(message) for message in tagged], [KLM1023])
        self.assertEqual([payload(message) for message in run_block(signal)],
                         [payload(message) for message in tagged])


if __name__ == "__main__":
    gr_unittest.run(qa_modes_blocks)
