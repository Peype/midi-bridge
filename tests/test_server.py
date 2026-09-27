"""Serveur réel sur un port libre, avec de vrais clients WebSocket et une sortie MIDI de test."""

import asyncio
import json

import mido
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from stagekontrol_bridge.midi_out import DryRunOutput
from stagekontrol_bridge.protocol import CLOSE_REPLACED
from stagekontrol_bridge.router import Router
from stagekontrol_bridge.server import Bridge

CC = json.dumps({"type": "midi", "message": "cc", "channel": 2, "number": 7, "value": 90})


async def start_bridge():
    out, logs = DryRunOutput(), []
    bridge = Bridge(out, log=logs.append)
    server = await bridge.start("127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    return server, f"ws://127.0.0.1:{port}", out, logs


def test_ping_pong_et_midi():
    async def scenario():
        server, url, out, logs = await start_bridge()
        async with server:
            async with connect(url) as ws:
                await ws.send(json.dumps({"type": "ping"}))
                assert json.loads(await ws.recv()) == {"type": "pong"}
                await ws.send(CC)
                await ws.send("illisible")
                await asyncio.sleep(0.1)
        assert [(m.type, m.channel, m.value) for m in out.sent] == [("control_change", 1, 90)]
        assert any("message ignoré" in line for line in logs)

    asyncio.run(scenario())


def test_un_seul_appareil_a_la_fois():
    async def scenario():
        server, url, out, _ = await start_bridge()
        async with server:
            first = await connect(url)
            second = await connect(url)
            # Le premier appareil est fermé avec le code « remplacé ».
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(first.recv(), 2)
            assert closed.value.rcvd is not None and closed.value.rcvd.code == CLOSE_REPLACED
            # Le second fonctionne normalement.
            await second.send(CC)
            await asyncio.sleep(0.1)
            await second.close()
        assert len(out.sent) == 1

    asyncio.run(scenario())


def test_routage_recu_de_l_app():
    async def scenario():
        out, logs = DryRunOutput(), []
        routed = []
        router = Router(routed.append)
        bridge = Bridge(out, log=logs.append, router=router)
        server = await bridge.start("127.0.0.1", 0)
        port = next(iter(server.sockets)).getsockname()[1]
        async with server:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "routing", "targets": [{"channel": 2, "octave": -1}]}))
                await asyncio.sleep(0.1)
        # Le clavier joue Do3 : le Pad (canal 2) reçoit Do2.
        router.handle(mido.Message("note_on", channel=0, note=60, velocity=90))
        assert [(m.channel, m.note) for m in routed] == [(1, 48)]
        assert any("clavier → canal 2 (-1 oct.)" in line for line in logs)

    asyncio.run(scenario())


def test_touche_du_clavier_renvoyee_a_l_app():
    async def scenario():
        router = Router(lambda m: None)
        bridge = Bridge(DryRunOutput(), log=lambda _: None, router=router)
        server = await bridge.start("127.0.0.1", 0)
        port = next(iter(server.sockets)).getsockname()[1]
        async with server:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await asyncio.sleep(0.05)
                # La touche arrive sur un autre thread, comme depuis l'entrée MIDI.
                await asyncio.to_thread(router.handle, mido.Message("note_on", note=36, velocity=80))
                assert json.loads(await asyncio.wait_for(ws.recv(), 2)) == {"type": "key", "note": 36}

    asyncio.run(scenario())
