import numpy as np
from gnuradio import gr_unittest

from gnuradio.serorx.adsb import crc, demod, modes

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION = "8D40621D58C382D690C8AC2863A7"
ADDRESS = 0x4840D6
SPU = 12


def overlaid(df, addr, payload=b"\x00\x19\x38"):
    body = bytes([df << 3]) + payload
    return (body + (crc.checksum(body + b"\x00" * 3, 56) ^ addr).to_bytes(3, "big")).hex().upper()


def buffer_with(frames, length=60_000, seed=5, amplitude=0.4):
    rng = np.random.default_rng(seed)
    signal = np.abs(rng.normal(0, 0.02, length)
                    + 1j * rng.normal(0, 0.02, length)).astype(np.float32)
    for start, text in frames:
        frame = modes.synthesize(text, SPU, amplitude)
        signal[start:start + len(frame)] += frame
    return signal


def hexes(frames):
    return [f.data.hex().upper() for f in frames]


class qa_demod(gr_unittest.TestCase):
    def test_decodes_an_extended_squitter(self):
        found = demod.Demod(SPU).process(buffer_with([(5000, KLM1023)]), 0, 0.0)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].data.hex().upper(), KLM1023)
        self.assertEqual(found[0].df, 17)
        self.assertEqual(found[0].icao, ADDRESS)
        self.assertEqual(found[0].errors, 0)
        self.assertGreater(found[0].level, 0.0)
        self.assertEqual(found[0].offset, 5000)
        self.assertEqual(len(found[0].confidence), 112)

    def test_offset_is_absolute(self):
        found = demod.Demod(SPU).process(buffer_with([(5000, KLM1023)]), 1_000_000, 0.0)
        self.assertEqual(found[0].offset, 1_005_000)

    def test_decodes_at_every_alignment(self):
        for shift in (-2, -1, 0, 1, 2):
            found = demod.Demod(SPU).process(buffer_with([(5000 + shift, KLM1023)]), 0, 0.0)
            self.assertEqual(hexes(found), [KLM1023], f"shift {shift}")

    def test_short_reply_needs_a_prior_squitter(self):
        reply = overlaid(4, ADDRESS)
        alone = demod.Demod(SPU).process(buffer_with([(5000, reply)]), 0, 0.0)
        self.assertEqual([f.df for f in alone], [])
        both = demod.Demod(SPU).process(buffer_with([(5000, KLM1023), (20_000, reply)]), 0, 0.0)
        self.assertEqual([f.df for f in both], [17, 4])
        self.assertEqual(both[1].icao, ADDRESS)
        self.assertEqual(len(both[1].data), 7)

    def test_short_replies_can_be_switched_off(self):
        engine = demod.Demod(SPU, short_replies=False)
        found = engine.process(buffer_with([(5000, KLM1023), (20_000, overlaid(4, ADDRESS))]),
                               0, 0.0)
        self.assertEqual([f.df for f in found], [17])

    def test_repairs_one_flipped_bit(self):
        broken = bytearray(bytes.fromhex(KLM1023))
        broken[6] ^= 0x04
        found = demod.Demod(SPU, correct_bits=1).process(
            buffer_with([(5000, broken.hex().upper())]), 0, 0.0)
        self.assertEqual(hexes(found), [KLM1023])
        self.assertEqual(found[0].errors, 1)

    def test_two_flipped_bits_need_depth_two(self):
        broken = bytearray(bytes.fromhex(KLM1023))
        broken[6] ^= 0x04
        broken[11] ^= 0x40
        signal = buffer_with([(5000, broken.hex().upper())])
        self.assertEqual(demod.Demod(SPU, correct_bits=1).process(signal, 0, 0.0), [])
        found = demod.Demod(SPU, correct_bits=2).process(signal, 0, 0.0)
        self.assertEqual(hexes(found), [KLM1023])
        self.assertEqual(found[0].errors, 2)

    def test_skip_matches_readsb(self):
        # Five sixths of the data bits: 93.3 us of a 120 us long frame, 46.7 us of a short one.
        self.assertEqual(demod.skip_samples(112, SPU), 1120)
        self.assertEqual(demod.skip_samples(56, SPU), 560)

    def test_back_to_back_frames(self):
        # The skip ends well inside the first frame, so a frame starting the moment the previous
        # one ends is still offered to the detector.
        second = 5000 + modes.frame_length(SPU)
        self.assertGreater(second, 5000 + demod.skip_samples(112, SPU))
        found = demod.Demod(SPU).process(buffer_with([(5000, KLM1023), (second, POSITION)]),
                                         0, 0.0)
        self.assertEqual(hexes(found), [KLM1023, POSITION])

    def test_duplicate_dropped(self):
        gap = SPU * 500                        # 500 us, inside the 1 ms window
        found = demod.Demod(SPU).process(buffer_with([(5000, KLM1023), (5000 + gap, KLM1023)]),
                                         0, 0.0)
        self.assertEqual(len(found), 1)

    def test_repeat_outside_the_window_is_kept(self):
        gap = SPU * 2000                       # 2 ms, outside the 1 ms window
        found = demod.Demod(SPU).process(buffer_with([(5000, KLM1023), (5000 + gap, KLM1023)]),
                                         0, 0.0)
        self.assertEqual(len(found), 2)

    def test_noise_alone_decodes_nothing(self):
        rng = np.random.default_rng(11)
        signal = np.abs(rng.normal(0, 0.02, 200_000)
                        + 1j * rng.normal(0, 0.02, 200_000)).astype(np.float32)
        self.assertEqual(demod.Demod(SPU).process(signal, 0, 0.0), [])

    def test_guard_is_reported(self):
        self.assertEqual(demod.Demod(SPU).guard, SPU // 2)


if __name__ == "__main__":
    gr_unittest.run(qa_demod)
