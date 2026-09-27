"""Ports MIDI du bridge, selon le système : sortie vers le logiciel, entrée du clavier maître.

- macOS (et Linux) : le bridge crée son propre port virtuel, visible par les logiciels musicaux.
- Windows : les ports virtuels n'existent pas nativement ; on ouvre par son nom un port
  loopMIDI créé au préalable, avec un message clair s'il est absent.
"""

from __future__ import annotations

import platform
import threading
from typing import Callable, Protocol

import mido

DEFAULT_PORT_NAME = "stageKontrol"


class MidiOutput(Protocol):
    name: str

    def send(self, message: mido.Message) -> None: ...

    def close(self) -> None: ...


class MidiPortError(RuntimeError):
    """Le port MIDI n'a pas pu être ouvert (message destiné à l'utilisateur)."""


def _find_windows_port(name: str, available: list[str]) -> str | None:
    """Windows ajoute parfois un numéro au nom du port (« stageKontrol 1 »)."""
    for candidate in available:
        if candidate == name or candidate.startswith(name + " "):
            return candidate
    return None


def open_output(name: str = DEFAULT_PORT_NAME, system: str | None = None) -> MidiOutput:
    system = system or platform.system()
    if system == "Windows":
        available = mido.get_output_names()
        found = _find_windows_port(name, available)
        if not found:
            listed = "\n".join(f"  - {n}" for n in available) or "  (aucun)"
            raise MidiPortError(
                f"Port MIDI « {name} » introuvable.\n"
                f"Ouvrez loopMIDI, créez un port nommé exactement « {name} », puis relancez le bridge.\n"
                f"Ports MIDI disponibles :\n{listed}"
            )
        return mido.open_output(found)
    try:
        return mido.open_output(name, virtual=True)
    except Exception as e:  # rtmidi lève des erreurs propres à chaque système
        raise MidiPortError(f"Impossible de créer le port MIDI virtuel « {name} » : {e}") from e


class DryRunOutput:
    """Sortie de test (--dry-run) : n'envoie rien, garde les messages pour affichage ou tests."""

    name = "(test, sans MIDI)"

    def __init__(self) -> None:
        self.sent: list[mido.Message] = []

    def send(self, message: mido.Message) -> None:
        self.sent.append(message)

    def close(self) -> None:
        pass


class LockedOutput:
    """Sortie partagée par l'app (WebSocket) et le clavier (thread MIDI) : un envoi à la fois."""

    def __init__(self, output: MidiOutput) -> None:
        self._output = output
        self._lock = threading.Lock()
        self.name = output.name

    def send(self, message: mido.Message) -> None:
        with self._lock:
            self._output.send(message)

    def close(self) -> None:
        self._output.close()


def keyboard_candidates(inputs: list[str], own_port: str) -> list[str]:
    """Entrées MIDI utilisables comme clavier : toutes sauf le port du bridge (évite une boucle)."""
    return [n for n in inputs if not n.startswith(own_port)]


def open_keyboard(
    name: str | None, own_port: str, callback: Callable[[mido.Message], None]
) -> mido.ports.BaseInput | None:
    """Ouvre le clavier maître : par son nom, ou automatiquement s'il n'y en a qu'un.

    Renvoie None s'il n'y a aucun clavier (le bridge fonctionne alors sans routage).
    """
    inputs = mido.get_input_names()
    candidates = keyboard_candidates(inputs, own_port)
    if name:
        found = [n for n in candidates if n == name or n.startswith(name)]
        if not found:
            listed = "\n".join(f"  - {n}" for n in candidates) or "  (aucune)"
            raise MidiPortError(f"Clavier « {name} » introuvable. Entrées MIDI disponibles :\n{listed}")
        return mido.open_input(found[0], callback=callback)
    if not candidates:
        return None
    if len(candidates) > 1:
        listed = "\n".join(f"  - {n}" for n in candidates)
        raise MidiPortError(
            "Plusieurs entrées MIDI : choisissez le clavier avec --keyboard \"nom\".\n" + listed
        )
    return mido.open_input(candidates[0], callback=callback)
