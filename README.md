# StageKontrol Bridge

Programme à lancer sur l'ordinateur (Windows ou Mac) pour relier l'app **StageKontrol** (téléphone)
à votre logiciel musical (Kontakt, Reaper, DAW…).

## Télécharger (utilisateurs)

Dernière version : **[Releases](https://github.com/Peype/midi-bridge/releases/latest)** — choisissez
le fichier de votre système, décompressez-le et suivez le fichier LISEZMOI qu'il contient :

- `StageKontrol-Bridge-Windows.zip` (nécessite [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html))
- `StageKontrol-Bridge-macOS-AppleSilicon.zip` (Mac M1, M2, M3…)
- `StageKontrol-Bridge-macOS-Intel.zip`

La suite de ce document s'adresse aux développeurs (lancer le bridge depuis le code source).

- **macOS** : le bridge crée son propre port MIDI virtuel **stageKontrol**.
- **Windows** : créez d'abord dans [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
  un port nommé exactement **stageKontrol** ; le bridge l'ouvre par son nom.

Dans le logiciel musical, choisissez l'entrée MIDI **stageKontrol**.

## Connexion : Wi-Fi (trouvé automatiquement) ou câble USB

- **Wi-Fi** : le bridge s'annonce sur le réseau (mDNS, service `_stagekontrol._tcp`). Dans l'app,
  Réglages → « Bridges trouvés sur le réseau » → **Utiliser**. La saisie manuelle de l'IP reste
  possible. Astuce : le point d'accès du téléphone fonctionne aussi (pensez aux données mobiles).
- **Câble USB** : branchez le téléphone au PC et choisissez **« MIDI »** dans la notification USB
  d'Android. Le bridge reconnaît le téléphone tout seul (il écoute toutes les entrées MIDI et
  reconnaît le SysEx StageKontrol) et retransmet son MIDI sur « stageKontrol » : même entrée MIDI
  qu'en Wi-Fi. Le câble est prioritaire ; s'il est débranché, l'app repasse en Wi-Fi.

## Installation (depuis le code source)

