from pathlib import Path

import mido
import pytest

from stagekontrol_bridge.rhythm import (
    Library,
    RhythmBox,
    RhythmError,
    RhythmPlayer,
    read_pattern,
    split_name,
)


def midi_file(notes, length_ticks=None, signature=(4, 4), tpb=480) -> mido.MidiFile:
    """Fichier MIDI : `notes` = [(noire de début, durée en noires, note, vélocité)]."""
    events = []
    for start, duration, note, velocity in notes:
        events.append((round(start * tpb), mido.Message("note_on", channel=0, note=note, velocity=velocity)))
        events.append((round((start + duration) * tpb), mido.Message("note_off", channel=0, note=note, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1].type == "note_on"))
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("time_signature", numerator=signature[0], denominator=signature[1], time=0))
    tick = 0
    for at, msg in events:
        track.append(msg.copy(time=at - tick))
        tick = at
    end = length_ticks if length_ticks is not None else tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, end - tick)))
    mid = mido.MidiFile(ticks_per_beat=tpb)
    mid.tracks.append(track)
    return mid


KICK, SNARE, HAT = 36, 38, 42
# Une mesure de 4/4 : grosse caisse sur 1 et 3, caisse claire sur 2 et 4.
GROOVE = [(0, 0.25, KICK, 100), (1, 0.25, SNARE, 90), (2, 0.25, KICK, 100), (3, 0.25, SNARE, 90)]
FILL = [(0, 0.25, SNARE, 120), (2, 0.25, SNARE, 120)]


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def player(clock: Clock):
    sent: list[tuple[float, str, int, int, int]] = []
    states: list[dict] = []

    def send(msg: mido.Message) -> None:
        sent.append((round(clock.t, 3), msg.type, msg.channel, msg.note, msg.velocity))

    p = RhythmPlayer(send, on_state=states.append, clock=clock, threaded=False)
    return p, sent, states


def run_until(p: RhythmPlayer, clock: Clock, t: float) -> None:
    """Fait avancer le jeu jusqu'à `t`, échéance par échéance (comme le fil d'exécution)."""
    while True:
        due = p.step()
        if due is None or due > t:
            clock.t = t
            p.step()
            return
        clock.t = due


def note_ons(sent):
    return [(t, note, vel) for t, kind, _ch, note, vel in sent if kind == "note_on"]


def parts(*pairs):
    return {name: read_pattern(midi_file(notes)) for name, notes in pairs}


class TestFichiers:
    def test_longueur_arrondie_a_la_mesure_et_positions_en_noires(self):
        p = read_pattern(midi_file([(0, 0.5, KICK, 100), (2.5, 0.5, SNARE, 80)]))
        assert p.length == 4 and p.bar == 4
        assert [(e.beat, e.note, e.velocity) for e in p.events if e.velocity] == [(0, KICK, 100), (2.5, SNARE, 80)]

    def test_mesure_a_12_8_et_fin_de_fichier(self):
        p = read_pattern(midi_file([(0, 0.5, KICK, 100)], length_ticks=480 * 12, signature=(12, 8)))
        assert p.bar == 6 and p.length == 12  # deux mesures de 12/8 (6 noires chacune)

    def test_releve_apres_la_fin_ramene_dans_la_boucle(self):
        # Relâchement pile sur la fin de la mesure : il doit partir avant que la boucle reprenne.
        p = read_pattern(midi_file([(3.5, 0.5, HAT, 70)]))
        off = [e for e in p.events if e.velocity == 0][0]
        assert p.length == 4 and off.beat < 4

    def test_fichier_sans_note_refuse(self):
        with pytest.raises(RhythmError):
            read_pattern(midi_file([]))

    def test_noms_des_fichiers(self):
        assert split_name("Salegy - Break") == ("Salegy", "Break")
        assert split_name("Salegy - fill") == ("Salegy", "Break")
        assert split_name("Tsapiky") == ("Tsapiky", "A")
        assert split_name("Hira - Gasy - B") == ("Hira - Gasy", "B")

    def test_bibliotheque(self, tmp_path: Path):
        midi_file(GROOVE).save(tmp_path / "Salegy - A.mid")
        midi_file(FILL).save(tmp_path / "Salegy - Break.mid")
        midi_file(GROOVE).save(tmp_path / "Salegy - Intro.mid")
        midi_file(GROOVE).save(tmp_path / "Pop.mid")
        (tmp_path / "notes.txt").write_text("pas un rythme")
        lib = Library(tmp_path)
        lib.scan()
        assert lib.describe() == [
            {"name": "Pop", "parts": ["A"]},
            {"name": "Salegy", "parts": ["Intro", "A", "Break"]},
        ]
        assert set(lib.load("Salegy")) == {"A", "Break", "Intro"}
        with pytest.raises(RhythmError):
            lib.load("Inconnu")


