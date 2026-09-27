import mido

from stagekontrol_bridge.protocol import ProtocolError, parse, to_targets
from stagekontrol_bridge.router import Router, Target

import pytest


def make():
    sent: list[mido.Message] = []
    return Router(sent.append), sent


def on(note, vel=100, ch=0):
    return mido.Message("note_on", channel=ch, note=note, velocity=vel)


def off(note, ch=0):
    return mido.Message("note_off", channel=ch, note=note, velocity=0)


def summary(sent):
    return [(m.type, m.channel + 1, getattr(m, "note", None)) for m in sent]


def test_chaque_piste_recoit_la_note_avec_son_octave():
    r, sent = make()
    r.set_targets([Target(1, 0), Target(2, -1), Target(3, 1)])
    r.handle(on(60))
    assert summary(sent) == [("note_on", 1, 60), ("note_on", 2, 48), ("note_on", 3, 72)]


def test_changer_l_octave_d_une_piste_ne_touche_qu_elle():
    r, sent = make()
    r.set_targets([Target(1, 0), Target(2, -1)])
    r.set_targets([Target(1, 0), Target(2, -2)])
    r.handle(on(60))
    assert summary(sent) == [("note_on", 1, 60), ("note_on", 2, 36)]


def test_note_relachee_la_ou_elle_a_ete_jouee_meme_apres_un_changement():
    r, sent = make()
    r.set_targets([Target(1, 0), Target(2, -1)])
    r.handle(on(60))
    # Pendant que la note est tenue : octave du Pad changée, Piano retiré du routage.
    r.set_targets([Target(2, 1)])
    sent.clear()
    r.handle(off(60))
    assert summary(sent) == [("note_off", 1, 60), ("note_off", 2, 48)]


def test_note_on_velocite_0_vaut_note_off():
    r, sent = make()
    r.set_targets([Target(1, 0)])
    r.handle(on(64))
    sent.clear()
    r.handle(on(64, vel=0))
    assert summary(sent) == [("note_off", 1, 64)]


def test_note_hors_limites_ignoree_sur_ce_canal():
    r, sent = make()
    r.set_targets([Target(1, 0), Target(2, 3)])
    r.handle(on(100))  # +3 octaves = 136 : hors 0–127
    assert summary(sent) == [("note_on", 1, 100)]


def test_aucune_piste_active_le_clavier_ne_joue_rien():
    r, sent = make()
    r.handle(on(60))
    r.handle(off(60))
    assert sent == []


def test_sustain_et_pitch_bend_vers_les_canaux_du_routage():
    r, sent = make()
    r.set_targets([Target(1, 0), Target(2, -1), Target(2, 0)])  # canal 2 deux fois : un seul envoi
    r.handle(mido.Message("control_change", channel=0, control=64, value=127))
    r.handle(mido.Message("pitchwheel", channel=0, pitch=2000))
    assert [(m.type, m.channel + 1) for m in sent] == [
        ("control_change", 1),
        ("control_change", 2),
        ("pitchwheel", 1),
        ("pitchwheel", 2),
    ]


def test_canal_retire_pendant_le_sustain_recoit_sustain_relache():
    r, sent = make()
    r.set_targets([Target(1, 0), Target(2, 0)])
    r.handle(mido.Message("control_change", channel=0, control=64, value=127))
    sent.clear()
    r.set_targets([Target(1, 0)])
    assert [(m.type, m.channel + 1, m.control, m.value) for m in sent] == [("control_change", 2, 64, 0)]


def test_program_change_du_clavier_ignore():
    r, sent = make()
    r.set_targets([Target(1, 0)])
    r.handle(mido.Message("program_change", channel=0, program=5))
    assert sent == []


def test_all_notes_off_a_l_arret():
    r, sent = make()
    r.set_targets([Target(1, 0), Target(2, -1)])
    r.handle(on(60))
    sent.clear()
    r.all_notes_off()
    assert summary(sent) == [("note_off", 1, 60), ("note_off", 2, 48)]


def test_message_de_routage():
    targets = to_targets(parse('{"type":"routing","targets":[{"channel":1,"octave":0},{"channel":2,"octave":-1}]}'))
    assert targets == [Target(1, 0), Target(2, -1)]
    assert to_targets(parse('{"type":"routing","targets":[]}')) == []
    for bad in [
        '{"type":"routing"}',
        '{"type":"routing","targets":[{"channel":17,"octave":0}]}',
        '{"type":"routing","targets":[{"channel":1,"octave":4}]}',
    ]:
        with pytest.raises(ProtocolError):
            to_targets(parse(bad))


def test_zones_main_gauche_main_droite():
    r, sent = make()
    r.set_targets([Target(1, 1, low=0, high=59), Target(2, 0, low=60, high=127)])
    r.handle(on(48))  # main gauche : basse (+1 octave) seulement
    r.handle(on(72))  # main droite : piano seulement
    assert summary(sent) == [("note_on", 1, 60), ("note_on", 2, 72)]


def test_zones_qui_se_chevauchent_superposent():
    r, sent = make()
    r.set_targets([Target(1, 0, low=0, high=64), Target(2, -1, low=60, high=127)])
    r.handle(on(62))
    assert summary(sent) == [("note_on", 1, 62), ("note_on", 2, 50)]


def test_seuls_les_controles_de_jeu_passent():
    r, sent = make()
    r.set_targets([Target(1, 0)])
    for cc in (7, 1, 10, 11, 64, 66, 67, 74):
        r.handle(mido.Message("control_change", channel=0, control=cc, value=100))
    assert [m.control for m in sent] == [1, 11, 64, 66, 67]


def test_touche_signalee_pour_l_apprentissage():
    r, _ = make()
    keys = []
    r.on_key = keys.append
    r.handle(on(60))
    r.handle(off(60))
    r.handle(on(36))
    assert keys == [60, 36]


def test_routage_avec_zone():
    targets = to_targets(parse('{"type":"routing","targets":[{"channel":1,"octave":0,"low":60,"high":127}]}'))
    assert targets == [Target(1, 0, 60, 127)]
    with pytest.raises(ProtocolError):
        to_targets(parse('{"type":"routing","targets":[{"channel":1,"octave":0,"low":70,"high":60}]}'))
