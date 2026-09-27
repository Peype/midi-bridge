import pytest

from stagekontrol_bridge.midi_out import _find_windows_port
from stagekontrol_bridge.protocol import ProtocolError, describe, is_ping, parse, to_midi


def midi(raw: str):
    return to_midi(parse(raw))


def test_cc_canal_1_devient_0():
    m = midi('{"type":"midi","message":"cc","channel":1,"number":74,"value":96}')
    assert (m.type, m.channel, m.control, m.value) == ("control_change", 0, 74, 96)


def test_canal_16_devient_15():
    assert midi('{"type":"midi","message":"cc","channel":16,"number":7,"value":0}').channel == 15


def test_note_on_et_vrai_note_off():
    on = midi('{"type":"midi","message":"note","channel":16,"note":38,"velocity":127,"state":"on"}')
    off = midi('{"type":"midi","message":"note","channel":16,"note":38,"velocity":0,"state":"off"}')
    assert (on.type, on.channel, on.note, on.velocity) == ("note_on", 15, 38, 127)
    assert (off.type, off.note, off.velocity) == ("note_off", 38, 0)
    assert off.bytes()[0] == 0x8F  # Note Off (0x80) sur le canal 16


def test_program_change():
    m = midi('{"type":"midi","message":"program","channel":1,"program":2}')
    assert (m.type, m.channel, m.program) == ("program_change", 0, 2)


def test_ping():
    assert is_ping(parse('{"type":"ping"}'))
    assert not is_ping(parse('{"type":"midi"}'))


@pytest.mark.parametrize(
    "raw",
    [
        "pas du json",
        "[1, 2]",
        '{"type":"autre"}',
        '{"type":"midi","message":"cc","channel":0,"number":7,"value":1}',
        '{"type":"midi","message":"cc","channel":17,"number":7,"value":1}',
        '{"type":"midi","message":"cc","channel":1,"number":128,"value":1}',
        '{"type":"midi","message":"cc","channel":1,"number":7,"value":-1}',
        '{"type":"midi","message":"cc","channel":1,"number":7,"value":1.5}',
        '{"type":"midi","message":"cc","channel":true,"number":7,"value":1}',
        '{"type":"midi","message":"note","channel":1,"note":60,"velocity":100,"state":"maybe"}',
        '{"type":"midi","message":"sysex","channel":1}',
    ],
)
def test_messages_invalides(raw):
    with pytest.raises(ProtocolError):
        midi(raw)


def test_port_loopmidi_windows():
    assert _find_windows_port("stageKontrol", ["Microsoft GS", "stageKontrol 1"]) == "stageKontrol 1"
    assert _find_windows_port("stageKontrol", ["stageKontrol"]) == "stageKontrol"
    assert _find_windows_port("stageKontrol", ["stageKontrolX", "Microsoft GS"]) is None


def test_description_lisible_canal_1_a_16():
    assert describe(midi('{"type":"midi","message":"cc","channel":1,"number":7,"value":90}')) == "CC 7 = 90  (canal 1)"
    assert describe(
        midi('{"type":"midi","message":"note","channel":16,"note":38,"velocity":0,"state":"off"}')
    ) == "Note Off 38 vél. 0  (canal 16)"
