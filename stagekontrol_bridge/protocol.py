"""Protocole JSON de stageKontrol → messages MIDI (fonctions pures, testées).

Messages reçus de l'app (canaux en 1–16 dans le JSON ; seul le bridge convertit en 0–15) :
    {"type": "midi", "message": "cc", "channel": 1, "number": 74, "value": 96}
    {"type": "midi", "message": "note", "channel": 16, "note": 38, "velocity": 127, "state": "on"}
    {"type": "midi", "message": "program", "channel": 1, "program": 2}
    {"type": "midi", "message": "pitchbend", "channel": 1, "value": -4096}  (−8192…8191, 0 = centre)
    {"type": "ping"}  → le bridge répond {"type": "pong"}
    {"type": "routing", "targets": [{"channel": 1, "octave": 0, "low": 60, "high": 127}, …]}
        → routage du clavier maître (voir router.py) ; low/high (zone) facultatifs, 0–127 par défaut
Messages envoyés à l'app :
    {"type": "key", "note": 60, "velocity": 112, "sent": 90}  → touche enfoncée sur le clavier maître
        (apprentissage des zones, calibration : vélocité jouée et envoyée après correction)
Reçu aussi :
    {"type": "velocity", "keys": {"62": -20}}  → correction de vélocité par touche (−50…50 %)
"""

from __future__ import annotations

import json
from typing import Any

import mido

from .router import Target

PONG = json.dumps({"type": "pong"})

# Code de fermeture WebSocket : la connexion est remplacée par celle d'un autre appareil.
# L'app qui le reçoit ne se reconnecte pas toute seule (évite que deux appareils se chassent).
CLOSE_REPLACED = 4001


class ProtocolError(ValueError):
    """Message JSON invalide : il est ignoré et signalé, sans arrêter le bridge."""


def parse(raw: str | bytes) -> dict[str, Any]:
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ProtocolError(f"JSON illisible : {raw!r}") from e
    if not isinstance(msg, dict):
        raise ProtocolError(f"objet JSON attendu : {raw!r}")
    return msg


def is_ping(msg: dict[str, Any]) -> bool:
    return msg.get("type") == "ping"


def _int(msg: dict[str, Any], key: str, low: int, high: int) -> int:
    v = msg.get(key)
    # bool est un sous-type d'int en Python : on le refuse explicitement.
    if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= high:
        raise ProtocolError(f"« {key} » doit être un entier de {low} à {high} (reçu : {v!r})")
    return v


def to_midi(msg: dict[str, Any]) -> mido.Message:
    """Convertit un message `{"type": "midi", …}` en message mido (canal 0–15)."""
    if msg.get("type") != "midi":
        raise ProtocolError(f"type inconnu : {msg.get('type')!r}")
    channel = _int(msg, "channel", 1, 16) - 1
    kind = msg.get("message")

    if kind == "cc":
        return mido.Message(
            "control_change",
            channel=channel,
            control=_int(msg, "number", 0, 127),
            value=_int(msg, "value", 0, 127),
        )
    if kind == "note":
        state = msg.get("state")
        if state not in ("on", "off"):
            raise ProtocolError(f"« state » doit valoir \"on\" ou \"off\" (reçu : {state!r})")
        # Note Off explicite (0x80), avec la vélocité reçue.
        return mido.Message(
            "note_on" if state == "on" else "note_off",
            channel=channel,
            note=_int(msg, "note", 0, 127),
            velocity=_int(msg, "velocity", 0, 127),
        )
    if kind == "program":
        return mido.Message("program_change", channel=channel, program=_int(msg, "program", 0, 127))
    if kind == "pitchbend":
        # -8192 (bas) … 0 (centre) … 8191 (haut), comme mido.
        return mido.Message("pitchwheel", channel=channel, pitch=_int(msg, "value", -8192, 8191))

    raise ProtocolError(f"message MIDI inconnu : {kind!r}")


def describe(m: mido.Message) -> str:
    """Texte lisible pour le terminal, avec le canal en 1–16 comme dans l'app."""
    ch = f"canal {m.channel + 1}"
    if m.type == "control_change":
        return f"CC {m.control} = {m.value}  ({ch})"
    if m.type == "note_on":
        return f"Note On {m.note} vél. {m.velocity}  ({ch})"
    if m.type == "note_off":
        return f"Note Off {m.note} vél. {m.velocity}  ({ch})"
    if m.type == "program_change":
        return f"Program Change {m.program}  ({ch})"
    if m.type == "pitchwheel":
        return f"Pitch Bend {m.pitch:+d}  ({ch})"
    return str(m)


def is_routing(msg: dict[str, Any]) -> bool:
    return msg.get("type") == "routing"


def to_targets(msg: dict[str, Any]) -> list[Target]:
    """Cibles du routage du clavier : canal 1–16, octave −3…+3."""
    raw = msg.get("targets")
    if not isinstance(raw, list) or len(raw) > 16:
        raise ProtocolError("« targets » doit être une liste d'au plus 16 cibles")
    targets = []
    for t in raw:
        if not isinstance(t, dict):
            raise ProtocolError(f"cible invalide : {t!r}")
        low = _int(t, "low", 0, 127) if "low" in t else 0
        high = _int(t, "high", 0, 127) if "high" in t else 127
        if low > high:
            raise ProtocolError(f"zone inversée : {low} > {high}")
        targets.append(
            Target(channel=_int(t, "channel", 1, 16), octave=_int(t, "octave", -3, 3), low=low, high=high)
        )
    return targets


def key_message(note: int, velocity: int | None = None, sent: int | None = None) -> str:
    msg: dict[str, Any] = {"type": "key", "note": note}
    if velocity is not None:
        msg["velocity"] = velocity
        msg["sent"] = sent if sent is not None else velocity
    return json.dumps(msg)


def is_velocity(msg: dict[str, Any]) -> bool:
    return msg.get("type") == "velocity"


def to_key_velocity(msg: dict[str, Any]) -> dict[int, int]:
    """Corrections de vélocité par touche : {"keys": {"62": -20, …}} (note 0–127 → −50…50 %)."""
    raw = msg.get("keys")
    if not isinstance(raw, dict) or len(raw) > 128:
        raise ProtocolError("« keys » doit associer au plus 128 touches à un pourcentage")
    out: dict[int, int] = {}
    for key, value in raw.items():
        try:
            note = int(key)
        except (TypeError, ValueError) as e:
            raise ProtocolError(f"touche invalide : {key!r}") from e
        if not 0 <= note <= 127 or not isinstance(value, int) or isinstance(value, bool) or not -50 <= value <= 50:
            raise ProtocolError(f"correction invalide : {key!r} → {value!r}")
        out[note] = value
    return out


def describe_targets(targets: list[Target]) -> str:
    if not targets:
        return "aucune piste"
    def one(t: Target) -> str:
        parts = []
        if t.octave:
            parts.append(f"{t.octave:+d} oct.")
        if t.low > 0 or t.high < 127:
            parts.append(f"notes {t.low}–{t.high}")
        return f"canal {t.channel}" + (f" ({', '.join(parts)})" if parts else "")

    return ", ".join(one(t) for t in targets)
