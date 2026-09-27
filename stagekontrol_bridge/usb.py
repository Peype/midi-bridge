"""Protocole du câble USB (téléphone en mode « USB MIDI »), identique à src/domain/usb-protocol.ts.

- Les messages MIDI de l'app (CC, notes, Program Change) arrivent tels quels.
- Les messages de contrôle (routage, ping/pong, touches jouées…) passent dans un SysEx réservé :
  F0 7D 'S' 'K' <JSON en ASCII 7 bits> F7. 7D = identifiant « non commercial » de la norme MIDI.
"""

from __future__ import annotations

import json
from typing import Any

import mido

# Octets après F0 (mido ne garde pas F0/F7 dans `data`).
SYSEX_ID = (0x7D, 0x53, 0x4B)


def encode_control(value: Any) -> mido.Message:
    """SysEx de contrôle contenant `value` en JSON (ASCII : les accents sont échappés en \\uXXXX)."""
    text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return mido.Message("sysex", data=SYSEX_ID + tuple(ord(c) & 0x7F for c in text))


def decode_control(msg: mido.Message) -> Any | None:
    """Contenu JSON d'un SysEx de contrôle, ou None si ce n'en est pas un (ou s'il est illisible)."""
    if msg.type != "sysex":
        return None
    data = tuple(msg.data)
    if data[: len(SYSEX_ID)] != SYSEX_ID:
        return None
    try:
        return json.loads(bytes(data[len(SYSEX_ID) :]).decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return None
