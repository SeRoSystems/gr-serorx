from gnuradio import gr_unittest

from gnuradio.serorx.adsb import message

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION_EVEN = "8D40621D58C382D690C8AC2863A7"
POSITION_ODD = "8D40621D58C386435CC412692AD6"
VELOCITY_GS = "8D485020994409940838175B284F"
VELOCITY_TAS = "8DA05F219B06B6AF189400CBC33F"


class qa_adsb(gr_unittest.TestCase):
    def test_header(self):
        self.assertEqual((message.df(KLM1023), message.icao(KLM1023), message.typecode(KLM1023)), (17, "4840D6", 4))
        self.assertEqual(len(message.bits(KLM1023)), 112)

    def test_callsign(self):
        self.assertEqual(message.callsign(KLM1023), "KLM1023")

    def test_altitude(self):
        self.assertEqual(message.altitude(POSITION_EVEN), 38000)
        self.assertEqual(message.altitude(POSITION_ODD), 38000)
        self.assertIsNone(message.altitude(KLM1023))

    def test_gillham(self):
        # C1 A1 C2 A2 C4 A4 B1 D1 B2 D2 B4 D4
        self.assertEqual(message.gillham("001000000000"), -1000)
        self.assertEqual(message.gillham("000010001010"), -200)
        self.assertEqual(message.gillham("001000101000"), 1000)
        self.assertIsNone(message.gillham("000000000000"))

    def test_nl(self):
        self.assertEqual(message.nl(0), 59)
        self.assertEqual(message.nl(52.2572), 36)
        self.assertEqual(message.nl(87), 2)
        self.assertEqual(message.nl(89), 1)

    def test_global_position(self):
        lat, lon = message.position(POSITION_EVEN, POSITION_ODD, even_newer=True)
        self.assertAlmostEqual(lat, 52.2572, places=4)
        self.assertAlmostEqual(lon, 3.91937, places=4)
        lat, lon = message.position(POSITION_EVEN, POSITION_ODD, even_newer=False)
        self.assertAlmostEqual(lat, 52.26578, places=4)
        self.assertAlmostEqual(lon, 3.92, delta=0.05)

    def test_velocity(self):
        gs = message.velocity(VELOCITY_GS)
        self.assertEqual(gs["speed"], 159)
        self.assertAlmostEqual(gs["track"], 182.88, places=2)
        self.assertEqual(gs["vertical_rate"], -832)
        tas = message.velocity(VELOCITY_TAS)
        self.assertEqual((tas["airspeed"], tas["airspeed_type"]), (375, "TAS"))
        self.assertAlmostEqual(tas["heading"], 243.98, places=2)
        self.assertEqual(tas["vertical_rate"], -2304)

    def test_decoder_pairs_positions(self):
        decoder = message.Decoder()
        self.assertEqual(decoder.decode(KLM1023, 0.0), {"callsign": "KLM1023"})
        self.assertEqual(decoder.decode(POSITION_ODD, 1.0), {"altitude": 38000})
        fields = decoder.decode(POSITION_EVEN, 2.0)
        self.assertEqual(fields["altitude"], 38000)
        self.assertAlmostEqual(fields["latitude"], 52.2572, places=4)
        self.assertAlmostEqual(fields["longitude"], 3.91937, places=4)
        self.assertEqual(decoder.decode(POSITION_ODD, 20.0), {"altitude": 38000})
        self.assertIn("speed", decoder.decode(VELOCITY_GS, 21.0))


if __name__ == "__main__":
    gr_unittest.run(qa_adsb)