class TestLecture:
    def test_joue_en_boucle_au_tempo_sur_le_canal_choisi(self):
        clock = Clock()
        p, sent, _ = player(clock)
        p.play("Pop", parts(("A", GROOVE)), tempo=120, channel=9)  # noire = 0,5 s
        run_until(p, clock, 2.5)
        assert note_ons(sent) == [
            (0.05, KICK, 100), (0.55, SNARE, 90), (1.05, KICK, 100), (1.55, SNARE, 90),
            (2.05, KICK, 100),  # 2e tour de boucle
        ]
        assert {ch for _t, _k, ch, _n, _v in sent} == {9}

    def test_volume_proportionnel_a_la_velocite(self):
        clock = Clock()
        p, sent, _ = player(clock)
        p.play("Pop", parts(("A", GROOVE)), tempo=120, volume=50)
        run_until(p, clock, 0.1)
        assert note_ons(sent) == [(0.05, KICK, 50)]

    def test_changement_de_partie_a_la_mesure_suivante(self):
        clock = Clock()
        p, sent, states = player(clock)
        p.play("Pop", parts(("A", GROOVE), ("B", FILL)), tempo=120)
        run_until(p, clock, 0.7)
        p.trigger("B")
        assert states[-1]["next"] == "B"
        run_until(p, clock, 2.2)
        # A finit sa mesure (jusqu'à 2,05 s), puis B commence.
        assert note_ons(sent)[-1] == (2.05, SNARE, 120)
        assert p.part == "B"

    def test_break_puis_retour_a_la_partie_en_cours(self):
        clock = Clock()
        p, sent, _ = player(clock)
        p.play("Pop", parts(("A", GROOVE), ("Break", FILL)), tempo=120)
        p.trigger("Break")
        run_until(p, clock, 4.2)
        ons = note_ons(sent)
        # Le break était demandé avant le 1er temps : il commence à la mesure en cours (0,05 s).
        assert ons[:2] == [(0.05, SNARE, 120), (1.05, SNARE, 120)]
        assert ons[2] == (2.05, KICK, 100)
        assert p.part == "A"

    def test_intro_enchaine_sur_a_et_fin_arrete(self):
        clock = Clock()
        p, sent, states = player(clock)
        p.play("Pop", parts(("Intro", FILL), ("A", GROOVE), ("Fin", [(0, 0.5, KICK, 127)])), part="Intro", tempo=120)
        run_until(p, clock, 2.1)
        assert p.part == "A"
        p.trigger("Fin")
        run_until(p, clock, 10)
        assert note_ons(sent)[-1] == (4.05, KICK, 127)
        assert p.playing is False and states[-1]["playing"] is False

    def test_changement_de_tempo_sans_saut(self):
        clock = Clock()
        p, sent, _ = player(clock)
        p.play("Pop", parts(("A", GROOVE)), tempo=120)
        run_until(p, clock, 0.3)  # temps 0,5 (entre la grosse caisse et la caisse claire)
        p.set(tempo=60)  # noire = 1 s : il reste une demi-noire avant la caisse claire
        run_until(p, clock, 1.0)
        assert note_ons(sent)[-1] == (0.8, SNARE, 90)

    def test_arret_relache_les_notes_tenues(self):
        clock = Clock()
        p, sent, _ = player(clock)
        p.play("Pop", parts(("A", [(0, 2, KICK, 100)])), tempo=120)
        run_until(p, clock, 0.1)
        p.stop()
        assert sent[-1][1:] == ("note_off", 9, KICK, 0)
        assert p.step() is None


class TestProtocole:
    def test_liste_puis_lecture_et_erreur(self, tmp_path: Path):
        midi_file(GROOVE).save(tmp_path / "Salegy.mid")
        sent, replies = [], []
        box = RhythmBox(tmp_path, sent.append, log=lambda _t: None)
        box.player._threaded = False
        box.handle({"type": "rhythm-list"}, replies.append)
        from stagekontrol_bridge import __version__

        assert replies[0] == {
            "type": "rhythms",
            "folder": str(tmp_path),
            "styles": [{"name": "Salegy", "parts": ["A"]}],
            "version": __version__,
        }
        box.handle({"type": "rhythm", "action": "play", "style": "Salegy", "tempo": 96, "channel": 10}, replies.append)
        assert box.player.playing and box.player.channel == 9 and box.player.tempo == 96
        box.handle({"type": "rhythm", "action": "play", "style": "Absent"}, replies.append)
        assert replies[-1]["type"] == "rhythm-error"
        box.handle({"type": "rhythm", "action": "stop"}, replies.append)
        assert not box.player.playing


