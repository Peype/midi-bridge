"""Point d'entrée du bridge (aussi utilisé par PyInstaller pour les versions Windows et macOS)."""

import sys

from stagekontrol_bridge.__main__ import main


def pause_if_packaged(code: int) -> None:
    """Version « double-clic » : en cas d'erreur, la fenêtre reste ouverte le temps de lire le message."""
    if getattr(sys, "frozen", False) and code != 0:
        try:
            input("\nAppuyez sur Entrée pour fermer…")
        except EOFError:
            pass


if __name__ == "__main__":
    code = main()
    pause_if_packaged(code)
    sys.exit(code)
