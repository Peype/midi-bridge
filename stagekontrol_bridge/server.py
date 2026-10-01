"""Serveur WebSocket du bridge : reçoit le JSON de l'app et l'envoie sur la sortie MIDI.

Un seul appareil à la fois : une nouvelle connexion remplace la précédente, fermée avec le
code CLOSE_REPLACED (l'app remplacée ne se reconnecte pas d'elle-même).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable

from websockets.asyncio.server import Server, ServerConnection, serve

from .midi_out import MidiOutput
from .protocol import (
    CLOSE_REPLACED,
    PONG,
    ProtocolError,
    describe,
    describe_targets,
    is_ping,
    is_routing,
    key_message,
    parse,
    to_midi,
    to_targets,
)
from .router import Router

Log = Callable[[str], None]


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def default_log(text: str) -> None:
    print(f"{stamp()}  {text}", flush=True)


class Bridge:
    def __init__(
        self,
        output: MidiOutput,
        log: Log = default_log,
        verbose: bool = True,
        router: Router | None = None,
    ) -> None:
        self.output = output
        self.log = log
        self.verbose = verbose
        # Routage du clavier maître (None : pas de clavier, le routage reçu est ignoré).
        self.router = router
        self.current: ServerConnection | None = None
        # Entrées MIDI (claviers, téléphones en USB), branchées par __main__.
        self.hub = None

    async def handle(self, ws: ServerConnection) -> None:
        peer = f"{ws.remote_address[0]}:{ws.remote_address[1]}" if ws.remote_address else "?"
        previous, self.current = self.current, ws
        if previous is not None:
            self.log(f"~ nouvel appareil ({peer}) : l'appareil précédent est déconnecté")
            await previous.close(code=CLOSE_REPLACED, reason="remplacé par un autre appareil")
        self.log(f"+ appareil connecté ({peer})")
        try:
            async for raw in ws:
                self.receive(ws, raw)
        finally:
            if self.current is ws:
                self.current = None
            self.log(f"- appareil déconnecté ({peer})")

    def receive(self, ws: ServerConnection, raw: str | bytes) -> None:
        """Message reçu en Wi-Fi (WebSocket)."""
        try:
            msg = parse(raw)
        except ProtocolError as e:
            self.log(f"? message ignoré : {e}")
            return
        self.control(msg, lambda text: asyncio.ensure_future(ws.send(text)))

    def control(self, msg: dict, reply: Callable[[str], object]) -> None:
        """Message JSON de l'app, en Wi-Fi comme en USB. `reply` renvoie un texte JSON à l'app."""
        try:
            if is_ping(msg):
                # Réponse immédiate : l'app coupe la connexion sans pong pendant 5 s.
                reply(PONG)
                return
            if is_routing(msg):
                targets = to_targets(msg)
                if self.router is None:
                    self.log(f"= routage reçu ({describe_targets(targets)}) : pas de clavier branché")
                else:
                    # Le routage est conservé si l'app se déconnecte : le clavier continue de jouer.
                    self.router.set_targets(targets)
                    self.log(f"= clavier → {describe_targets(targets)}")
                return
            midi = to_midi(msg)
        except ProtocolError as e:
            self.log(f"? message ignoré : {e}")
            return
        self.forward(midi)

    def forward(self, midi) -> None:
        """MIDI de l'app vers la sortie « stageKontrol »."""
        self.output.send(midi)
        if self.verbose:
            self.log(f"→ {describe(midi)}")

    def _send_key(self, note: int) -> None:
        ws = self.current
        if ws is not None:
            asyncio.ensure_future(ws.send(key_message(note)))
        # Téléphones branchés en USB : même message, en SysEx.
        for phone in list(self.usb_phones()):
            phone.send_control({"type": "key", "note": note})

    def wifi_peer(self) -> str | None:
        """Adresse du téléphone connecté en Wi-Fi, ou None."""
        ws = self.current
        if ws is None or not ws.remote_address:
            return None
        return str(ws.remote_address[0])

    def usb_phones(self):
        """Téléphones branchés en USB (fournis par l'InputHub, s'il y en a un)."""
        return self.hub.phones.values() if self.hub is not None else ()

    async def start(self, host: str, port: int, process_request=None) -> Server:
        if self.router is not None:
            # Les touches arrivent sur le thread MIDI : on repasse sur la boucle asyncio pour l'envoi.
            loop = asyncio.get_running_loop()
            self.router.on_key = lambda note: loop.call_soon_threadsafe(self._send_key, note)
        self.loop = asyncio.get_running_loop()
        # process_request : page d'état HTTP servie sur le même port (voir status.py).
        return await serve(self.handle, host, port, process_request=process_request)
