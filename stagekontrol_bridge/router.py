"""Routage du clavier maître : chaque note jouée part sur le canal de chaque piste qui sonne et dont
la zone contient la touche, transposée de l'octave de la piste (le routage est envoyé par l'app).
Le clavier peut n'envoyer que sur un seul canal (ex. M-Audio Keystation) : c'est le bridge qui
redistribue.

Règles :
- Une note est relâchée exactement là où elle a été jouée (mêmes canaux, mêmes hauteurs), même si
  l'octave ou le routage a changé entre-temps : aucune note bloquée.
- La zone (split) s'applique à la touche jouée, avant l'octave.
- Seuls les contrôles de jeu du clavier sont transmis aux canaux du routage : molette (CC 1),
  expression (CC 11), pédales (CC 64, 66, 67), pitch bend et aftertouch. Les autres CC (ex. le fader
  de volume du clavier, CC 7) sont ignorés : ils écraseraient les réglages de l'app.
- Un canal retiré du routage pendant que le sustain est enfoncé reçoit un sustain relâché.
- Une note transposée hors de 0–127 n'est pas jouée sur ce canal.
- Program Change, SysEx, horloge… du clavier sont ignorés (l'app gère les timbres).
- Correction de vélocité par touche (calibration du clavier, réglée dans l'app) : appliquée à la
  touche jouée, sur toutes les pistes, en pourcentage (−50 % … +50 %), résultat borné à 1–127.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

import mido

SUSTAIN = 64
# CC du clavier transmis aux instruments : molette, expression, sustain, sostenuto, douce.
PLAYING_CCS = frozenset({1, 11, 64, 66, 67})


MAX_KEY_CORRECTION = 50


def adjust_velocity(velocity: int, percent: int) -> int:
    """Vélocité corrigée d'un pourcentage, bornée à 1–127 (une note jouée ne devient jamais muette)."""
    if not percent:
        return velocity
    return max(1, min(127, round(velocity * (100 + percent) / 100)))


@dataclass(frozen=True)
class Target:
    channel: int  # 1–16, comme dans l'app
    octave: int  # −3…+3
    low: int = 0  # zone : touche la plus basse (0–127)
    high: int = 127  # zone : touche la plus haute (0–127)


class Router:
    def __init__(self, send: Callable[[mido.Message], None]) -> None:
        self._send = send
        # Appelé à chaque touche enfoncée (apprentissage des zones, calibration) :
        # note jouée, vélocité jouée, vélocité envoyée après correction.
        self.on_key: Callable[[int, int, int], None] | None = None
        # Correction de vélocité par touche (note jouée → pourcentage).
        self.key_velocity: dict[int, int] = {}
        self._lock = threading.Lock()
        self.targets: list[Target] = []
        # (canal d'entrée, note jouée) → [(canal de sortie 0–15, note envoyée)]
        self._held: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._sustain = False

    def _channels(self) -> list[int]:
        """Canaux de sortie (0–15) du routage courant, sans doublon."""
        return sorted({t.channel - 1 for t in self.targets})

    def set_targets(self, targets: list[Target]) -> None:
        with self._lock:
            before = set(self._channels())
            self.targets = list(targets)
            removed = before - set(self._channels())
            if self._sustain:
                for ch in sorted(removed):
                    self._send(mido.Message("control_change", channel=ch, control=SUSTAIN, value=0))

    def set_key_velocity(self, corrections: dict[int, int]) -> None:
        with self._lock:
            self.key_velocity = {n: p for n, p in corrections.items() if p}

    def handle(self, msg: mido.Message) -> None:
        """Message reçu du clavier (appelé depuis le thread d'entrée MIDI)."""
        with self._lock:
            if msg.type == "note_on" and msg.velocity > 0:
                velocity = adjust_velocity(msg.velocity, self.key_velocity.get(msg.note, 0))
                self._note_on(msg.copy(velocity=velocity))
                if self.on_key:
                    self.on_key(msg.note, msg.velocity, velocity)
            elif msg.type in ("note_off", "note_on"):
                self._note_off(msg)
            elif msg.type == "control_change":
                if msg.control not in PLAYING_CCS:
                    return
                if msg.control == SUSTAIN:
                    self._sustain = msg.value >= 64
                for ch in self._channels():
                    self._send(msg.copy(channel=ch))
            elif msg.type in ("pitchwheel", "aftertouch"):
                for ch in self._channels():
                    self._send(msg.copy(channel=ch))
            # Autres messages (program_change, sysex, clock, polytouch…) : ignorés.

    def _note_on(self, msg: mido.Message) -> None:
        key = (msg.channel, msg.note)
        # Même touche rejouée sans relâchement : on relâche d'abord la précédente.
        for ch, note in self._held.pop(key, []):
            self._send(mido.Message("note_off", channel=ch, note=note, velocity=0))
        sent: list[tuple[int, int]] = []
        for t in self.targets:
            if not t.low <= msg.note <= t.high:
                continue
            note = msg.note + 12 * t.octave
            if 0 <= note <= 127 and (t.channel - 1, note) not in sent:
                self._send(mido.Message("note_on", channel=t.channel - 1, note=note, velocity=msg.velocity))
                sent.append((t.channel - 1, note))
        if sent:
            self._held[key] = sent

    def _note_off(self, msg: mido.Message) -> None:
        velocity = msg.velocity if msg.type == "note_off" else 0
        for ch, note in self._held.pop((msg.channel, msg.note), []):
            self._send(mido.Message("note_off", channel=ch, note=note, velocity=velocity))

    def all_notes_off(self) -> None:
        """Relâche toutes les notes tenues (arrêt du bridge)."""
        with self._lock:
            for notes in self._held.values():
                for ch, note in notes:
                    self._send(mido.Message("note_off", channel=ch, note=note, velocity=0))
            self._held.clear()
