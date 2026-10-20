"""Bridge StageKontrol : WebSocket / USB → MIDI."""

import os

__version__ = "1.3.0"

# Le navigateur envoie au bridge tous ses cookies « localhost », quel que soit le port (Metro, Expo,
# autres outils de développement…) : leur ligne peut dépasser la limite de websockets (8 Ko par
# ligne d'en-tête) et la page d'état ne s'ouvre plus (erreur 431). On la relève à 64 Ko. websockets
# lit cette valeur au chargement : elle doit être fixée avant tout import de websockets.
os.environ.setdefault("WEBSOCKETS_MAX_LINE_LENGTH", str(64 * 1024))
