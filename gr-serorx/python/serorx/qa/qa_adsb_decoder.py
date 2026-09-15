import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

from gnuradio.serorx.adsb import adsb_decoder, crc, modes

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION = "8D40621D58C382D690C8AC2863A7"
ADDRESS = 0x4840D6
SPU = 12
# DF4 carrying 25000 ft, parity overlaid with the address of KLM1023.
REPLY_BODY = bytes.fromhex("20001030")
ALTITUDE_REPLY = (REPLY_BODY
                  + (crc.checksum(REPLY_BODY + b"\x00" * 3, 56) ^ ADDRESS)
                  .to_bytes(3, "big")).hex().upper()


def stream_with(frames, length=150_000, seed=1, spu=SPU, amplitude=0.3):
    rng = np.random.default_rng(seed)
    signal = (rng.normal(0, 0.014, length)
              + 1j * rng.normal(0, 0.014, length)).astype(np.complex64)
    for start, text in frames:
        frame = modes.synthesize(text, spu, amplitude)
        signal[start:start + len(frame)] += frame.astype(np.complex64)
    return signal


class qa_adsb_decoder(gr_unittest.TestCase):
    def run_signal(self, signal, **kwargs):
        tb = gr.top_block()
        dec = adsb_decoder(print_lines=False, **kwargs)
        lines = blocks.message_debug()
        fields = blocks.message_debug()
        tb.connect(blocks.vector_source_c(signal.tolist()), dec)
        tb.msg_connect(dec, "frames", lines, "store")
        tb.msg_connect(dec, "fields", fields, "store")
        tb.run()
        texts = [bytes(pmt.u8vector_elements(pmt.cdr(lines.get_message(i)))).decode()
                 for i in range(lines.num_messages())]
        records = [pmt.to_python(fields.get_message(i)) for i in range(fields.num_messages())]
        return dec, texts, records

    def test_frames_in_noise(self):
        dec, lines, _ = self.run_signal(
            stream_with([(5_000, KLM1023), (40_000, POSITION), (100_003, KLM1023)]))
        self.assertEqual(dec.frame_count(), 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].endswith("DF17 ICAO=4840D6 TC=4 callsign=KLM1023"), lines[0])
        self.assertTrue(lines[1].endswith("DF17 ICAO=40621D TC=11 altitude=38000"), lines[1])
        self.assertIn("DF17", lines[2])

    def test_short_reply_after_a_squitter(self):
        dec, lines, _ = self.run_signal(
            stream_with([(5_000, KLM1023), (40_000, ALTITUDE_REPLY)]))
        self.assertEqual(len(lines), 2)
        self.assertIn("DF17", lines[0])
        self.assertTrue(lines[1].endswith("DF4 ICAO=4840D6 alt=25000"), lines[1])

    def test_reply_alone_is_not_decoded(self):
        # Without a preceding squitter the address is unknown, so the reply cannot be validated.
        dec, lines, _ = self.run_signal(stream_with([(40_000, ALTITUDE_REPLY)]))
        self.assertEqual(lines, [])

    def test_fields_carry_the_score(self):
        _, _, records = self.run_signal(stream_with([(5_000, KLM1023)]))
        self.assertEqual(records[0]["df"], 17)
        self.assertEqual(records[0]["icao"], "4840D6")
        self.assertEqual(records[0]["callsign"], "KLM1023")
        self.assertGreater(records[0]["score"], 0)
        self.assertGreater(records[0]["level"], 0.0)
        self.assertEqual(records[0]["errors"], 0)

    def test_noise_only(self):
        dec, lines, _ = self.run_signal(stream_with([], length=200_000, seed=2))
        self.assertEqual(dec.frame_count(), 0)
        self.assertEqual(lines, [])

    def test_set_samp_rate(self):
        signal = stream_with([(5_000, KLM1023)], length=50_000, seed=3, spu=4)
        tb = gr.top_block()
        dec = adsb_decoder(print_lines=False)
        dec.set_samp_rate(4e6)
        tb.connect(blocks.vector_source_c(signal.tolist()), dec)
        tb.run()
        self.assertEqual(dec.frame_count(), 1)

    def test_bad_rate(self):
        with self.assertRaises(ValueError):
            adsb_decoder(samp_rate=3e6)


if __name__ == "__main__":
    gr_unittest.run(qa_adsb_decoder)
