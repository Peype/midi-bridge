"""Bridge stageKontrol : WebSocket (app) → MIDI (Kontakt, DAW…).

Lancement depuis la racine du projet :
    bridge/.venv/bin/python bridge/run_bridge.py [--port 8765] [--midi-port stageKontrol] [--dry-run]
        [--keyboard "nom du clavier"] [--no-keyboard] [--list-midi]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import socket
import sys

import mido

from .midi_out import (
    DEFAULT_PORT_NAME,
    DryRunOutput,
    LockedOutput,
    MidiOutput,
    MidiPortError,
    keyboard_candidates,
    open_output,
)
from .discovery import announce, withdraw
from .inputs import InputHub
from .router import Router
from .server import Bridge

DEFAULT_WS_PORT = 8765


def local_ips() -> list[str]:
    """Adresses IPv4 locales du PC, à saisir dans les réglages de l'app."""
    ips: set[str] = set()
    try:
        # Adresse utilisée pour sortir sur le réseau (aucun paquet n'est envoyé).
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


INPUT_REFRESH_S = 2.0


async def run(output: MidiOutput, port: int, verbose: bool, router: Router | None, keyboard: str | None) -> None:
    bridge = Bridge(output, verbose=verbose, router=router)
    server = await bridge.start("0.0.0.0", port)

    # Entrées MIDI : claviers maîtres (vers le routage) et téléphones branchés en USB.
    hub = InputHub(
        own_port=bridge_port_name(output),
        keyboard=keyboard,
        on_keyboard=router.handle if router is not None else None,
        on_phone_midi=lambda _phone, msg: bridge.forward(msg),
        on_phone_control=lambda phone, msg: bridge.control(msg, lambda text: phone.send_control(json.loads(text))),
        log=bridge.log,
    )
    bridge.hub = hub
    hub.refresh()

    ips = local_ips()
    announced = await announce(port, ips) if ips else None

    print(f"Bridge StageKontrol à l'écoute sur le port {port}.")
    print(f"Sortie MIDI : {output.name}")
    if router is None:
        print("Clavier maître : désactivé (--no-keyboard)")
    else:
        print(f"Clavier maître : {keyboard or 'toutes les entrées MIDI (hors téléphone)'}, routé vers les pistes actives")
        print("  → Dans le logiciel, désactivez le clavier comme entrée MIDI directe (sinon notes en double).")
    print("Câble USB : branchez le téléphone en mode « MIDI » ; il est reconnu automatiquement.")
    if ips:
        print("Adresse(s) IP (Wi-Fi) : " + ", ".join(ips))
        print("  → L'app trouve le bridge toute seule (Réglages)" + ("" if announced else " — sauf : zeroconf absent, saisissez l'IP"))
    else:
        print("Adresse IP locale introuvable : vérifiez que le PC est sur le réseau Wi-Fi.")
    if platform.system() == "Windows":
        print("Si Windows demande l'autorisation du pare-feu, acceptez pour les réseaux privés.")
    print("Ctrl+C pour arrêter.\n", flush=True)

    async def watch_inputs() -> None:
        while True:
            await asyncio.sleep(INPUT_REFRESH_S)
            hub.refresh()

    watcher = asyncio.create_task(watch_inputs())
    try:
        async with server:
            await server.serve_forever()
    finally:
        watcher.cancel()
        hub.close()
        await withdraw(announced)


def bridge_port_name(output: MidiOutput) -> str:
    """Nom du port du bridge, à ne pas écouter (sinon il recevrait sa propre sortie)."""
    return output.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stagekontrol-bridge", description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_WS_PORT, help="port WebSocket (8765)")
    parser.add_argument(
        "--midi-port", default=DEFAULT_PORT_NAME, help=f"nom du port MIDI ({DEFAULT_PORT_NAME})"
    )
    parser.add_argument("--dry-run", action="store_true", help="sans MIDI : affiche seulement les messages")
    parser.add_argument("--quiet", action="store_true", help="n'affiche pas chaque message MIDI")
    parser.add_argument("--keyboard", help="nom (ou début du nom) de l'entrée MIDI du clavier maître")
    parser.add_argument("--no-keyboard", action="store_true", help="n'ouvre aucun clavier (pas de routage)")
    parser.add_argument("--list-midi", action="store_true", help="affiche les ports MIDI et quitte")
    args = parser.parse_args(argv)

    if args.list_midi:
        print("Entrées MIDI (claviers possibles) :")
        for n in keyboard_candidates(mido.get_input_names(), args.midi_port) or ["(aucune)"]:
            print(f"  - {n}")
        print("Sorties MIDI :")
        for n in mido.get_output_names() or ["(aucune)"]:
            print(f"  - {n}")
        return 0

    try:
        output: MidiOutput = LockedOutput(DryRunOutput() if args.dry_run else open_output(args.midi_port))
    except MidiPortError as e:
        print(e, file=sys.stderr)
        return 1

    # Routage du clavier maître : actif sauf --no-keyboard ; le clavier est détecté (ou branché) en
    # cours de route. --keyboard limite le routage à une entrée précise.
    router: Router | None = None if args.no_keyboard else Router(output.send)

    try:
        asyncio.run(run(output, args.port, not args.quiet, router, args.keyboard))
    except KeyboardInterrupt:
        print("\nArrêt du bridge.")
    except OSError as e:
        print(f"Impossible d'écouter sur le port {args.port} : {e}", file=sys.stderr)
        return 1
    finally:
        if router is not None:
            router.all_notes_off()
        output.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
