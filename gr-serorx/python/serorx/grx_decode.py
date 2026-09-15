"""Wrapper over the GRX gRPC service Receiverd, the receiver's own decoder output. No GNU Radio dependency."""
from dataclasses import dataclass

import grpc
from google.protobuf import empty_pb2

from .grx_client import BAND_NAMES, GrxError, _Client
from .proto import Common_pb2, Receiverd_pb2, Receiverd_pb2_grpc

GPS_TOW = Receiverd_pb2.GPS_TOW
DOWNLINK_FORMATS = tuple(range(25))
ANY_RADIO = Receiverd_pb2.RadioIdentificationEnable(band=Common_pb2.BAND_MATCH_ANY, per_band_index=-1)
_CAP = Receiverd_pb2.ReceptionCapabilities
# The two streams of variable band take any of the four bands the receiver counts them on.
ANY_DME_TACAN = (_CAP.DOWNLINK_DME_TACAN_PULSE_PAIR, _CAP.UPLINK_DME_TACAN_PULSE_PAIR,
                 _CAP.UAT_DME_TACAN_PULSE_PAIR, _CAP.GENERIC_DME_TACAN_PULSE_PAIR)
ANY_ISOLATED_PULSE = (_CAP.DOWNLINK_ISOLATED_PULSE, _CAP.UPLINK_ISOLATED_PULSE,
                      _CAP.UAT_ISOLATED_PULSE, _CAP.GENERIC_ISOLATED_PULSE)


@dataclass(frozen=True)
class Decode:
    """One decoded item. timestamp is nanoseconds in the base named by base, dropped the stream counter."""
    name: str
    timestamp: int
    base: int
    fields: dict
    dropped: int

    @property
    def timed(self):
        return self.base == GPS_TOW


@dataclass(frozen=True)
class StreamSpec:
    """One Receiverd stream. band names the channel whose IQ carries the signal, '' when the item names it.

    capabilities are the ReceptionCapabilities entries a receiver reports for it. It delivers items
    when it reports any of them.
    """
    name: str
    label: str
    band: str
    rpc: str
    request: object
    container: str
    dropped: str
    fields: object
    capabilities: tuple


def _band_name(band):
    return BAND_NAMES.get(band, str(band))


def _levels(item):
    return {"level_signal": item.level_signal, "level_noise": item.level_noise}


def _carrier(item):
    if not item.carrier_frequency_offset_computed:
        return {}
    return {"carrier_offset": item.carrier_frequency_offset, "carrier_error": item.carrier_frequency_estimation_error}


def _modes_fields(item, key):
    fields = {"payload": bytes(item.payload), "corrected_bits": item.number_corrected_bits,
              "address_tracked": item.confirmation_flags.address_tracked,
              "message_valid": item.confirmation_flags.message_valid}
    if item.payload:
        fields[key] = item.payload[0] >> 3
    fields.update(_levels(item))
    fields.update(_carrier(item))
    return fields


def _uat_fields(item):
    fields = {"payload": bytes(item.payload), "corrected_bits": item.number_corrected_bits}
    fields.update(_levels(item))
    fields.update(_carrier(item))
    return fields


def _modeac_fields(item):
    first = item.receptions[0] if item.receptions else None
    return {"code": item.code, "receptions": len(item.receptions),
            "level_signal": first.signal_level if first is not None else float("nan")}


def _dme_fields(item):
    fields = {"spacing": item.spacing, "band": _band_name(item.band), "channel_index": item.per_band_index}
    fields.update(_levels(item))
    return fields


def _pulse_fields(item):
    fields = {"duration": item.duration, "band": _band_name(item.band), "channel_index": item.per_band_index}
    fields.update(_levels(item))
    return fields


def _mode4_interrogation_fields(item):
    fields = _levels(item)
    fields["sls_level"] = item.sls_level
    return fields


def _mode123ac_fields(item):
    fields = {"mode": Receiverd_pb2.Mode123ACInterrogation.Mode.Name(item.mode), "all_call": item.all_call,
              "include_mode_s": item.include_mode_s, "s1_level": item.s1_level, "sls_level": item.sls_level}
    fields.update(_levels(item))
    return fields


