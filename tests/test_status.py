"""Page d'état : servie en HTTP sur le port du WebSocket, qui continue de fonctionner."""

import asyncio
import json
import urllib.request
from urllib.parse import parse_qs, urlparse

import mido
from websockets.asyncio.client import connect

from stagekontrol_bridge.midi_out import DryRunOutput, MidiPortError, WaitingOutput
from stagekontrol_bridge.server import Bridge
from stagekontrol_bridge.status import StatusPage, build_snapshot, connect_url, qr_svg


def snapshot(**over):
    base = dict(
        name="PC-Studio",
        port=8765,
        ips=["192.168.1.20"],
        midi_name="stageKontrol",
        midi_ok=True,
        midi_error=None,
        wifi_peer=None,
        usb_phones=[],
        keyboards=["Axiom 61"],
        keyboard_routing=True,
    )
    base.update(over)
    return build_snapshot(**base)


def test_lien_du_qr_code():
    url = connect_url("192.168.1.20", 8765, "PC de Pey'pé")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc) == ("stagekontrol", "connect")
    assert parse_qs(parsed.query) == {"host": ["192.168.1.20"], "port": ["8765"], "name": ["PC de Pey'pé"]}
    assert qr_svg(url).lstrip().startswith("<svg")


def test_etat_decrit():
    s = snapshot(midi_ok=False, midi_error="Port introuvable", usb_phones=["RMX3842"])
    assert s["connect_urls"][0]["url"].startswith("stagekontrol://connect?host=192.168.1.20")
    assert s["midi"]["ok"] is False and s["usb"] == ["RMX3842"]


def test_page_et_websocket_sur_le_meme_port():
    async def scenario():
        bridge = Bridge(DryRunOutput(), log=lambda _: None)
        page = StatusPage(lambda: snapshot())
        server = await bridge.start("127.0.0.1", 0, process_request=page.process_request)
        port = next(iter(server.sockets)).getsockname()[1]

        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                return r.headers.get("Content-Type"), r.read().decode()

        async with server:
            html_type, html = await asyncio.to_thread(get, "/")
            json_type, status = await asyncio.to_thread(get, "/status.json")
            svg_type, svg = await asyncio.to_thread(get, "/qr/0.svg")
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send('{"type":"ping"}')
                pong = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert html_type.startswith("text/html") and "StageKontrol" in html
        assert json_type == "application/json" and json.loads(status)["name"] == "PC-Studio"
        assert svg_type == "image/svg+xml" and "<svg" in svg
        assert pong == {"type": "pong"}

    asyncio.run(scenario())


def test_port_midi_qui_arrive_apres_le_lancement():
    created = {"ok": False}
    opened = []

    def opener(name):
        if not created["ok"]:
            raise MidiPortError(f"Port MIDI « {name} » introuvable.")
        opened.append(DryRunOutput())
        return opened[-1]

    out = WaitingOutput("stageKontrol", opener)
    assert not out.available and "introuvable" in out.error
    out.send(mido.Message("control_change", control=7, value=1))  # ignoré, sans erreur
    assert out.retry() is False
    created["ok"] = True  # l'utilisateur crée le port dans loopMIDI
    assert out.retry() is True and out.available and out.error is None
    out.send(mido.Message("control_change", control=7, value=2))
    assert opened[0].sent[0].value == 2
    assert out.retry() is False  # déjà ouvert
