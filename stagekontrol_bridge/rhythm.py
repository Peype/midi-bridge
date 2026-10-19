"""Boîte à rythme du bridge : joue en boucle des fichiers MIDI de batterie, pilotée par l'app.

Les rythmes sont des fichiers .mid rangés dans un dossier (par défaut ~/StageKontrol/Rythmes),
une partie par fichier, nommés « Style - Partie.mid » :
    Salegy - A.mid, Salegy - B.mid, Salegy - Break.mid, Salegy - Intro.mid, Salegy - Fin.mid
Un fichier sans « - » est la partie A de son style. Parties reconnues : A, B, C (en boucle), Intro,
Break (ou Fill, Roulement) et Fin (ou Ending, Outro), jouées une seule fois.

Le rythme tourne sur l'horloge du PC (le téléphone n'envoie que des ordres) : le Wi-Fi ne décale
jamais les coups. Le groove du fichier est rejoué tel quel ; seul le tempo vient de l'app (en noires
par minute). Les notes partent sur le canal choisi dans l'app, quel que soit celui du fichier.

Synchronisation du click du téléphone : le bridge répond aux demandes d'heure de l'app
({"type":"time","t":…} → {"type":"time","t":…,"server":ms}) pour qu'elle mesure le décalage entre
les deux horloges, et annonce l'instant de chaque début de mesure ({"type":"rhythm-clock",
"at":ms,"tempo":noires/min}), sur la même horloge. L'app y cale ses clics.

Règles de jeu :
- Changer de partie, Break et Fin prennent effet au début de la mesure suivante.
- Break joue sa partie une fois puis revient à la partie en cours ; Intro enchaîne sur A ; Fin
  arrête le rythme à sa fin.
- Le tempo et le volume changent tout de suite ; le rythme continue si l'app se déconnecte.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import mido

DEFAULT_FOLDER = Path.home() / "StageKontrol" / "Rythmes"
EXTENSIONS = (".mid", ".midi")

# Nom de partie dans le fichier (minuscules) → nom affiché.
PART_ALIASES = {
    "a": "A",
    "b": "B",
    "c": "C",
    "intro": "Intro",
    "break": "Break",
    "fill": "Break",
    "roulement": "Break",
    "fin": "Fin",
    "ending": "Fin",
    "end": "Fin",
    "outro": "Fin",
}
PART_ORDER = ["Intro", "A", "B", "C", "Break", "Fin"]
# Parties jouées une seule fois (les autres tournent en boucle).
ONCE_PARTS = frozenset({"Intro", "Break", "Fin"})

MIN_TEMPO, MAX_TEMPO = 20.0, 400.0
START_DELAY_S = 0.05
# Attente maximale entre deux vérifications (un ordre de l'app est pris en compte dans ce délai).
MAX_WAIT_S = 0.02


class RhythmError(ValueError):
    """Rythme introuvable ou fichier MIDI illisible (message pour l'utilisateur)."""


# --- Fichiers -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    beat: float  # position en noires depuis le début de la partie
    note: int
    velocity: int  # 0 = relâchement


@dataclass(frozen=True)
class Pattern:
    events: tuple[Event, ...]
    length: float  # longueur en noires (mesures entières)
    bar: float  # longueur d'une mesure en noires (4 en 4/4, 6 en 12/8)


def read_pattern(mid: mido.MidiFile) -> Pattern:
    """Notes d'un fichier MIDI, positions en noires ; longueur arrondie à la mesure entière."""
    tpb = mid.ticks_per_beat or 480
    tick = 0
    bar: float | None = None
    events: list[Event] = []
    for msg in mido.merge_tracks(mid.tracks):
        tick += msg.time
        if msg.type == "time_signature" and bar is None:
            bar = msg.numerator * 4 / msg.denominator
        elif msg.type == "note_on" and msg.velocity > 0:
            events.append(Event(tick / tpb, msg.note, msg.velocity))
        elif msg.type in ("note_on", "note_off"):
            events.append(Event(tick / tpb, msg.note, 0))
    bar = bar or 4.0
    end = tick / tpb
    length = max(1, math.ceil(end / bar - 1e-6)) * bar
    kept: list[Event] = []
    for e in events:
        if e.beat < length - 1e-9:
            kept.append(e)
        elif e.velocity == 0:
            # Relâchement après la fin de la boucle : juste avant la fin.
            kept.append(Event(length - 1e-6, e.note, 0))
    if not any(e.velocity for e in kept):
        raise RhythmError("aucune note dans le fichier")
    # À position égale, les relâchements passent avant les nouvelles notes.
    kept.sort(key=lambda e: (e.beat, e.velocity != 0))
    return Pattern(tuple(kept), length, bar)


def split_name(stem: str) -> tuple[str, str]:
    """« Salegy - Break » → (« Salegy », « Break ») ; « Salegy » → (« Salegy », « A »)."""
    if " - " not in stem:
        return stem.strip(), "A"
    style, raw = stem.rsplit(" - ", 1)
    raw = raw.strip()
    return style.strip(), PART_ALIASES.get(raw.lower(), raw)


def part_key(part: str) -> tuple[int, str]:
    return (PART_ORDER.index(part) if part in PART_ORDER else len(PART_ORDER), part)


class Library:
    """Rythmes du dossier : style → partie → fichier. Relu à chaque demande de l'app."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.styles: dict[str, dict[str, Path]] = {}
        self._cache: dict[Path, tuple[float, Pattern]] = {}

    def scan(self) -> dict[str, dict[str, Path]]:
        styles: dict[str, dict[str, Path]] = {}
        if self.folder.is_dir():
            for path in sorted(self.folder.iterdir()):
                if path.suffix.lower() in EXTENSIONS and path.is_file():
                    style, part = split_name(path.stem)
                    if style:
                        styles.setdefault(style, {})[part] = path
        self.styles = dict(sorted(styles.items(), key=lambda kv: kv[0].lower()))
        return self.styles

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": s, "parts": sorted(parts, key=part_key)} for s, parts in self.styles.items()]

    def load(self, style: str) -> dict[str, Pattern]:
        """Toutes les parties d'un style (lecture des fichiers, gardés en mémoire tant qu'inchangés)."""
        parts = self.styles.get(style) or self.scan().get(style)
        if not parts:
            raise RhythmError(f"rythme « {style} » introuvable dans {self.folder}")
        patterns: dict[str, Pattern] = {}
        for part, path in parts.items():
            try:
                mtime = path.stat().st_mtime
                cached = self._cache.get(path)
                if cached is None or cached[0] != mtime:
                    cached = (mtime, read_pattern(mido.MidiFile(path)))
                    self._cache[path] = cached
                patterns[part] = cached[1]
            except (OSError, ValueError, EOFError) as e:
                raise RhythmError(f"« {path.name} » illisible : {e}") from e
        return patterns


# --- Lecture ------------------------------------------------------------------------------------


def loop_part(parts: dict[str, Pattern], wanted: str | None = None) -> str | None:
    """Partie qui tourne en boucle : `wanted` si possible, sinon A, sinon la première non unique."""
    if wanted in parts and wanted not in ONCE_PARTS:
        return wanted
    if "A" in parts:
        return "A"
    loops = sorted((p for p in parts if p not in ONCE_PARTS), key=part_key)
    return loops[0] if loops else None


class RhythmPlayer:
    """Joue un style sur un fil d'exécution dédié. Toutes les méthodes sont sûres entre fils."""

    def __init__(
        self,
        send: Callable[[mido.Message], None],
        on_state: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        on_clock: Callable[[dict[str, Any]], None] | None = None,
        threaded: bool = True,
    ) -> None:
        self._send = send
        # Sans fil d'exécution (tests) : c'est l'appelant qui fait avancer le jeu avec step().
        self._threaded = threaded
        self.on_state = on_state
        self.on_clock = on_clock
        self._clock = clock
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self.style: str | None = None
        self.parts: dict[str, Pattern] = {}
        self.playing = False
        self.part: str | None = None  # partie en cours
        self.main: str | None = None  # partie en boucle à reprendre après Intro / Break
        self.tempo = 100.0
        self.channel = 9  # 0–15 (canal 10 = batterie General MIDI)
        self.volume = 100
        # Horloge : la noire `_b0` tombe à l'instant `_t0`.
        self._t0 = 0.0
        self._b0 = 0.0
        self._start = 0.0  # noire où la partie en cours a commencé (sa position 0)
        self._index = 0  # prochain événement de la partie en cours
        self._switch_at: float | None = None  # noire du prochain changement de partie
        self._next: str | None = None  # partie qui commencera à `_switch_at` (None = arrêt)
        self._held: set[int] = set()

    # Horloge ------------------------------------------------------------------------------------

    def _beat_at(self, t: float) -> float:
        return self._b0 + (t - self._t0) * self.tempo / 60.0

    def _time_of(self, beat: float) -> float:
        return self._t0 + (beat - self._b0) * 60.0 / self.tempo

    def _next_bar(self, now_beat: float) -> float:
        """Début de la prochaine mesure de la partie en cours (au plus tôt maintenant)."""
        pattern = self.parts[self.part] if self.part else None
        bar = pattern.bar if pattern else 4.0
        done = max(0.0, now_beat - self._start)
        return self._start + math.ceil(done / bar - 1e-9) * bar

    # Ordres de l'app ----------------------------------------------------------------------------

    def play(
        self,
        style: str,
        parts: dict[str, Pattern],
        part: str | None = None,
        tempo: float | None = None,
        channel: int | None = None,
        volume: int | None = None,
        delay: float = 0.0,
    ) -> None:
        """
        Démarre `style` (ou change de partie à la mesure suivante s'il joue déjà). `delay` (s) retarde
        le départ, par exemple pour un décompte du click.
        """
        with self._cond:
            self._settings(tempo, channel, volume)
            if self.playing and style == self.style:
                self.parts = parts
                if part and part in parts:
                    self._queue(part)
            else:
                self._release()
                self.style, self.parts = style, parts
                first = part if part in parts else loop_part(parts)
                if first is None:
                    self.playing = False
                    self._notify()
                    return
                self.main = loop_part(parts, None if first in ONCE_PARTS else first)
                self.playing = True
                self._t0, self._b0 = self._clock() + START_DELAY_S + max(0.0, min(10.0, delay)), 0.0
                self._begin(first, 0.0)
                self._ensure_thread()
            self._notify()
            self._cond.notify()

    def trigger(self, part: str) -> None:
        """Partie demandée en cours de jeu (A, B, Break, Fin…) : à la mesure suivante."""
        with self._cond:
            if self.playing and part in self.parts:
                self._queue(part)
                self._notify()
                self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._release()
            self.playing = False
            self._switch_at = self._next = None
            self._notify()
            self._cond.notify()

    def set(self, tempo: float | None = None, channel: int | None = None, volume: int | None = None) -> None:
        with self._cond:
            self._settings(tempo, channel, volume)
            self._cond.notify()

    def state(self) -> dict[str, Any]:
        with self._cond:
            return self._state()

    def now_ms(self) -> float:
        return self._clock() * 1000.0

    def announce(self) -> None:
        """Réannonce le début de la mesure en cours (click lancé pendant que le rythme joue)."""
        with self._cond:
            if self.playing:
                self._announce(self._bar_start())

    # Interne (appelé sous verrou) ---------------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        return {
            "type": "rhythm-state",
            "playing": self.playing,
            "style": self.style,
            "part": self.part if self.playing else None,
            "next": self._next if self.playing and self._switch_at is not None else None,
        }

    def _notify(self) -> None:
        if self.on_state:
            self.on_state(self._state())

    def _announce(self, beat: float) -> None:
        """Début de mesure à la noire `beat` : son instant sur l'horloge du bridge, pour l'app."""
        if self.on_clock and self.playing:
            self.on_clock({"type": "rhythm-clock", "at": self._time_of(beat) * 1000.0, "tempo": self.tempo})

    def _bar_start(self) -> float:
        """Début de la mesure en cours de la partie en cours (en noires)."""
        pattern = self.parts.get(self.part) if self.part else None
        bar = pattern.bar if pattern else 4.0
        now = max(self._beat_at(self._clock()), self._start)
        return self._start + math.floor((now - self._start) / bar + 1e-9) * bar

    def _settings(self, tempo: float | None, channel: int | None, volume: int | None) -> None:
        if tempo is not None:
            tempo = max(MIN_TEMPO, min(MAX_TEMPO, float(tempo)))
            if tempo != self.tempo:
                now = self._clock()
                # Le tempo change maintenant, sans saut de position.
                if self.playing and now > self._t0:
                    self._b0, self._t0 = self._beat_at(now), now
                self.tempo = tempo
                if self.playing:
                    self._announce(self._bar_start())
        if channel is not None and channel != self.channel:
            self._release()
            self.channel = channel
        if volume is not None:
            self.volume = max(0, min(100, int(volume)))

    def _queue(self, part: str) -> None:
        if part in ONCE_PARTS:
            if part != "Fin" and self.part not in ONCE_PARTS:
                self.main = self.part
        else:
            self.main = part
        now = self._beat_at(self._clock())
        self._switch_at = self._next_bar(max(now, self._b0))
        self._next = part

    def _begin(self, part: str, at: float) -> None:
        self.part, self._start, self._index = part, at, 0
        self._switch_at = self._next = None
        self._announce(at)

    def _release(self) -> None:
        for note in sorted(self._held):
            self._send(mido.Message("note_off", channel=self.channel, note=note, velocity=0))
        self._held.clear()

    def _fire(self, e: Event) -> None:
        if e.velocity == 0:
            if e.note in self._held:
                self._held.discard(e.note)
                self._send(mido.Message("note_off", channel=self.channel, note=e.note, velocity=0))
            return
        velocity = max(1, round(e.velocity * self.volume / 100)) if self.volume > 0 else 0
        if velocity == 0:
            return
        if e.note in self._held:
            self._send(mido.Message("note_off", channel=self.channel, note=e.note, velocity=0))
        self._held.add(e.note)
        self._send(mido.Message("note_on", channel=self.channel, note=e.note, velocity=velocity))

    def _after_once(self) -> str | None:
        """Partie qui suit une partie jouée une seule fois (None = arrêt)."""
        if self.part == "Fin":
            return None
        return loop_part(self.parts, self.main)

    def step(self) -> float | None:
        """Joue ce qui est dû ; renvoie l'instant de la prochaine échéance (None = rien à jouer)."""
        while self.playing:
            pattern = self.parts[self.part]
            end = self._start + pattern.length
            boundary = end if self._switch_at is None else min(self._switch_at, end)
            if self._index < len(pattern.events):
                at = self._start + pattern.events[self._index].beat
                if at < boundary:
                    due = self._time_of(at)
                    if due > self._clock():
                        return due
                    self._fire(pattern.events[self._index])
                    self._index += 1
                    continue
            due = self._time_of(boundary)
            if due > self._clock():
                return due
            # Limite atteinte : changement de partie, ou fin de la boucle.
            if self._switch_at is not None and self._switch_at <= boundary + 1e-9:
                part = self._next
            elif self.part in ONCE_PARTS:
                part = self._after_once()
            else:
                part = self.part
            self._release()
            if part is None or part not in self.parts:
                self.playing = False
                self._switch_at = self._next = None
                self._notify()
                return None
            changed = part != self.part
            self._begin(part, boundary)
            if changed:
                self._notify()
        return None

    def _ensure_thread(self) -> None:
        if not self._threaded:
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="rythme", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        with self._cond:
            while True:
                due = self.step()
                if due is None:
                    self._cond.wait()
                    continue
                delay = due - self._clock()
                if delay > MAX_WAIT_S:
                    # Attente longue : interrompue dès qu'un ordre arrive.
                    self._cond.wait(timeout=delay - MAX_WAIT_S / 2)
                elif delay > 0:
                    # Dernière approche : time.sleep est précis à la milliseconde (Windows compris,
                    # Python 3.11+), contrairement à l'attente sur verrou (minuterie système ~15 ms).
                    self._cond.release()
                    try:
                        time.sleep(delay)
                    finally:
                        self._cond.acquire()