STREAMS = (
    StreamSpec("modes_downlink", "Mode S downlink", "1090", "GetModeSDownlinkFrames",
               lambda: Receiverd_pb2.GetModeSDownlinkFramesRequest(downlink_formats=DOWNLINK_FORMATS),
               "frame", "frames_dropped", lambda item: _modes_fields(item, "df"), (_CAP.DOWNLINK_MODE_S_FRAME,)),
    StreamSpec("modes_uplink", "Mode S uplink", "1030", "GetModeSUplinkFrames",
               lambda: Receiverd_pb2.GetModeSUplinkFramesRequest(uplink_formats=DOWNLINK_FORMATS),
               "frame", "frames_dropped", lambda item: _modes_fields(item, "uf"), (_CAP.UPLINK_MODE_S_FRAME,)),
    StreamSpec("modeac_downlink", "Mode A/C downlink", "1090", "GetModeACDownlinkFrames",
               lambda: Receiverd_pb2.GetModeACDownlinkFramesRequest(
                   binning_mode=Receiverd_pb2.GetModeACDownlinkFramesRequest.ALL),
               "frame", "frames_dropped", _modeac_fields, (_CAP.DOWNLINK_MODE_AC_REPLY,)),
    StreamSpec("uat_adsb", "UAT ADS-B", "978", "GetUATADSBMessages",
               lambda: Receiverd_pb2.GetUATADSBMessagesRequest(payload_type_codes=tuple(range(32))),
               "message", "messages_dropped", _uat_fields, (_CAP.UAT_ADS_B_MESSAGE,)),
    StreamSpec("uat_uplink", "UAT ground uplink", "978", "GetUATGroundUplinkMessages",
               Receiverd_pb2.GetUATGroundUplinkMessagesRequest, "message", "messages_dropped", _uat_fields,
               (_CAP.UAT_GROUND_UPLINK_MESSAGE,)),
    StreamSpec("dme_tacan", "DME/TACAN pulse pairs", "", "GetDMETACANPulsePairs",
               lambda: Receiverd_pb2.GetDMETACANPulsePairsRequest(enabled_radios=[ANY_RADIO]),
               "message", "messages_dropped", _dme_fields, ANY_DME_TACAN),
    StreamSpec("isolated_pulses", "Isolated pulses", "", "GetIsolatedPulses",
               lambda: Receiverd_pb2.GetIsolatedPulsesRequest(enabled_radios=[ANY_RADIO]),
               "message", "messages_dropped", _pulse_fields, ANY_ISOLATED_PULSE),
    StreamSpec("mode4_interrogations", "Mode 4 interrogations", "1030", "GetMode4Interrogations",
               Receiverd_pb2.GetMode4InterrogationsRequest, "message", "messages_dropped",
               _mode4_interrogation_fields, (_CAP.UPLINK_MODE_4_INTERROGATION,)),
    StreamSpec("mode4_replies", "Mode 4 replies", "1090", "GetMode4Replies",
               Receiverd_pb2.GetMode4RepliesRequest, "message", "messages_dropped", _levels,
               (_CAP.DOWNLINK_MODE_4_REPLY,)),
    StreamSpec("mode5_interrogations", "Mode 5 interrogations", "1030", "GetMode5Interrogations",
               Receiverd_pb2.GetMode5InterrogationsRequest, "message", "messages_dropped", _levels,
               (_CAP.UPLINK_MODE_5_INTERROGATION,)),
    StreamSpec("mode5_replies", "Mode 5 replies", "1090", "GetMode5Replies",
               Receiverd_pb2.GetMode5RepliesRequest, "message", "messages_dropped", _levels,
               (_CAP.DOWNLINK_MODE_5_REPLY,)),
    StreamSpec("mode123ac_interrogations", "Mode 1/2/3 A/C interrogations", "1030", "GetMode123ACInterrogations",
               Receiverd_pb2.GetMode123ACInterrogationsRequest, "message", "messages_dropped", _mode123ac_fields,
               (_CAP.UPLINK_BASIC_INTERROGATION,)),
)
SPECS = {spec.name: spec for spec in STREAMS}
STREAM_NAMES = tuple(spec.name for spec in STREAMS)


class DecodeIterator:
    """Iterates one Receiverd stream as Decode. cancel() ends the RPC."""

    def __init__(self, call, endpoint, spec):
        self._call = call
        self._endpoint = endpoint
        self._spec = spec

    def __iter__(self):
        return self

    def __next__(self):
        try:
            reply = next(self._call)
        except grpc.RpcError as err:
            raise GrxError(self._endpoint, err.code(), err.details()) from None
        item = getattr(reply, self._spec.container)
        return Decode(self._spec.name, item.timestamp, item.timing_base, self._spec.fields(item),
                      getattr(reply, self._spec.dropped))

    def cancel(self):
        self._call.cancel()


class GrxDecode(_Client):
    """Receiverd, port 5303. The twelve decoder streams, one RPC each."""

    def __init__(self, host, port=5303, timeout=5.0):
        super().__init__(host, port, timeout)
        self._stub = Receiverd_pb2_grpc.ReceiverdStub(self._channel)

    def statistics(self):
        """Signal counters and rates. The unary call that proves the service answers."""
        return self._call(self._stub.GetStatistics, empty_pb2.Empty())

    def start(self, name):
        spec = SPECS[name]
        return DecodeIterator(getattr(self._stub, spec.rpc)(spec.request()), self.endpoint, spec)