Python 3.10 ou plus récent. Depuis ce dossier :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Sous Windows, remplacez `.venv/bin/` par `.venv\Scripts\`.

## Lancement

```bash
.venv/bin/python run_bridge.py
```

Le bridge affiche l'adresse IP à saisir dans les réglages de l'app (⚙). Le téléphone et le PC
doivent être sur le même réseau Wi-Fi.

| Option | Rôle |
|---|---|
| `--port 8765` | port WebSocket (le même que dans l'app) |
| `--midi-port stageKontrol` | nom du port MIDI |
| `--dry-run` | sans MIDI : affiche seulement les messages reçus |
| `--quiet` | n'affiche pas chaque message MIDI |

Un seul appareil à la fois : si un deuxième appareil se connecte, il remplace le premier, qui
affiche « Remplacé » (le toucher reprend la main).

## Clavier maître : octave et zones par piste

Branchez le clavier maître sur le PC : le bridge le lit et envoie chaque note **sur le canal de
chaque piste qui sonne** (active, non mutée, solo compris), **transposée de l'octave de la piste**
(réglage « − Oct + » sur chaque tranche de l'app). Exemple : Piano à 0 sur le canal 1, Pad à −1
sur le canal 2 → Do3 joué = Do3 sur le canal 1 et Do2 sur le canal 2.

Chaque piste peut aussi avoir une **zone** (split) : elle ne reçoit que les touches de sa zone
(appliquée à la touche jouée, avant l'octave). Exemple main gauche / main droite : Basse sur les
touches basses, Piano sur les touches hautes ; des zones qui se chevauchent superposent les
instruments. Les bornes s'apprennent au clavier dans l'éditeur de piste (le bridge renvoie à l'app
chaque touche enfoncée).

Le clavier peut n'envoyer que sur **un seul canal** (ex. M-Audio Keystation MK3) : c'est le bridge
qui redistribue sur les canaux des pistes. Seuls les contrôles de jeu du clavier sont transmis :
molette (CC 1), expression (CC 11), pédales (CC 64, 66, 67), pitch bend et aftertouch ; les autres
CC (ex. le fader de volume du clavier, CC 7) sont ignorés pour ne pas écraser les réglages de l'app.

- Toutes les entrées MIDI du PC (sauf les téléphones branchés en USB) sont traitées comme des
  claviers ; `--keyboard "nom"` en choisit une seule (`--list-midi` affiche les noms).
  `--no-keyboard` désactive le routage. Un clavier branché en cours de route est pris en compte.
- **Dans le logiciel (Kontakt, Reaper…), désactivez le clavier comme entrée MIDI directe** et
  gardez seulement l'entrée « stageKontrol », sinon chaque note sera jouée deux fois.
- Chaque instrument doit écouter son propre canal (Kontakt : A1, A2…).
- Une note est toujours relâchée là où elle a été jouée, même si l'octave change pendant qu'elle
  est tenue. Sustain, pitch bend et molette suivent le routage.
- Si l'app se déconnecte, le dernier routage reste actif : le clavier continue de jouer.

| Option | Rôle |
|---|---|
| `--keyboard "nom"` | limite le routage à cette entrée MIDI (par défaut : toutes les entrées sauf les téléphones) |
| `--no-keyboard` | pas de routage du clavier |
| `--list-midi` | affiche les ports MIDI et quitte |

## Vérifier sans logiciel musical

- macOS : [MIDI Monitor](https://www.snoize.com/midimonitor/) (Snoize), source « stageKontrol ».
- Windows : MIDI-OX ou Protokol.

## Protocole

Canaux en 1–16 dans le JSON ; seul le bridge les convertit en 0–15.

```json
{ "type": "midi", "message": "cc", "channel": 1, "number": 7, "value": 90 }
{ "type": "midi", "message": "note", "channel": 16, "note": 38, "velocity": 127, "state": "on" }
{ "type": "midi", "message": "program", "channel": 1, "program": 2 }
{ "type": "ping" }
{ "type": "routing", "targets": [{ "channel": 1, "octave": 1, "low": 0, "high": 59 }, { "channel": 2, "octave": 0, "low": 60, "high": 127 }] }
```

Câble USB : les messages MIDI passent en octets MIDI ; les autres (`ping`, `pong`, `routing`,
`key`) dans un SysEx `F0 7D 53 4B <JSON ASCII> F7` (`7D` = identifiant « non commercial »).

Envoyé par le bridge à l'app :

```json
{ "type": "key", "note": 60 }
```

- `note` avec `"state": "off"` envoie un vrai Note Off (0x80).
- `ping` → le bridge répond `{ "type": "pong" }` (l'app coupe sans pong pendant 5 s).
- Un message invalide est ignoré et signalé dans le terminal.
- Code de fermeture `4001` : connexion remplacée par un autre appareil.
- `routing` : cibles du clavier maître (canal 1–16, octave −3…+3, zone `low`–`high` facultative,
  0–127 par défaut), envoyé par l'app à chaque changement et à chaque connexion. Liste vide = le
  clavier ne joue rien.
- `key` : touche enfoncée sur le clavier maître (apprentissage des zones dans l'app).

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Publier une version (versions Windows et Mac)

GitHub Actions (`.github/workflows/build.yml`) construit les trois versions avec PyInstaller
(`stagekontrol-bridge.spec`) et les publie dans une release, à chaque tag `v*` :

```bash
git tag v1.0.0 && git push origin v1.0.0
```

L'app télécharge toujours la dernière release (`releases/latest/download/<fichier>`) : gardez les
mêmes noms de fichiers. Construction locale (système courant) :
`.venv/bin/pip install -r requirements-build.txt && .venv/bin/pyinstaller stagekontrol-bridge.spec`.
