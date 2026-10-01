"""Bridge stageKontrol : WebSocket (app) → MIDI (Kontakt, DAW…).

Lancement depuis la racine du projet :
    bridge/.venv/bin/python bridge/run_bridge.py [--port 8765] [--midi-port stageKontrol] [--dry-run]
        [--keyboard "nom du clavier"] [--no-keyboard] [--list-midi]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import webbrowser
import platform
import socket
import sys

import mido

from .midi_out import (
    DEFAULT_PORT_NAME,
    DryRunOutput,
    LockedOutput,
    MidiOutput,
    WaitingOutput,
    keyboard_candidates,
)
from .discovery import announce, withdraw
from .inputs import InputHub
from .router import Router
from .server import Bridge
from .status import StatusPage, build_snapshot

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


async def run(
    output: MidiOutput,
    midi: WaitingOutput | DryRunOutput,
    midi_port: str,
    port: int,
    verbose: bool,
    router: Router | None,
    keyboard: str | None,
    open_browser: bool,
) -> None:
    bridge = Bridge(output, verbose=verbose, router=router)

    # Entrées MIDI : claviers maîtres (vers le routage) et téléphones branchés en USB.
    hub = InputHub(
        own_port=midi_port,
        keyboard=keyboard,
        on_keyboard=router.handle if router is not None else None,
        on_phone_midi=lambda _phone, msg: bridge.forward(msg),
        on_phone_control=lambda phone, msg: bridge.control(msg, lambda text: phone.send_control(json.loads(text))),
        log=bridge.log,
    )
    bridge.hub = hub
    hub.refresh()

    ips = local_ips()
    name = socket.gethostname().split(".")[0] or "StageKontrol"
    page = StatusPage(
        lambda: build_snapshot(
            name=name,
            port=port,
            ips=ips,
            midi_name=midi.name,
            midi_ok=getattr(midi, "available", True),
            midi_error=getattr(midi, "error", None),
            wifi_peer=bridge.wifi_peer(),
            usb_phones=[p.name for p in bridge.usb_phones()],
            keyboards=hub.keyboards(),
            keyboard_routing=router is not None,
        )
    )
    server = await bridge.start("0.0.0.0", port, process_request=page.process_request)
    announced = await announce(port, ips) if ips else None
    status_url = f"http://localhost:{port}/"

    print(f"Bridge StageKontrol à l'écoute sur le port {port}.")
    print(f"Page d'état (QR code à scanner avec le téléphone) : {status_url}")
    if getattr(midi, "available", True):
        print(f"Sortie MIDI : {midi.name}")
    else:
        print(f"Sortie MIDI : en attente du port « {midi.name} ».")
        print(f"  {getattr(midi, 'error', '')}")
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
    print("Ctrl+C (ou fermer cette fenêtre) pour arrêter.\n", flush=True)
    if open_browser:
        webbrowser.open(status_url)

    async def watch() -> None:
        while True:
            await asyncio.sleep(INPUT_REFRESH_S)
            hub.refresh()
            # Port MIDI créé après le lancement (loopMIDI) : on le prend aussitôt.
            if isinstance(midi, WaitingOutput) and midi.retry():
                bridge.log(f"+ port MIDI « {midi.name} » trouvé")

    watcher = asyncio.create_task(watch())
    try:
        async with server:
            await server.serve_forever()
    finally:
        watcher.cancel()
        hub.close()
        await withdraw(announced)


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
    parser.add_argument("--no-browser", action="store_true", help="n'ouvre pas la page d'état au lancement")
    args = parser.parse_args(argv)

    if args.list_midi:
        print("Entrées MIDI (claviers possibles) :")
        for n in keyboard_candidates(mido.get_input_names(), args.midi_port) or ["(aucune)"]:
            print(f"  - {n}")
        print("Sorties MIDI :")
        for n in mido.get_output_names() or ["(aucune)"]:
            print(f"  - {n}")
        return 0

    # Le port MIDI peut manquer au lancement (loopMIDI pas encore configuré) : le bridge démarre quand
    # même, la page d'état explique quoi faire et le port est pris dès qu'il existe.
    midi: WaitingOutput | DryRunOutput = DryRunOutput() if args.dry_run else WaitingOutput(args.midi_port)
    output: MidiOutput = LockedOutput(midi)

    # Routage du clavier maître : actif sauf --no-keyboard ; le clavier est détecté (ou branché) en
    # cours de route. --keyboard limite le routage à une entrée précise.
    router: Router | None = None if args.no_keyboard else Router(output.send)

    try:
        asyncio.run(
            run(output, midi, args.midi_port, args.port, not args.quiet, router, args.keyboard, not args.no_browser)
        )
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