# --- Protocole ----------------------------------------------------------------------------------


def is_rhythm(msg: dict[str, Any]) -> bool:
    return msg.get("type") in ("rhythm", "rhythm-list", "time")


class RhythmBox:
    """Bibliothèque + lecteur, branchés sur les messages de l'app."""

    def __init__(
        self,
        folder: Path,
        send: Callable[[mido.Message], None],
        broadcast: Callable[[dict[str, Any]], None] = lambda _m: None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.library = Library(folder)
        self.log = log
        self.broadcast = broadcast
        self.player = RhythmPlayer(
            send, on_state=lambda s: self.broadcast(s), on_clock=lambda c: self.broadcast(c)
        )

    def listing(self) -> dict[str, Any]:
        self.library.scan()
        return {"type": "rhythms", "folder": str(self.library.folder), "styles": self.library.describe()}

    def handle(self, msg: dict[str, Any], reply: Callable[[dict[str, Any]], None]) -> None:
        """Message `rhythm-list` ou `rhythm` de l'app. Les erreurs sont renvoyées à l'app."""
        if msg.get("type") == "time":
            # Heure du bridge (même horloge que les annonces de mesure), pour mesurer le décalage.
            reply({"type": "time", "t": msg.get("t"), "server": self.player.now_ms()})
            return
        if msg.get("type") == "rhythm-list":
            reply(self.listing())
            reply(self.player.state())
            return
        action = msg.get("action")
        tempo = _number(msg.get("tempo"))
        channel = _channel(msg.get("channel"))
        volume = _number(msg.get("volume"))
        vol = int(volume) if volume is not None else None
        try:
            if action == "play":
                style = msg.get("style")
                if not isinstance(style, str) or not style:
                    raise RhythmError("aucun rythme choisi")
                part = msg.get("part") if isinstance(msg.get("part"), str) else None
                delay = _number(msg.get("delay")) or 0.0
                self.player.play(style, self.library.load(style), part, tempo, channel, vol, delay)
                self.log(f"♪ rythme « {style} »" + (f" ({part})" if part else "") + f", {tempo or self.player.tempo:g} noires/min")
            elif action == "part":
                part = msg.get("part")
                if isinstance(part, str):
                    self.player.trigger(part)
                    self.log(f"♪ rythme → {part} (mesure suivante)")
            elif action == "stop":
                self.player.stop()
                self.log("♪ rythme arrêté")
            elif action == "set":
                self.player.set(tempo, channel, vol)
            elif action == "sync":
                self.player.announce()
            else:
                raise RhythmError(f"action inconnue : {action!r}")
        except RhythmError as e:
            self.log(f"? rythme : {e}")
            reply({"type": "rhythm-error", "message": str(e)})


def _number(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _channel(v: Any) -> int | None:
    """Canal 1–16 du JSON → 0–15 pour mido."""
    return v - 1 if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 16 else None
