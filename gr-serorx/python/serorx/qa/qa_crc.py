from gnuradio import gr_unittest

from gnuradio.serorx.adsb import crc

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION = "8D40621D58C382D690C8AC2863A7"


class qa_crc(gr_unittest.TestCase):
    def test_clean_frames(self):
        for text in (KLM1023, POSITION):
            self.assertEqual(crc.checksum(bytes.fromhex(text), 112), 0)

    def test_overlaid_frame_gives_the_address(self):
        # A short reply's checksum is the sender's address, so it is not zero.
        body = bytes([4 << 3, 0x00, 0x19, 0x38])
        frame = body + (crc.checksum(body + b"\x00" * 3, 56) ^ 0x4840D6).to_bytes(3, "big")
        self.assertEqual(crc.checksum(frame, 56), 0x4840D6)

    def test_single_bit_syndromes_are_unique(self):
        for bits in (56, 112):
            seen = {crc.bit_syndrome(i, bits) for i in range(bits)}
            self.assertEqual(len(seen), bits)

    def test_repairs_every_single_bit_error(self):
        clean = bytes.fromhex(KLM1023)
        for bit in range(112):
            broken = bytearray(clean)
            broken[bit // 8] ^= 0x80 >> (bit % 8)
            info = crc.diagnose(crc.checksum(broken, 112), 112, 1)
            self.assertIsNotNone(info, f"bit {bit} not diagnosed")
            self.assertEqual(info.errors, 1)
            self.assertEqual(info.positions, (bit,))
            self.assertEqual(crc.fix(broken, info), clean)

    def test_double_bit_needs_depth_two(self):
        clean = bytes.fromhex(KLM1023)
        broken = bytearray(clean)
        broken[3] ^= 0x20
        broken[9] ^= 0x01
        syndrome = crc.checksum(broken, 112)
        self.assertIsNone(crc.diagnose(syndrome, 112, 1))
        info = crc.diagnose(syndrome, 112, 2)
        self.assertEqual(info.errors, 2)
        self.assertEqual(crc.fix(broken, info), clean)

    def test_clean_syndrome_reports_no_errors(self):
        info = crc.diagnose(0, 112, 1)
        self.assertEqual(info.errors, 0)
        self.assertEqual(crc.fix(bytes.fromhex(KLM1023), info), bytes.fromhex(KLM1023))

    def test_unknown_syndrome(self):
        self.assertIsNone(crc.diagnose(0x123456, 112, 2))

    def test_correction_off_at_depth_zero(self):
        broken = bytearray(bytes.fromhex(KLM1023))
        broken[6] ^= 0x04
        self.assertIsNone(crc.diagnose(crc.checksum(broken, 112), 112, 0))

    def test_correct_address_follows_the_repair(self):
        clean = bytes.fromhex(KLM1023)
        broken = bytearray(clean)
        broken[2] ^= 0x08                      # bit 20, inside the address field
        info = crc.diagnose(crc.checksum(broken, 112), 112, 1)
        broken_addr = int.from_bytes(broken[1:4], "big")
        self.assertNotEqual(broken_addr, 0x4840D6)
        self.assertEqual(crc.correct_address(broken_addr, info), 0x4840D6)

    def test_correct_address_ignores_flips_outside_the_field(self):
        clean = bytes.fromhex(KLM1023)
        broken = bytearray(clean)
        broken[6] ^= 0x04                      # bit 53, past the address field
        info = crc.diagnose(crc.checksum(broken, 112), 112, 1)
        self.assertEqual(crc.correct_address(0x4840D6, info), 0x4840D6)

    def test_positions_stay_inside_the_frame(self):
        for bits in (56, 112):
            for bit in range(bits):
                info = crc.diagnose(crc.bit_syndrome(bit, bits), bits, 2)
                self.assertTrue(all(0 <= p < bits for p in info.positions))


if __name__ == "__main__":
    gr_unittest.run(qa_crc)
