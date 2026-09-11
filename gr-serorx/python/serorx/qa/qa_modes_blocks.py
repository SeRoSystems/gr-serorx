from unittest import mock

import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

from gnuradio.serorx.adsb import adsb_fields, modes, modes_frame_check, modes_preamble, modes_slicer

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION_EVEN = "8D40621D58C382D690C8AC2863A7"
POSITION_ODD = "8D40621D58C386435CC412692AD6"
SPU = 12


def text_of(msg):
    """The text of a byte PDU."""
    return bytes(pmt.u8vector_elements(pmt.cdr(msg))).decode()


def signal_with(frames, length=150_000, seed=1):
    rng = np.random.default_rng(seed)
    mag = np.abs(rng.normal(0, 0.02, length) + 1j * rng.normal(0, 0.02, length)).astype(np.float32)
    for start, msg in frames:
        frame = modes.synthesize(msg, SPU, 0.3)
        mag[start:start + len(frame)] += frame
    return mag


class qa_modes_blocks(gr_unittest.TestCase):
    def run_chain(self, mag, tags=(), print_lines=False):
        tb = gr.top_block()
        src = blocks.vector_source_f(mag.tolist(), False, 1, list(tags))
        preamble = modes_preamble(samp_rate=12e6)
        slicer = modes_slicer()
        check = modes_frame_check()
        fields = adsb_fields(print_lines=print_lines)
        debug = {name: blocks.message_debug() for name in ("windows", "bits", "confidence", "frames", "hex", "lines", "fields", "accepted")}
        tb.connect(src, preamble)
        tb.msg_connect(preamble, "windows", slicer, "windows")
        tb.msg_connect(slicer, "bits", check, "bits")
        tb.msg_connect(check, "frames", fields, "frames")
        for block, port, name in ((preamble, "windows", "windows"), (slicer, "bits", "bits"), (slicer, "confidence", "confidence"),
                                  (check, "frames", "frames"), (check, "hex", "hex"), (fields, "lines", "lines"),
                                  (fields, "fields", "fields"), (check, "windows", "accepted")):
            tb.msg_connect(block, port, debug[name], "store")
        tb.run()
        stored = {port: [dbg.get_message(i) for i in range(dbg.num_messages())] for port, dbg in debug.items()}
        return preamble, check, fields, stored

    def test_chain(self):
        mag = signal_with([(5_000, KLM1023), (40_000, POSITION_EVEN), (80_000, POSITION_ODD)])
        preamble, check, fields, stored = self.run_chain(mag)
        self.assertGreaterEqual(preamble.candidate_count(), 3)
        self.assertEqual(len(stored["windows"]), preamble.candidate_count())
        self.assertEqual(len(stored["bits"]), len(stored["windows"]))
        self.assertEqual(len(stored["confidence"]), len(stored["windows"]))
        self.assertEqual([text_of(m) for m in stored["hex"]], [KLM1023, POSITION_EVEN, POSITION_ODD])
        self.assertEqual(check.frame_count(), 3)
        self.assertEqual(fields.frame_count(), 3)
        lines = [text_of(m) for m in stored["lines"]]
        self.assertTrue(lines[0].endswith("DF17 ICAO=4840D6 TC=4 callsign=KLM1023"), lines[0])
        self.assertTrue(lines[1].endswith("DF17 ICAO=40621D TC=11 altitude=38000"), lines[1])
        self.assertIn("latitude=52.2657", lines[2])
        record = pmt.to_python(stored["fields"][2])
        self.assertEqual((record["df"], record["icao"], record["tc"]), (17, "40621D", 11))
        self.assertEqual(bytes(record["frame"]).hex().upper(), POSITION_ODD)
        self.assertEqual(record["altitude"], 38000)
        frame_meta = pmt.to_python(pmt.car(stored["frames"][0]))
        self.assertEqual((frame_meta["df"], frame_meta["corrected"]), (17, False))
        self.assertNotIn("hex", frame_meta)
        self.assertEqual(frame_meta["offset"], 5_000)
        window = pmt.to_python(pmt.cdr(stored["windows"][0]))
        self.assertEqual(len(window), 1440)
        self.assertEqual(len(stored["accepted"]), 3)
        accepted = pmt.to_python(pmt.cdr(stored["accepted"][0]))
        self.assertEqual(len(accepted), 1440)
        self.assertGreater(max(accepted), 0.2)
        self.assertNotIn("window", frame_meta)
        confidence = pmt.to_python(pmt.cdr(stored["confidence"][0]))
        self.assertEqual(len(confidence), 112)
        self.assertGreater(confidence[0], 0)

    def test_rx_time_tag_gives_frame_time(self):
        mag = signal_with([(5_000, KLM1023)])
        tag = gr.tag_utils.python_to_tag((0, pmt.intern("rx_time"),
                                          pmt.make_tuple(pmt.from_uint64(100), pmt.from_double(0.5)), pmt.intern("qa")))
        _, _, _, stored = self.run_chain(mag, tags=[tag])
        meta = pmt.to_python(pmt.car(stored["frames"][0]))
        self.assertAlmostEqual(meta["time"], 100.5 + 5_000 / 12e6, places=9)
        record = pmt.to_python(stored["fields"][0])
        self.assertAlmostEqual(record["time"], meta["time"], places=9)

    def test_correction_counts(self):
        mag = signal_with([(5_000, KLM1023)])
        start = 5_000 + (modes.PREAMBLE_US + 42) * SPU
        mag[start:start + SPU // 2] = 0.14
        mag[start + SPU // 2:start + SPU] = 0.15
        _, check, _, stored = self.run_chain(mag)
        self.assertEqual([text_of(m) for m in stored["hex"]], [KLM1023])
        self.assertEqual(check.corrected_count(), 1)
        self.assertTrue(pmt.to_python(pmt.car(stored["frames"][0]))["corrected"])

    def test_string_input_and_df_filter(self):
        tb = gr.top_block()
        fields = adsb_fields(print_lines=False)
        strobe = blocks.message_strobe(pmt.intern(KLM1023.lower()), 50)
        debug = blocks.message_debug()
        tb.msg_connect(strobe, "strobe", fields, "frames")
        tb.msg_connect(fields, "lines", debug, "store")
        tb.start()
        import time
        time.sleep(0.3)
        tb.stop()
        tb.wait()
        self.assertGreaterEqual(debug.num_messages(), 1)
        self.assertTrue(text_of(debug.get_message(0)).endswith("callsign=KLM1023"))
        check = modes_frame_check(accepted="11")
        self.assertEqual(check._accepted, (11,))

    def test_wrong_type_pdus_are_ignored(self):
        f32 = pmt.cons(pmt.PMT_NIL, pmt.init_f32vector(3, [0.0, 1.0, 2.0]))
        u8 = pmt.cons(pmt.PMT_NIL, pmt.init_u8vector(3, [1, 2, 3]))
        for block, bad in ((modes_slicer(), u8), (modes_frame_check(), f32), (adsb_fields(print_lines=False), f32)):
            with mock.patch.object(type(block), "_warn") as warn:
                block._handle(bad)
                block._handle(bad)
            self.assertEqual(warn.call_count, 1, type(block).__name__)
        fields = adsb_fields(print_lines=False)
        fields._handle(f32)
        fields._handle(pmt.intern("not hex at all, but 28 chars"))
        fields._handle(pmt.intern(KLM1023))
        self.assertEqual(fields.frame_count(), 1)

    def test_bad_rx_time_tag_is_ignored(self):
        mag = signal_with([(5_000, KLM1023)])
        tag = gr.tag_utils.python_to_tag((0, pmt.intern("rx_time"), pmt.from_double(1.0), pmt.intern("qa")))
        _, check, _, stored = self.run_chain(mag, tags=[tag])
        self.assertEqual(check.frame_count(), 1)
        self.assertNotIn("time", pmt.to_python(pmt.car(stored["frames"][0])))

    def test_position_ages_use_stream_clock(self):
        fields = adsb_fields(print_lines=False)
        decoder = mock.Mock(wraps=fields._decoder)
        fields._decoder = decoder

        def pdu(msg, offset, gps_time):
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("offset"), pmt.from_uint64(offset))
            meta = pmt.dict_add(meta, pmt.intern("samp_rate"), pmt.from_double(12e6))
            meta = pmt.dict_add(meta, pmt.intern("time"), pmt.from_double(gps_time))
            return pmt.cons(meta, pmt.init_u8vector(14, list(bytes.fromhex(msg))))

        fields._handle(pdu(POSITION_EVEN, 0, 604799.0))
        fields._handle(pdu(POSITION_ODD, 12_000_000 * 100, 99.0))
        self.assertEqual([call.args[1] for call in decoder.decode.call_args_list], [0.0, 100.0])

    def test_set_samp_rate_changes_detection(self):
        rng = np.random.default_rng(1)
        mag = np.abs(rng.normal(0, 0.02, 50_000) + 1j * rng.normal(0, 0.02, 50_000)).astype(np.float32)
        frame = modes.synthesize(KLM1023, 4, 0.3)
        mag[5_000:5_000 + len(frame)] += frame
        tb = gr.top_block()
        preamble = modes_preamble(samp_rate=12e6)
        preamble.set_samp_rate(4e6)
        slicer, check = modes_slicer(), modes_frame_check()
        tb.connect(blocks.vector_source_f(mag.tolist()), preamble)
        tb.msg_connect(preamble, "windows", slicer, "windows")
        tb.msg_connect(slicer, "bits", check, "bits")
        tb.run()
        self.assertEqual(check.frame_count(), 1)


if __name__ == "__main__":
    gr_unittest.run(qa_modes_blocks)
