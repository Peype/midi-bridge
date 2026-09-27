import mido

from stagekontrol_bridge.inputs import InputHub
from stagekontrol_bridge.usb import decode_control, encode_control


def test_sysex_de_controle_aller_retour():
    msg = encode_control({"type": "ping"})
    assert msg.type == "sysex"
    assert msg.bytes()[:4] == [0xF0, 0x7D, 0x53, 0x4B]
    assert decode_control(msg) == {"type": "ping"}


def test_accents_en_ascii_7_bits():
    value = {"type": "hello", "name": "Pey'pé"}
    msg = encode_control(value)
    assert all(b < 0x80 for b in msg.data)
    assert decode_control(msg) == value


def test_meme_format_que_l_app():
    # Octets produits par encodeControl({type:'ping'}) côté app (src/domain/usb-protocol.ts).
    app = [0xF0, 0x7D, 0x53, 0x4B] + [ord(c) for c in '{"type":"ping"}'] + [0xF7]
    assert decode_control(mido.Message.from_bytes(app)) == {"type": "ping"}


def test_autres_messages_ignores():
    assert decode_control(mido.Message("control_change", control=7, value=1)) is None
    assert decode_control(mido.Message("sysex", data=[0x43, 0x10])) is None


class FakePort:
    def __init__(self, name, callback=None):
        self.name, self.callback, self.sent, self.closed = name, callback, [], False

    def send(self, msg):
        self.sent.append(msg)

    def close(self):
        self.closed = True


def make_hub(inputs, outputs, keyboard=None):
    opened_in, opened_out = {}, {}
    events = {"keyboard": [], "phone_midi": [], "phone_control": [], "log": []}

    def open_input(name, callback):
        opened_in[name] = FakePort(name, callback)
        return opened_in[name]

    def open_output(name):
        opened_out[name] = FakePort(name)
        return opened_out[name]

    hub = InputHub(
        own_port="stageKontrol",
        keyboard=keyboard,
        on_keyboard=events["keyboard"].append,
        on_phone_midi=lambda phone, msg: events["phone_midi"].append((phone.name, msg)),
        on_phone_control=lambda phone, msg: events["phone_control"].append((phone.name, msg)),
        log=events["log"].append,
        list_inputs=lambda: list(inputs),
        list_outputs=lambda: list(outputs),
        open_input=open_input,
        open_output=open_output,
    )
    return hub, opened_in, opened_out, events


def test_le_telephone_est_reconnu_a_son_sysex_et_n_est_pas_un_clavier():
    hub, ins, outs, ev = make_hub(["Keystation", "RMX3842", "stageKontrol"], ["RMX3842", "stageKontrol"])
    hub.refresh()
    assert set(ins) == {"Keystation", "RMX3842"}  # jamais son propre port
    note = mido.Message("note_on", note=60, velocity=90)
    ins["Keystation"].callback(note)
    ins["RMX3842"].callback(encode_control({"type": "ping"}))
    ins["RMX3842"].callback(mido.Message("control_change", channel=1, control=7, value=90))
    assert ev["keyboard"] == [note]
    assert ev["phone_control"] == [("RMX3842", {"type": "ping"})]
    assert [(n, m.type) for n, m in ev["phone_midi"]] == [("RMX3842", "control_change")]
    # Réponse au téléphone par la sortie du même nom.
    hub.phones["RMX3842"].send_control({"type": "pong"})
    assert decode_control(outs["RMX3842"].sent[0]) == {"type": "pong"}


def test_clavier_choisi_avec_keyboard():
    hub, ins, _, ev = make_hub(["Keystation", "Autre"], [], keyboard="Keystation")
    hub.refresh()
    ins["Autre"].callback(mido.Message("note_on", note=60, velocity=90))
    ins["Keystation"].callback(mido.Message("note_on", note=62, velocity=90))
    assert [m.note for m in ev["keyboard"]] == [62]


def test_cable_branche_puis_debranche_en_cours_de_route():
    inputs = ["Keystation"]
    hub, ins, _, ev = make_hub(inputs, ["RMX3842"])
    hub.refresh()
    inputs.append("RMX3842")
    hub.refresh()
    ins["RMX3842"].callback(encode_control({"type": "ping"}))
    assert "RMX3842" in hub.phones
    inputs.remove("RMX3842")
    hub.refresh()
    assert "RMX3842" not in hub.phones and ins["RMX3842"].closed
    assert any("débranché" in line for line in ev["log"])
