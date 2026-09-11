import subprocess
import sys

from gnuradio import gr_unittest
from gnuradio.serorx import _protobuf_too_old
from gnuradio.serorx.proto import (Common_pb2, Monitord_pb2, Monitord_pb2_grpc, Samplestreamingd_pb2,
                                   Samplestreamingd_pb2_grpc, TunableChanneld_pb2, TunableChanneld_pb2_grpc)


class qa_proto(gr_unittest.TestCase):
    def test_band_enum(self):
        self.assertEqual(Common_pb2.BAND_TUNABLE, 128)

    def test_stubs_and_messages(self):
        self.assertTrue(hasattr(TunableChanneld_pb2_grpc, "TunableChanneldStub"))
        self.assertTrue(hasattr(Samplestreamingd_pb2_grpc, "SamplestreamingdStub"))
        self.assertTrue(hasattr(Monitord_pb2_grpc, "MonitordStub"))
        self.assertEqual(Monitord_pb2.SystemInformation(serial_number="s").serial_number, "s")

    def test_missing_grpcio_message(self):
        code = "import sys; sys.modules['grpc'] = None; import gnuradio.serorx"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"gr-serorx needs the grpcio and protobuf packages for {sys.executable}", result.stderr)
        self.assertIn("python3-grpcio", result.stderr)

    def test_protobuf_version_check(self):
        self.assertTrue(_protobuf_too_old("3.19.6"))
        self.assertFalse(_protobuf_too_old("3.20.0"))
        self.assertFalse(_protobuf_too_old("3.21.12"))
        self.assertFalse(_protobuf_too_old("4.25.3"))
        self.assertFalse(_protobuf_too_old("7.36.0"))
        self.assertFalse(_protobuf_too_old("unknown"))
        radio = Common_pb2.RadioIdentification(band=Common_pb2.BAND_TUNABLE, per_band_index=0)
        req = TunableChanneld_pb2.SetCenterFrequencyRequest(channel=radio, center_frequency_hz=1_090_000_000)
        self.assertEqual(req.center_frequency_hz, 1_090_000_000)
        self.assertEqual(Samplestreamingd_pb2.StartStreamReply(lost_blocks=3).lost_blocks, 3)


if __name__ == "__main__":
    gr_unittest.run(qa_proto)
