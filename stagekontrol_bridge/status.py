"""Page d'état du bridge, servie sur le même port que le WebSocket (http://localhost:8765/).

Elle s'ouvre dans le navigateur au lancement et montre :
- le QR code à scanner avec le téléphone (lien stagekontrol://connect?host=…&port=…) ;
- l'appareil connecté (Wi-Fi ou USB), les claviers détectés ;
- l'état du port MIDI (sur Windows : que faire si le port loopMIDI manque).
"""

from __future__ import annotations

import json
import platform
from typing import Any, Callable
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from . import __version__

LOOPMIDI_URL = "https://www.tobias-erichsen.de/software/loopmidi.html"


def connect_url(host: str, port: int, name: str) -> str:
    """Lien ouvert par le téléphone en scannant le QR code."""
    return "stagekontrol://connect?" + urlencode({"host": host, "port": port, "name": name})


def qr_svg(text: str) -> str:
    """QR code en SVG (noir sur blanc : le plus facile à lire pour un appareil photo)."""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


def _response(status: int, content_type: str, body: str | bytes) -> Response:
    data = body.encode("utf-8") if isinstance(body, str) else body
    headers = Headers([("Content-Type", content_type), ("Content-Length", str(len(data))), ("Cache-Control", "no-store")])
    reason = {200: "OK", 404: "Not Found"}.get(status, "OK")
    return Response(status, reason, headers, data)