class TestSynchronisation:
    def test_annonce_chaque_debut_de_mesure_sur_l_horloge_du_bridge(self):
        clock = Clock()
        clocks: list[dict] = []
        p = RhythmPlayer(lambda _m: None, clock=clock, threaded=False, on_clock=clocks.append)
        p.play("Pop", parts(("A", GROOVE)), tempo=120, delay=1.0)  # départ à 1,05 s
        assert clocks[-1] == {"type": "rhythm-clock", "at": pytest.approx(1050.0), "tempo": 120}
        run_until(p, clock, 3.2)  # 2e tour de boucle à 3,05 s
        assert clocks[-1]["at"] == pytest.approx(3050.0)

    def test_changement_de_tempo_annonce_la_mesure_en_cours(self):
        clock = Clock()
        clocks: list[dict] = []
        p = RhythmPlayer(lambda _m: None, clock=clock, threaded=False, on_clock=clocks.append)
        p.play("Pop", parts(("A", GROOVE)), tempo=120)
        run_until(p, clock, 1.05)  # temps 2 de la mesure
        p.set(tempo=60)
        # Ancre sur la nouvelle grille : le temps 1 de la mesure, deux noires (2 s à 60) avant 1,05 s.
        assert clocks[-1]["at"] == pytest.approx(-950.0) and clocks[-1]["tempo"] == 60

    def test_heure_du_bridge(self, tmp_path: Path):
        replies = []
        box = RhythmBox(tmp_path, lambda _m: None, log=lambda _t: None)
        box.handle({"type": "time", "t": 123.5}, replies.append)
        assert replies[0]["type"] == "time" and replies[0]["t"] == 123.5
        assert isinstance(replies[0]["server"], float)


class TestImportDepuisLaPage:
    def test_import_verifie_et_range_sous_le_bon_nom(self, tmp_path: Path):
        lib = Library(tmp_path / "Rythmes")
        data = _bytes(midi_file(GROOVE))
        assert lib.save("  Salegy ", "fill", data) == ("Salegy", "Break")
        assert (tmp_path / "Rythmes" / "Salegy - Break.mid").read_bytes() == data
        assert lib.describe() == [{"name": "Salegy", "parts": ["Break"]}]

    def test_meme_style_autres_majuscules_et_remplacement_d_une_partie(self, tmp_path: Path):
        lib = Library(tmp_path)
        lib.save("Salegy", "A", _bytes(midi_file(GROOVE)))
        lib.save("salegy", "A", _bytes(midi_file(FILL)))
        assert [p.name for p in tmp_path.iterdir()] == ["Salegy - A.mid"]
        assert read_pattern(mido.MidiFile(tmp_path / "Salegy - A.mid")).events[0].note == SNARE

    def test_refuse_un_fichier_qui_n_est_pas_un_rythme(self, tmp_path: Path):
        lib = Library(tmp_path)
        for style, part, data in [("S", "A", b"pas du midi"), ("S", "A", b""), ("", "A", _bytes(midi_file(GROOVE))),
                                  ("S", "A", _bytes(midi_file([])))]:
            with pytest.raises(RhythmError):
                lib.save(style, part, data)
        assert list(tmp_path.iterdir()) == []

    def test_nom_de_style_sans_caracteres_interdits(self):
        from stagekontrol_bridge.rhythm import clean_style

        assert clean_style('  Sa/le:gy?  ') == "Sa le gy"
        assert clean_style("...") == ""

    def test_supprimer_et_renommer(self, tmp_path: Path):
        lib = Library(tmp_path)
        lib.save("Salegy", "A", _bytes(midi_file(GROOVE)))
        lib.save("Salegy", "Break", _bytes(midi_file(FILL)))
        lib.save("Pop", "A", _bytes(midi_file(GROOVE)))
        assert lib.delete("Salegy", "Break") == 1
        assert lib.rename("Salegy", "Salegy 6/8") == "Salegy 6 8"
        with pytest.raises(RhythmError):
            lib.rename("Pop", "salegy 6 8")
        assert lib.describe() == [{"name": "Pop", "parts": ["A"]}, {"name": "Salegy 6 8", "parts": ["A"]}]
        assert lib.delete("Pop") == 1

    def test_demandes_de_la_page_et_nouvelle_liste_pour_le_telephone(self, tmp_path: Path):
        import base64

        replies, published = [], []
        box = RhythmBox(tmp_path, lambda _m: None, broadcast=published.append, log=lambda _t: None)
        data = base64.b64encode(_bytes(midi_file(GROOVE))).decode()
        box.admin({"type": "rhythm-upload", "name": "f0", "style": "Salegy", "part": "A", "data": data}, replies.append)
        assert replies[-1] == {"type": "rhythm-saved", "style": "Salegy", "part": "A", "name": "f0"}
        assert published[-1]["styles"] == [{"name": "Salegy", "parts": ["A"]}]
        box.admin({"type": "rhythm-upload", "name": "f1", "style": "X", "part": "A", "data": "@@@"}, replies.append)
        assert replies[-1]["type"] == "rhythm-error" and replies[-1]["name"] == "f1"
        box.admin({"type": "rhythm-delete", "style": "Salegy"}, replies.append)
        assert published[-1]["styles"] == []


def _bytes(mid: mido.MidiFile) -> bytes:
    import io

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()
