import threading
import time

import grpc
from gnuradio import gr_unittest

import fake_grx
from gnuradio.serorx.grx_client import GrxError, GrxStream, radio_id
from gnuradio.serorx.grx_decode import SPECS, STREAM_NAMES, GrxDecode
from gnuradio.serorx.proto import Receiverd_pb2

BLOCKS = 3 * fake_grx.FRAME_EVERY


class qa_grx_decode(gr_unittest.TestCase):
    def setUp(self):
        self.fake = fake_grx.FakeGrx(realtime=False).start()
        self.decode = GrxDecode("127.0.0.1", self.fake.decode_port, 2.0)

    def tearDown(self):
        self.decode.close()
        self.fake.stop()

    def pump(self, blocks=BLOCKS):
        """Runs the 1090 sample stream, which publishes the decoded items."""
        stream = GrxStream("127.0.0.1", self.fake.stream_port, 2.0)
        thread = threading.Thread(target=lambda: [None for _ in stream.start(radio_id("1090"), blocks)], daemon=True)
        thread.start()
        return thread

    def first(self, names):
        """The first item of every named stream, collected while the sample stream runs."""
        found = {}
        calls = {name: self.decode.start(name) for name in names}
        threads = []
        for name, call in calls.items():
            def take(name=name, call=call):
                for item in call:
                    found[name] = item
                    break
            thread = threading.Thread(target=take, daemon=True)
            thread.start()
            threads.append(thread)
        time.sleep(0.2)
        self.pump()
        for thread in threads:
            thread.join(timeout=10)
        for call in calls.values():
            call.cancel()
        return found

    def test_every_stream_delivers(self):
        found = self.first(STREAM_NAMES)
        self.assertEqual(sorted(found), sorted(STREAM_NAMES))
        for name, item in found.items():
            self.assertTrue(item.timed, name)
            self.assertEqual(item.base, Receiverd_pb2.GPS_TOW)
            self.assertGreater(item.timestamp, 0)
            self.assertEqual(item.dropped, 0)

    def test_mode_s_fields(self):
        item = self.first(["modes_downlink"])["modes_downlink"]
        self.assertEqual(item.fields["payload"].hex().upper(), fake_grx.ADSB_FRAMES[0])
        self.assertEqual(item.fields["df"], 17)
        self.assertTrue(item.fields["message_valid"])
        self.assertTrue(item.fields["address_tracked"])
        self.assertEqual(item.fields["level_signal"], fake_grx.SIGNAL_LEVEL)
        self.assertEqual(item.fields["level_noise"], fake_grx.NOISE_LEVEL)
        self.assertNotIn("carrier_offset", item.fields)

    def test_stream_specific_fields(self):
        found = self.first(["modeac_downlink", "dme_tacan", "isolated_pulses", "mode123ac_interrogations"])
        self.assertEqual(found["modeac_downlink"].fields["code"], fake_grx.MODEAC_CODE)
        self.assertEqual(found["modeac_downlink"].fields["receptions"], 1)
        self.assertEqual(found["dme_tacan"].fields["spacing"], 12)
        self.assertEqual(found["dme_tacan"].fields["band"], "1030")
        self.assertEqual(found["isolated_pulses"].fields["duration"], fake_grx.PULSE_DURATION)
        self.assertEqual(found["isolated_pulses"].fields["band"], "1090")
        self.assertEqual(found["mode123ac_interrogations"].fields["mode"], "MODE_A")
        self.assertTrue(found["mode123ac_interrogations"].fields["all_call"])

    def test_requests_carry_the_format_lists(self):
        self.assertEqual(tuple(SPECS["modes_downlink"].request().downlink_formats), tuple(range(25)))
        self.assertEqual(tuple(SPECS["modes_uplink"].request().uplink_formats), tuple(range(25)))
        self.assertEqual(tuple(SPECS["uat_adsb"].request().payload_type_codes), tuple(range(32)))

    def test_statistics_answers(self):
        self.assertIsNotNone(self.decode.statistics())

    def test_cancel_ends_the_stream(self):
        call = self.decode.start("mode5_replies")
        call.cancel()
        with self.assertRaises(GrxError) as caught:
            next(call)
        self.assertEqual(caught.exception.code, grpc.StatusCode.CANCELLED)

    def test_closed_port(self):
        self.fake.stop()
        decode = GrxDecode("127.0.0.1", self.fake.decode_port, 1.0)
        with self.assertRaises(GrxError) as caught:
            decode.statistics()
        self.assertEqual(caught.exception.code, grpc.StatusCode.UNAVAILABLE)
        decode.close()

    def test_unknown_stream(self):
        with self.assertRaises(KeyError):
            self.decode.start("nothing")


if __name__ == "__main__":
    gr_unittest.run(qa_grx_decode)
