# Recette PyInstaller du bridge StageKontrol (Windows et macOS) : un dossier « StageKontrol-Bridge »
# contenant l'exécutable (avec console : le journal du bridge s'y affiche) et ses bibliothèques.
# Mode dossier plutôt que fichier unique : pas de décompression à chaque lancement, démarrage rapide. Construire depuis le dossier bridge/ :
#     pyinstaller stagekontrol-bridge.spec
# Le .exe Windows doit être construit sous Windows (voir .github/workflows/bridge.yml).

import sys

from PyInstaller.utils.hooks import collect_submodules

# mido charge son moteur MIDI dynamiquement ; zeroconf a des sous-modules compilés.
hidden = ["mido.backends.rtmidi", "rtmidi"] + collect_submodules("zeroconf")

a = Analysis(
    ["run_bridge.py"],
    pathex=["."],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="StageKontrol-Bridge",
    console=True,
    icon="assets/icon.ico" if sys.platform == "win32" else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="StageKontrol-Bridge", upx=False)
