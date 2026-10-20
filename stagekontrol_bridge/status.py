"""Page d'état du bridge, servie sur le même port que le WebSocket (http://localhost:8765/).

Elle s'ouvre dans le navigateur au lancement et montre :
- le QR code à scanner avec le téléphone (lien stagekontrol://connect?host=…&port=…) ;
- l'appareil connecté (Wi-Fi ou USB), les claviers détectés ;
- l'état du port MIDI (sur Windows : que faire si le port loopMIDI manque) ;
- les rythmes de la boîte à rythme : import par glisser-déposer, suppression, renommage (via la
  connexion WebSocket « /admin », réservée à l'ordinateur lui-même).
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
  .wide { margin-top: 16px; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  button { background: #262d38; color: var(--text); border: 1px solid var(--line); border-radius: 8px;
           padding: 6px 12px; font: inherit; font-size: 14px; cursor: pointer; }
  button:hover { border-color: var(--yellow); }
  button.go { background: var(--yellow); color: #111; border-color: var(--yellow); font-weight: 600; }
  button.danger:hover { border-color: var(--bad); color: var(--bad); }
  input, select { background: #0d1117; color: var(--text); border: 1px solid var(--line); border-radius: 8px;
                  padding: 6px 8px; font: inherit; font-size: 14px; }
  .drop { border: 2px dashed var(--line); border-radius: 12px; padding: 18px; text-align: center; color: var(--dim);
          cursor: pointer; margin: 10px 0; }
  .drop.over { border-color: var(--yellow); color: var(--text); }
  .style { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 0; border-top: 1px solid var(--line); }
  .style b { min-width: 140px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; background: #0d1117; border: 1px solid var(--line);
          border-radius: 999px; padding: 2px 4px 2px 10px; font-size: 14px; }
  .chip button { padding: 0 6px; border: none; background: none; color: var(--dim); }
  .chip button:hover { color: var(--bad); }
  .pending { display: grid; grid-template-columns: 1fr 170px 110px auto auto; gap: 8px; align-items: center; padding: 6px 0; }
  @media (max-width: 720px) { .pending { grid-template-columns: 1fr 1fr; } }
  .msg-ok { color: var(--ok); } .msg-bad { color: var(--bad); }
  .playing { color: var(--ok); font-size: 13px; }
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
  <div class="card wide" id="rhythms-card">
    <div class="head">
      <h2>Rythmes (boîte à rythme)</h2>
      <button id="open-folder" title="Ouvrir le dossier des rythmes">Ouvrir le dossier</button>
    </div>
    <p class="muted">Ajoutez vos rythmes de batterie (fichiers MIDI .mid) : glissez-les ici, choisissez le style
      et la partie (A et B tournent en boucle, Intro et Break une fois, Fin arrête). Le téléphone les voit
      aussitôt dans « Rythme ».</p>
    <div class="drop" id="drop">Glissez vos fichiers .mid ici, ou <u>cliquez pour les choisir</u>
      <input type="file" id="file" accept=".mid,.midi,audio/midi" multiple hidden></div>
    <div id="pending"></div>
    <div id="rmsg" class="muted"></div>
    <datalist id="style-names"></datalist>
    <div id="rlist"></div>
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

  // --- Rythmes : connexion « /admin » (seulement depuis cet ordinateur) ---
  const PARTS = ['Intro', 'A', 'B', 'C', 'Break', 'Fin'];
  const ALIASES = { a: 'A', b: 'B', c: 'C', intro: 'Intro', break: 'Break', fill: 'Break', roulement: 'Break',
                    fin: 'Fin', ending: 'Fin', end: 'Fin', outro: 'Fin' };
  let ws = null, styles = [], playing = null, pendingId = 0;
  const pending = new Map();

  function connectAdmin() {
    ws = new WebSocket(`ws://${location.host}/admin`);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'rhythm-list' }));
    ws.onclose = () => setTimeout(connectAdmin, 2000);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === 'rhythms') { styles = m.styles; renderList(); }
      else if (m.type === 'rhythm-state') { playing = m.playing ? m.style : null; renderList(); }
      else if (m.type === 'rhythm-saved') finishPending(m.name, true, `Importé : ${m.style} - ${m.part}`);
      else if (m.type === 'rhythm-error') {
        if (m.name && pending.has(m.name)) finishPending(m.name, false, m.message);
        else message(m.message, false);
      }
    };
  }
  const send = (msg) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); else message('Bridge non joignable.', false); };
  const message = (text, ok) => { $('rmsg').innerHTML = text ? `<span class="${ok ? 'msg-ok' : 'msg-bad'}">${esc(text)}</span>` : ''; };

  function renderList() {
    $('style-names').innerHTML = styles.map((s) => `<option value="${esc(s.name)}">`).join('');
    $('rlist').innerHTML = styles.length
      ? styles.map((s, i) => `<div class="style"><b>${esc(s.name)}</b>
          ${s.parts.map((p) => `<span class="chip">${esc(p)}<button title="Supprimer cette partie" data-del="${i}" data-part="${esc(p)}">✕</button></span>`).join('')}
          ${playing === s.name ? '<span class="playing">● en cours</span>' : ''}
          <span style="flex:1"></span>
          <button data-rename="${i}">Renommer</button>
          <button class="danger" data-delstyle="${i}">Supprimer</button></div>`).join('')
      : '<p class="muted">Aucun rythme pour l’instant.</p>';
  }

  $('rlist').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.del !== undefined) {
      const s = styles[+b.dataset.del];
      if (confirm(`Supprimer la partie ${b.dataset.part} de « ${s.name} » ?`)) send({ type: 'rhythm-delete', style: s.name, part: b.dataset.part });
    } else if (b.dataset.delstyle !== undefined) {
      const s = styles[+b.dataset.delstyle];
      if (confirm(`Supprimer le rythme « ${s.name} » et toutes ses parties ?`)) send({ type: 'rhythm-delete', style: s.name });
    } else if (b.dataset.rename !== undefined) {
      const s = styles[+b.dataset.rename];
      const to = prompt(`Nouveau nom pour « ${s.name} » :`, s.name);
      if (to && to.trim() && to.trim() !== s.name) send({ type: 'rhythm-rename', style: s.name, to: to.trim() });
    }
  });

  // Style et partie devinés à partir du nom « Style - Partie.mid » (modifiables avant l'import).
  function guess(filename) {
    const stem = filename.replace(/\\.midi?$/i, '');
    const i = stem.lastIndexOf(' - ');
    if (i < 0) return { style: stem.trim(), part: 'A' };
    const raw = stem.slice(i + 3).trim();
    return { style: stem.slice(0, i).trim(), part: ALIASES[raw.toLowerCase()] || raw };
  }

  function addFiles(files) {
    message('');
    for (const f of files) {
      if (!/\\.midi?$/i.test(f.name)) { message(`« ${f.name} » n'est pas un fichier .mid`, false); continue; }
      const id = `f${pendingId++}`;
      const g = guess(f.name);
      pending.set(id, f);
      const parts = PARTS.includes(g.part) ? PARTS : [...PARTS, g.part];
      const row = document.createElement('div');
      row.className = 'pending'; row.id = id;
      row.innerHTML = `<span>${esc(f.name)} <span class="muted">(${Math.ceil(f.size / 1024)} Ko)</span><div class="muted" data-status></div></span>
        <input list="style-names" data-style value="${esc(g.style)}" placeholder="Style (ex. Salegy)">
        <select data-part>${parts.map((p) => `<option${p === g.part ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select>
        <button class="go" data-go>Importer</button><button data-cancel>Retirer</button>`;
      $('pending').appendChild(row);
    }
  }

  $('pending').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    const row = b.closest('.pending'); const id = row.id;
    if (b.dataset.cancel !== undefined) { pending.delete(id); row.remove(); return; }
    const style = row.querySelector('[data-style]').value.trim();
    const part = row.querySelector('[data-part]').value;
    if (!style) { row.querySelector('[data-status]').innerHTML = '<span class="msg-bad">Indiquez le style.</span>'; return; }
    const reader = new FileReader();
    reader.onload = () => {
      const data = String(reader.result).split(',')[1] || '';
      row.querySelector('[data-status]').textContent = 'Envoi…';
      send({ type: 'rhythm-upload', name: id, style, part, data });
    };
    reader.readAsDataURL(pending.get(id));
  });

  function finishPending(id, ok, text) {
    const row = document.getElementById(id); if (!row) { message(text, ok); return; }
    if (ok) { pending.delete(id); row.remove(); message(text, true); }
    else row.querySelector('[data-status]').innerHTML = `<span class="msg-bad">${esc(text)}</span>`;
  }

  const drop = $('drop');
  drop.addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); addFiles(e.dataTransfer.files); });
  $('open-folder').addEventListener('click', () => send({ type: 'rhythm-open-folder' }));
  connectAdmin();
</script>
</body>
</html>
"""