class StatusPage:
    def __init__(self, snapshot: Callable[[], dict[str, Any]]) -> None:
        # Fonction qui décrit l'état courant du bridge (voir __main__.py).
        self.snapshot = snapshot

    def process_request(self, _connection: Any, request: Request) -> Response | None:
        """Crochet de websockets : répond aux requêtes HTTP, laisse passer les WebSocket."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        path = request.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return _response(200, "text/html; charset=utf-8", PAGE)
        if path == "/status.json":
            return _response(200, "application/json", json.dumps(self.snapshot(), ensure_ascii=False))
        if path.startswith("/qr/") and path.endswith(".svg"):
            urls = self.snapshot().get("connect_urls", [])
            try:
                index = int(path[len("/qr/") : -len(".svg")])
                return _response(200, "image/svg+xml", qr_svg(urls[index]["url"]))
            except (ValueError, IndexError):
                pass
        return _response(404, "text/plain; charset=utf-8", "Introuvable")


def build_snapshot(
    *,
    name: str,
    port: int,
    ips: list[str],
    midi_name: str,
    midi_ok: bool,
    midi_error: str | None,
    wifi_peer: str | None,
    usb_phones: list[str],
    keyboards: list[str],
    keyboard_routing: bool,
) -> dict[str, Any]:
    return {
        "version": __version__,
        "name": name,
        "system": platform.system(),
        "port": port,
        "connect_urls": [{"ip": ip, "url": connect_url(ip, port, name)} for ip in ips],
        "midi": {"name": midi_name, "ok": midi_ok, "error": midi_error, "loopmidi_url": LOOPMIDI_URL},
        "wifi": wifi_peer,
        "usb": usb_phones,
        "keyboards": keyboards,
        "keyboard_routing": keyboard_routing,
    }


# Page autonome (aucune ressource externe) : couleurs du logo, mise à jour chaque seconde.
PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StageKontrol Bridge</title>
<style>
  :root { --bg:#0b0d11; --panel:#1b212b; --line:#2a3240; --text:#eef1f5; --dim:#8793a3;
          --yellow:#fcd11b; --light:#faf5eb; --ok:#3ddc84; --bad:#ff5a5a; --warn:#ffb020; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 980px; margin: 0 auto; padding: 24px 16px 40px; }
  h1 { font-size: 30px; margin: 0 0 4px; letter-spacing: .3px; }
  h1 .s { color: var(--light); } h1 .k { color: var(--yellow); }
  .tag { color: var(--dim); letter-spacing: 3px; font-size: 12px; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: minmax(260px, 340px) 1fr; gap: 16px; }
  @media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
  .card h2 { font-size: 15px; color: var(--dim); font-weight: 600; margin: 0 0 10px; }
  .qr { background: #fff; border-radius: 10px; padding: 8px; }
  .qr img { width: 100%; display: block; }
  .ip { text-align: center; color: var(--dim); margin-top: 8px; font-size: 14px; }
  .row { display: flex; align-items: center; gap: 10px; padding: 6px 0; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; background: var(--dim); }
  .ok .dot { background: var(--ok); } .bad .dot { background: var(--bad); } .wait .dot { background: var(--warn); }
  .muted { color: var(--dim); font-size: 14px; }
  ol { margin: 6px 0 0; padding-left: 20px; } li { margin: 4px 0; }
  .alert { border-color: var(--bad); }
  a { color: var(--yellow); }
  code { background: #0d1117; padding: 1px 6px; border-radius: 6px; }
  .stack { display: grid; gap: 16px; }
</style>
</head>
<body>
<main>
  <h1><span class="s">Stage</span><span class="k">Kontrol</span> Bridge</h1>
  <div class="tag">MOBILE MIDI CONTROL SURFACE</div>

  <div class="grid">
    <div class="card">
      <h2>Scannez avec l'appareil photo du téléphone</h2>
      <div id="qrs"></div>
      <p class="muted">Le téléphone et l'ordinateur doivent être sur le même Wi-Fi. Sans Wi-Fi :
        câble USB, puis « MIDI » dans la notification USB du téléphone.</p>
    </div>

    <div class="stack">
      <div class="card" id="midi-card">
        <h2>Port MIDI</h2>
        <div id="midi"></div>
      </div>
      <div class="card">
        <h2>Téléphone</h2>
        <div id="phone"></div>
      </div>
      <div class="card">
        <h2>Clavier maître</h2>
        <div id="keys"></div>
      </div>
    </div>
  </div>
  <p class="muted" id="foot"></p>
</main>
<script>
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const row = (cls, text, sub) =>
    `<div class="row ${cls}"><span class="dot"></span><div>${text}${sub ? `<div class="muted">${sub}</div>` : ''}</div></div>`;
  let qrKey = '';

  async function refresh() {
    let s;
    try { s = await (await fetch('/status.json', { cache: 'no-store' })).json(); }
    catch { $('foot').textContent = 'Bridge arrêté : relancez StageKontrol Bridge.'; return; }

    const key = s.connect_urls.map((u) => u.url).join('|');
    if (key !== qrKey) {
      qrKey = key;
      $('qrs').innerHTML = s.connect_urls.length
        ? s.connect_urls.map((u, i) => `<div class="qr"><img alt="QR code" src="/qr/${i}.svg?${Date.now()}"></div>
            <div class="ip">${esc(u.ip)}:${s.port}</div>`).join('')
        : '<p class="muted">Aucune adresse Wi-Fi : branchez le téléphone en USB.</p>';
    }

    const m = s.midi;
    $('midi-card').classList.toggle('alert', !m.ok);
    $('midi').innerHTML = m.ok
      ? row('ok', `Port « ${esc(m.name)} » prêt`, 'Dans votre logiciel, choisissez cette entrée MIDI.')
      : row('bad', `Port « ${esc(m.name)} » introuvable`) +
        (s.system === 'Windows'
          ? `<ol><li>Installez <a href="${m.loopmidi_url}" target="_blank">loopMIDI</a> (gratuit) et ouvrez-le.</li>
               <li>Dans « New port-name », tapez exactement <code>${esc(m.name)}</code>, puis cliquez sur <b>+</b>.</li>
               <li>Laissez cette page ouverte : le bridge prend le port tout seul.</li>
               <li>Astuce : dans loopMIDI, activez le démarrage avec Windows pour ne plus y penser.</li></ol>`
          : `<p class="muted">${esc(m.error || '')}</p>`);

    const phone = [];
    for (const p of s.usb) phone.push(row('ok', 'Connecté en USB', esc(p)));
    if (s.wifi) phone.push(row('ok', 'Connecté en Wi-Fi', esc(s.wifi)));
    $('phone').innerHTML = phone.length ? phone.join('') : row('wait', 'En attente du téléphone…', 'Scannez le QR code, ou branchez le câble USB.');

    $('keys').innerHTML = !s.keyboard_routing
      ? row('', 'Routage du clavier désactivé')
      : s.keyboards.length
        ? s.keyboards.map((k) => row('ok', esc(k))).join('') +
          '<p class="muted">Désactivez ce clavier comme entrée directe dans votre logiciel (sinon notes en double).</p>'
        : row('wait', 'Aucun clavier détecté', 'Branchez-le : il est pris en compte automatiquement.');

    $('foot').textContent = `StageKontrol Bridge ${s.version} · ${s.name} · fermez la fenêtre du bridge pour l'arrêter.`;
  }
  refresh();
  setInterval(refresh, 1000);
</script>
</body>
</html>
"""
