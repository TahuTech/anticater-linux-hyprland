#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anticater-gui — editor app-mapping.json + kontrol daemon, antarmuka web lokal.

  python3 gui/anticater-gui.py [--port 8099] [--config PATH] [--open]

Server HTTP 127.0.0.1:<port>; buka http://127.0.0.1:<port> di browser.
Tanpa dependensi tambahan (stdlib); validasi kunci dipinjam dari anticaterd.
Simpan config atomik; daemon yang dihidupkan GUI ini ikut dimatikan saat GUI tutup.
"""

import argparse
import collections
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import http.server

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "daemon"))
from anticaterd import check_config, default_config_path  # noqa: E402

DAEMON = REPO / "daemon" / "anticaterd.py"

SLOT_LABELS = {
    "rotate_left": "Rotasi kiri",
    "rotate_right": "Rotasi kanan",
    "press": "Tekan",
    "press_rotate_left": "Tekan + rotasi kiri",
    "press_rotate_right": "Tekan + rotasi kanan",
}
SLOT_ORDER = list(SLOT_LABELS)

TEMPLATES = {
    "browser": {
        "rotate_left": {"type": "shortcut", "description": "Tab sebelumnya",
                        "keys": ["control", "shift", "tab"]},
        "rotate_right": {"type": "shortcut", "description": "Tab berikutnya",
                         "keys": ["control", "tab"]},
        "press": {"type": "shortcut", "description": "Muat ulang",
                  "keys": ["control", "r"]},
        "press_rotate_left": {"type": "shortcut", "description": "Riwayat mundur",
                              "keys": ["control", "left"]},
        "press_rotate_right": {"type": "shortcut", "description": "Riwayat maju",
                               "keys": ["control", "right"]},
    },
    "code": {
        "rotate_left": {"type": "shortcut", "description": "Editor sebelumnya",
                        "keys": ["control", "pageup"]},
        "rotate_right": {"type": "shortcut", "description": "Editor berikutnya",
                         "keys": ["control", "pagedown"]},
        "press": {"type": "shortcut", "description": "Palet perintah",
                  "keys": ["control", "shift", "p"]},
        "press_rotate_left": {"type": "shortcut", "description": "Masalah sebelumnya",
                              "keys": ["f8"]},
        "press_rotate_right": {"type": "shortcut", "description": "Masalah berikutnya",
                               "keys": ["shift", "f8"]},
    },
    "media": {
        "rotate_left": {"type": "key", "description": "Kecilkan volume",
                        "keys": ["volume_down"]},
        "rotate_right": {"type": "key", "description": "Besarkan volume",
                         "keys": ["volume_up"]},
        "press": {"type": "key", "description": "Play / pause",
                  "keys": ["play_pause"]},
        "press_rotate_left": {"type": "key", "description": "Lagu sebelumnya",
                              "keys": ["previous_track"]},
        "press_rotate_right": {"type": "key", "description": "Lagu berikutnya",
                               "keys": ["next_track"]},
    },
    "desain": {
        "rotate_left": {"type": "key", "description": "Kuas mengecil",
                        "keys": ["bracketleft"]},
        "rotate_right": {"type": "key", "description": "Kuas membesar",
                         "keys": ["bracketright"]},
        "press": {"type": "key", "description": "Alat kuas", "keys": ["b"]},
        "press_rotate_left": {"type": "shortcut", "description": "Kekerasan kuas turun",
                              "keys": ["shift", "bracketleft"]},
        "press_rotate_right": {"type": "shortcut", "description": "Kekerasan kuas naik",
                               "keys": ["shift", "bracketright"]},
    },
    "kliping": {
        "rotate_left": {"type": "key", "description": "Mundur 1 frame",
                        "keys": ["left"]},
        "rotate_right": {"type": "key", "description": "Maju 1 frame",
                         "keys": ["right"]},
        "press": {"type": "key", "description": "Play / pause", "keys": ["space"]},
        "press_rotate_left": {"type": "shortcut", "description": "Mundur 5 frame",
                              "keys": ["shift", "left"]},
        "press_rotate_right": {"type": "shortcut", "description": "Maju 5 frame",
                               "keys": ["shift", "right"]},
    },
}


def empty_slots():
    return {s: {"type": "noop", "keys": [], "description": ""} for s in SLOT_ORDER}


def hyprctl_json(*args):
    try:
        p = subprocess.run(["hyprctl", "-j", *args], capture_output=True,
                           text=True, timeout=3)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


class Gui:
    def __init__(self, cfg_path):
        self.cfg_path = Path(cfg_path)
        with open(self.cfg_path, encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.cfg.setdefault("global", {})
        self.cfg.setdefault("apps", {})
        self.lock = threading.RLock()
        self.proc = None
        self.ring = collections.deque(maxlen=300)

    # ---- log ----
    def note(self, msg):
        with self.lock:
            self.ring.append(msg)

    def log_lines(self):
        with self.lock:
            return list(self.ring)

    # ---- info fokus ----
    def focus(self):
        j = hyprctl_json("activewindow")
        if not j:
            return None
        cls = j.get("class")
        return cls if cls and cls != "invalid" else None

    def windows(self):
        j = hyprctl_json("clients")
        if not isinstance(j, list):
            return []
        seen, out = set(), []
        for w in j:
            c = w.get("class")
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return sorted(out)

    # ---- config ----
    def normalize(self, cfg):
        cfg.setdefault("global", {})
        cfg.setdefault("apps", {})
        for scope in (cfg["global"],):
            for s in SLOT_ORDER:
                scope.setdefault(s, {"type": "noop", "keys": [], "description": ""})
        for app in cfg["apps"].values():
            for s in SLOT_ORDER:
                app.setdefault(s, {"type": "noop", "keys": [], "description": ""})
            for act in app.values():
                if act.get("type") == "command" and isinstance(act.get("keys"), str):
                    act["keys"] = shlex.split(act["keys"])
        return cfg

    def save(self, cfg):
        cfg = self.normalize(cfg)
        errs = check_config(cfg)
        if errs:
            return errs
        tmp = self.cfg_path.with_name(self.cfg_path.name + ".tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.cfg_path)
        with self.lock:
            self.cfg = cfg
        self.note("config disimpan: %s" % self.cfg_path)
        return None

    # ---- daemon ----
    def daemon_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start_daemon(self):
        if self.daemon_running():
            return False, "daemon sudah jalan"
        self.proc = subprocess.Popen(
            [sys.executable, str(DAEMON), "--config", str(self.cfg_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._pump, daemon=True).start()
        self.note("daemon dimulai (pid %d)" % self.proc.pid)
        return True, "daemon dimulai (pid %d)" % self.proc.pid

    def _pump(self):
        for line in self.proc.stdout:
            self.note(line.rstrip())

    def stop_daemon(self):
        if not self.daemon_running():
            return False, "daemon tidak berjalan"
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.note("daemon dihentikan")
        return True, "daemon dihentikan"


class Handler(http.server.BaseHTTPRequestHandler):
    gui = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj, ctype="application/json; charset=utf-8"):
        body = json.dumps(obj).encode() if not isinstance(obj, (bytes, str)) else \
            obj.encode() if isinstance(obj, str) else obj
        self.send_response(code)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode("utf-8") if n else ""

    def _json(self, code, obj):
        return self._send(code, obj)

    # ---- GET ----
    def do_GET(self):
        g = self.gui
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/state":
            with g.lock:
                cfg = json.loads(json.dumps(g.cfg))
            return self._json(200, {
                "config": cfg, "focus": g.focus(), "windows": g.windows(),
                "daemon_running": g.daemon_running(), "log": g.log_lines(),
                "cfg_path": str(g.cfg_path),
            })
        if path == "/api/live":
            return self._json(200, {
                "focus": g.focus(), "windows": g.windows(),
                "daemon_running": g.daemon_running(), "log": g.log_lines(),
            })
        return self._json(404, {"error": "tak dikenal"})

    # ---- POST ----
    def do_POST(self):
        g = self.gui
        path = urllib.parse.urlparse(self.path).path
        try:
            req = json.loads(self._body() or "{}")
        except ValueError:
            return self._json(400, {"error": "JSON rusak"})
        if path == "/api/save":
            cfg = req.get("config")
            if not isinstance(cfg, dict):
                return self._json(400, {"error": "config harus objek"})
            errs = g.save(cfg)
            if errs:
                return self._json(400, {"errors": errs})
            return self._json(200, {"ok": True})
        if path == "/api/app":
            cls = (req.get("class") or "").strip()
            action = req.get("action")
            if not cls:
                return self._json(400, {"error": "butuh class"})
            with g.lock:
                if action == "add":
                    g.cfg.setdefault("apps", {})[cls] = empty_slots()
                    g.note("app ditambahkan: %s" % cls)
                elif action == "remove":
                    g.cfg.get("apps", {}).pop(cls, None)
                    g.note("app dihapus: %s" % cls)
                else:
                    return self._json(400, {"error": "aksi tak dikenal"})
            return self._json(200, {"ok": True})
        if path == "/api/template":
            name = req.get("name")
            app = req.get("app") or "__global__"
            if name not in TEMPLATES:
                return self._json(400, {"error": "template tak dikenal"})
            with g.lock:
                if app == "__global__":
                    target = g.cfg["global"]
                    note = "template %s -> global" % name
                else:
                    g.cfg.setdefault("apps", {})[app] = empty_slots()
                    target = g.cfg["apps"][app]
                    note = "template %s -> %s" % (name, app)
                target.update({k: dict(v) for k, v in TEMPLATES[name].items()})
                g.note(note)
            return self._json(200, {"ok": True})
        if path == "/api/daemon":
            action = req.get("action")
            if action == "start":
                ok, msg = g.start_daemon()
            elif action == "stop":
                ok, msg = g.stop_daemon()
            else:
                return self._json(400, {"error": "aksi tak dikenal"})
            return self._json(200, {"ok": ok, "message": msg})
        return self._json(404, {"error": "tak dikenal"})


PAGE = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ANTICATER — kontrol knob</title>
<style>
  :root { color-scheme: dark; --fg:#e8e8e8; --mut:#9aa0a6; --bg:#17181c; --srf:#202228;
          --bd:#2f323a; --acc:#7aa2f7; --ok:#9ece6a; --err:#f7768e; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 system-ui, sans-serif; }
  header { display:flex; align-items:center; gap:12px; padding:10px 16px;
           border-bottom:1px solid var(--bd); background:var(--srf); position:sticky; top:0; }
  header h1 { font-size:15px; margin:0; font-weight:600; }
  .spacer { flex:1; }
  .badge { background:#14151a; border:1px solid var(--bd); border-radius:6px;
           padding:2px 10px; font-family:ui-monospace,monospace; font-size:12px; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
         background:var(--err); margin-right:6px; }
  .dot.on { background:var(--ok); }
  button { background:#262a33; color:var(--fg); border:1px solid var(--bd);
           border-radius:6px; padding:5px 12px; cursor:pointer; }
  button:hover { border-color:var(--acc); }
  button.primary { background:var(--acc); border-color:var(--acc); color:#0b0c10; font-weight:600; }
  button:disabled { opacity:.45; cursor:default; }
  main { display:grid; grid-template-columns: 270px 1fr; gap:0; min-height:calc(100vh - 49px); }
  aside { border-right:1px solid var(--bd); padding:12px; background:var(--srf); overflow-y:auto; }
  aside h2 { font-size:11px; text-transform:uppercase; letter-spacing:.08em;
             color:var(--mut); margin:14px 0 6px; }
  .app { display:flex; align-items:center; justify-content:space-between; width:100%;
         background:none; border:1px solid transparent; border-radius:6px;
         padding:6px 8px; margin:2px 0; text-align:left; font-family:ui-monospace,monospace; }
  .app:hover { border-color:var(--bd); }
  .app.active { border-color:var(--acc); background:#20232b; }
  .app .x { color:var(--mut); margin-left:8px; font-size:11px; }
  .app .x:hover { color:var(--err); }
  .addrow { display:flex; gap:6px; margin-top:8px; }
  .addrow input { flex:1; background:#14151a; border:1px solid var(--bd); color:var(--fg);
                  border-radius:6px; padding:5px 8px; font-family:ui-monospace,monospace; }
  section { padding:16px 20px; overflow-y:auto; }
  .slot { border:1px solid var(--bd); border-radius:8px; padding:12px 14px;
          margin-bottom:10px; background:var(--srf); }
  .slot h3 { margin:0 0 8px; font-size:13px; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .row label { color:var(--mut); font-size:12px; min-width:90px; }
  select, input[type=text] { background:#14151a; border:1px solid var(--bd); color:var(--fg);
          border-radius:6px; padding:5px 8px; font-family:ui-monospace,monospace; font-size:12px; }
  input[type=text].keys { min-width:280px; flex:1; }
  .hint { color:var(--mut); font-size:11px; margin-top:4px; }
  .knobwrap { display:flex; justify-content:center; margin-bottom:16px; }
  .knobwrap svg { width:100%; max-width:340px; height:auto; }
  .zone { cursor:pointer; transition:filter .15s; }
  .zone:hover rect, .zone:hover circle { stroke-width:2.5; }
  .zone.sel rect, .zone.sel circle { stroke:var(--acc); stroke-width:2.5; }
  .zone.sel { filter:drop-shadow(0 0 6px var(--acc)); }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }
  .chip { background:#14151a; border:1px solid #3a3f4a; border-bottom-width:2px; border-radius:5px;
          padding:2px 8px; font-family:ui-monospace,monospace; font-size:12px; color:#cdd3dc; }
  .slot { transition:box-shadow .2s; }
  .slot.sel { box-shadow:0 0 0 2px var(--acc); }
  .record { background:#14151a; border:1px solid #3a3f4a; border-radius:5px; padding:4px 10px;
            color:var(--mut); font-size:12px; cursor:pointer; white-space:nowrap; }
  .record:hover { border-color:var(--acc); color:var(--fg); }
  .record.armed { border-color:var(--acc); color:var(--acc); box-shadow:0 0 0 1px var(--acc); }
  #savebar { position:sticky; bottom:0; display:flex; gap:10px; align-items:center;
             padding:10px 20px; background:linear-gradient(transparent, var(--bg) 35%);
             border-top:1px solid var(--bd); }
  #savebar .msg { color:var(--err); font-size:12px; }
  #logbox { position:fixed; right:0; bottom:0; width:440px; max-height:40vh;
            background:#0d0e11; border:1px solid var(--bd); border-radius:8px 0 0 0;
            display:flex; flex-direction:column; }
  #logbox summary { cursor:pointer; padding:6px 12px; color:var(--mut); font-size:12px; }
  #loglines { overflow-y:auto; padding:0 12px 10px; margin:0; font:11px/1.45 ui-monospace,monospace;
              white-space:pre-wrap; word-break:break-all; color:#b8c0cc; }
  details#logbox:not([open]) #loglines { display:none; }
</style>
</head>
<body>
<header>
  <h1>ANTICATER — kontrol knob</h1>
  <span class="badge">fokus: <b id="focus">…</b></span>
  <span class="badge"><span class="dot" id="dot"></span><span id="dstate">daemon mati</span></span>
  <span class="spacer"></span>
  <button id="daemonbtn" onclick="toggleDaemon()">Nyalakan daemon</button>
</header>
<main>
  <aside>
    <h2>Profil</h2>
    <button class="app active" id="app-__global__" onclick="select('__global__', this)">🌐 global</button>
    <div id="applist"></div>
    <h2>Tambah app (WM_CLASS)</h2>
    <div class="addrow">
      <input id="newapp" list="wlist" placeholder="mis. brave-browser" onkeydown="if(event.key==='Enter')addApp()">
      <datalist id="wlist"></datalist>
      <button onclick="addApp()">+</button>
    </div>
    <h2>Template</h2>
    <div class="addrow">
      <select id="tpl">
        <option value="browser">Browser</option>
        <option value="code">Menulis kode</option>
        <option value="media">Media</option>
        <option value="desain">Desain</option>
        <option value="kliping">Kliping</option>
      </select>
      <button onclick="applyTpl()">Terapkan</button>
    </div>
  </aside>
  <section id="editor"></section>
</main>
<div id="savebar">
  <button class="primary" id="savebtn" onclick="save()" disabled>Simpan</button>
  <span class="msg" id="msg"></span>
  <span class="spacer" style="flex:1"></span>
  <span class="badge" id="cfgpath"></span>
</div>
<details id="logbox">
  <summary>Log daemon</summary>
  <pre id="loglines"></pre>
</details>
<script>
"use strict";
const $ = id => document.getElementById(id);
const SLOTS = ["rotate_left","rotate_right","press","press_rotate_left","press_rotate_right"];
const SLOT_NAMES = {rotate_left:"Rotasi kiri",rotate_right:"Rotasi kanan",press:"Tekan",
  press_rotate_left:"Tekan + rotasi kiri",press_rotate_right:"Tekan + rotasi kanan"};
const TYPES = ["key","shortcut","mouse","scroll","command","noop"];
const TYPE_COLORS = {key:"#7aa2f7", shortcut:"#7aa2f7", scroll:"#2ac3de",
  mouse:"#bb9af7", command:"#9ece6a", noop:"#3a3d46"};
let CFG = null, SEL = "__global__", DIRTY = false;

function esc(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",
  '"':"&quot;","'":"&#39;"}[c])); }

function markDirty(){ if(!DIRTY){ DIRTY=true; $("savebtn").disabled=false; } }

function renderKnob(scope){
  const Z = [
    {s:"rotate_left",        x:6,   y:114, w:46, h:56, g:"◀", c:"kiri"},
    {s:"rotate_right",       x:268, y:114, w:46, h:56, g:"▶", c:"kanan"},
    {s:"press_rotate_left",  x:74,  y:16,  w:60, h:54, g:"⟲", c:"tahan+putar", c2:"kiri"},
    {s:"press_rotate_right", x:186, y:16,  w:60, h:54, g:"⟳", c:"tahan+putar", c2:"kanan"}
  ];
  let p = '<div class="knobwrap"><svg viewBox="0 0 320 240">' +
    '<circle cx="160" cy="142" r="64" style="fill:#14151a;stroke:var(--bd)"/>';
  for (const z of Z){
    const a = scope[z.s] || {type:"noop"};
    const col = TYPE_COLORS[a.type] || TYPE_COLORS.noop;
    const on = a.type !== "noop";
    const ks = (a.keys||[]).join(" + ");
    const gy = z.c2 ? z.y+24 : z.y+z.h/2+4;
    const cy = z.c2 ? z.y+39 : z.y+z.h-10;
    const c2y = z.c2 ? z.y+51 : 0;
    p += '<g class="zone" data-slot="'+z.s+'" onclick="focusSlot(\\''+z.s+'\\')"><title>' +
      esc(SLOT_NAMES[z.s]+": "+(on ? a.type+(ks ? " "+ks : "") : "noop")) + '</title>' +
      '<rect x="'+z.x+'" y="'+z.y+'" width="'+z.w+'" height="'+z.h+'" rx="12" fill="'+col+
      '" fill-opacity="'+(on?0.25:0.06)+'" stroke="'+col+'" stroke-width="1.5"/>' +
      '<text x="'+(z.x+z.w/2)+'" y="'+gy+'" text-anchor="middle" font-size="17" ' +
      'font-family="ui-monospace,monospace,sans-serif" fill="'+(on?col:"#565b66")+'">'+z.g+'</text>' +
      '<text x="'+(z.x+z.w/2)+'" y="'+cy+'" text-anchor="middle" font-size="9" ' +
      'style="fill:var(--mut)">'+z.c+'</text>' +
      (z.c2 ? '<text x="'+(z.x+z.w/2)+'" y="'+c2y+'" text-anchor="middle" font-size="9" ' +
        'style="fill:var(--mut)">'+z.c2+'</text>' : '') + '</g>';
  }
  const pr = scope["press"] || {type:"noop"};
  const pcol = TYPE_COLORS[pr.type] || TYPE_COLORS.noop;
  const pon = pr.type !== "noop";
  p += '<g class="zone" data-slot="press" onclick="focusSlot(\\'press\\')"><title>' +
    esc(SLOT_NAMES["press"]+": "+(pon ? pr.type+" "+((pr.keys||[]).join(" + ")) : "noop")) + '</title>' +
    '<circle cx="160" cy="142" r="34" fill="'+pcol+'" fill-opacity="'+(pon?0.2:0.05)+'" stroke="'+pcol+'" stroke-width="1.5"/>' +
    '<circle cx="160" cy="142" r="10" fill="'+(pon?pcol:"#565b66")+'"/>' +
    '<text x="160" y="188" text-anchor="middle" font-size="9" style="fill:var(--mut)">tekan</text></g>';
  return p + '</svg></div>';
}

let selSlot = "rotate_left";
function markSel(slot){
  selSlot = slot;
  document.querySelectorAll(".zone").forEach(z => z.classList.toggle("sel", z.dataset.slot === slot));
  document.querySelectorAll(".slot").forEach(c => c.classList.toggle("sel", c.id === "slot-"+slot));
}
function focusSlot(slot){
  const el = document.getElementById("slot-"+slot);
  if(!el) return;
  markSel(slot);
  el.scrollIntoView({behavior:"smooth", block:"center"});
  const i = el.querySelector("input.keys"); if(i) i.focus();
}
let rec = null;
const RECKEY = { " ":"space", Tab:"tab", Enter:"return", Backspace:"backspace", Delete:"delete",
  ArrowUp:"up", ArrowDown:"down", ArrowLeft:"left", ArrowRight:"right",
  Home:"home", End:"end", PageUp:"pageup", PageDown:"pagedown", Insert:"insert",
  AudioVolumeUp:"volume_up", AudioVolumeDown:"volume_down", AudioVolumeMute:"mute",
  MediaPlayPause:"play_pause", MediaTrackNext:"next_track", MediaTrackPrevious:"previous_track",
  MediaStop:"stop", ",":"comma", ".":"period", "/":"slash", ";":"semicolon", "'":"quote",
  "`":"backquote", "-":"minus", "=":"equal", "[":"bracketleft", "]":"bracketright" };
function recStop(){
  if(rec){ rec.btn.textContent = "⌨ Rekam"; rec.btn.classList.remove("armed"); rec = null; }
}
document.addEventListener("keydown", e => {
  if(!rec) return;
  e.preventDefault(); e.stopPropagation();
  if(e.repeat) return;
  if(e.key === "Escape"){ recStop(); return; }
  let name = RECKEY[e.key];
  if(!name && e.key.length===1 && /^[a-z0-9]$/i.test(e.key)) name = e.key.toLowerCase();
  if(!name && /^F([1-9]|1[0-2])$/i.test(e.key)) name = e.key.toLowerCase();
  if(!name) return;
  const ks = [];
  if(e.ctrlKey) ks.push("control");
  if(e.shiftKey) ks.push("shift");
  if(e.altKey) ks.push("alt");
  if(e.metaKey) ks.push("super");
  ks.push(name);
  const {slot, inp, upd, scope} = rec;
  scope[slot] = scope[slot] || {};
  scope[slot].keys = ks;
  inp.value = ks.join(" ");
  upd(); markDirty();
  recStop();
  inp.focus();
});

function select(app, btn){
  SEL = app;
  document.querySelectorAll(".app").forEach(b => b.classList.toggle("active", b.id === "app-"+app));
  renderEditor();
}

function renderApps(){
  const list = $("applist"); list.innerHTML = "";
  const add = (c, active) => {
    const b = document.createElement("button");
    b.className = "app" + (active ? " active" : "");
    b.id = "app-"+c; b.onclick = () => select(c, b);
    const span = document.createElement("span"); span.textContent = c;
    const x = document.createElement("span"); x.className = "x"; x.textContent = "✕";
    x.title = "hapus"; x.onclick = e => { e.stopPropagation(); removeApp(c); };
    b.append(span, x); list.appendChild(b);
  };
  Object.keys(CFG.apps||{}).sort().forEach(c => add(c, c===SEL));
  $("app-__global__").classList.toggle("active", SEL==="__global__");
}

function renderEditor(){
  const ed = $("editor"); ed.innerHTML = "";
  const scope = SEL==="__global__" ? CFG.global : (CFG.apps[SEL] || {});
  const h = document.createElement("h2");
  h.textContent = SEL==="__global__" ? "Global (semua app lain)" : "Profil: "+SEL;
  ed.appendChild(h); ed.insertAdjacentHTML("beforeend", renderKnob(scope));
  for (const slot of SLOTS){
    const act = scope[slot] || {type:"noop", keys:[], description:""};
    const card = document.createElement("div"); card.className = "slot"; card.id = "slot-"+slot;
    const h3 = document.createElement("h3"); h3.textContent = SLOT_NAMES[slot]; card.appendChild(h3);

    const r1 = document.createElement("div"); r1.className = "row";
    const lb = document.createElement("label"); lb.textContent = "Aksi";
    const sel = document.createElement("select");
    TYPES.forEach(t => { const o = document.createElement("option"); o.value=t;
      o.textContent=t; if(t===act.type) o.selected=true; sel.appendChild(o); });
    sel.onchange = () => { scope[slot] = scope[slot] || {}; scope[slot].type = sel.value;
      scope[slot].keys = []; renderEditor(); markDirty(); };
    r1.append(lb, sel); card.appendChild(r1);

    const r2 = document.createElement("div"); r2.className = "row";
    const lb2 = document.createElement("label"); lb2.textContent =
      act.type==="command" ? "Perintah (argv)" : act.type==="mouse" ? "Tombol" :
      act.type==="scroll" ? "Arah" : "Kunci";
    const inp = document.createElement("input"); inp.type="text"; inp.className="keys";
    inp.placeholder = act.type==="command" ? "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+" :
      act.type==="mouse" ? "left|right|middle" : act.type==="scroll" ? "up|down|left|right" :
      "control shift tab"; inp.value = (act.keys||[]).join(" ");
    inp.oninput = () => { scope[slot] = scope[slot] || {};
      const v = inp.value.split(/\\s+/).filter(Boolean);
      scope[slot].keys = act.type==="command" ? inp.value : v; updateChips(); markDirty(); };
    r2.append(lb2, inp);
    if(act.type !== "command" && act.type !== "mouse"){
      const rb = document.createElement("button"); rb.type="button"; rb.className="record";
      rb.textContent = "⌨ Rekam";
      rb.onclick = () => {
        if(rec && rec.btn !== rb) recStop();
        rec = {btn: rb, slot: slot, inp: inp, scope: scope, upd: updateChips};
        rb.classList.add("armed"); rb.textContent = "Rekam… Esc batal";
        inp.focus();
      };
      r2.append(rb);
    }
    card.appendChild(r2);
    const chips = document.createElement("div"); chips.className = "chips"; card.appendChild(chips);
    const updateChips = () => {
      chips.innerHTML = "";
      let ks = (scope[slot] && scope[slot].keys) || [];
      if(!Array.isArray(ks)) ks = String(ks).trim() ? ks.split(/\\s+/) : [];
      (ks||[]).forEach(k => {
        const c = document.createElement("span"); c.className = "chip"; c.textContent = k;
        chips.appendChild(c);
      });
    };
    updateChips();

    const r3 = document.createElement("div"); r3.className = "row";
    const lb3 = document.createElement("label"); lb3.textContent = "Catatan";
    const d = document.createElement("input"); d.type="text"; d.className="keys";
    d.placeholder = "keterangan opsional"; d.value = act.description || "";
    d.oninput = () => { scope[slot] = scope[slot] || {}; scope[slot].description = d.value; markDirty(); };
    r3.append(lb3, d); card.appendChild(r3);
    card.addEventListener("focusin", () => markSel(slot));
    ed.appendChild(card);
  }
  if(selSlot) markSel(selSlot);
}

function addApp(){
  const c = $("newapp").value.trim(); if(!c) return;
  CFG.apps[c] = CFG.apps[c] || {}; SLOTS.forEach(s => CFG.apps[c][s] = CFG.apps[c][s] || {type:"noop",keys:[],description:""});
  $("newapp").value = ""; renderApps(); select(c); markDirty();
}
function removeApp(c){ delete CFG.apps[c]; renderApps(); if(SEL===c) select("__global__"); markDirty(); }

function applyTpl(){
  const name = $("tpl").value;
  const scope = SEL==="__global__" ? CFG.global : (CFG.apps[SEL]=CFG.apps[SEL]||{});
  fetch("/api/template",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name, app:SEL})}).then(r=>r.json()).then(j=>{
    if(j.ok){ load(); markDirty(); msg("Template diterapkan ke "+(SEL==="__global__"?"global":SEL), false); }
    else msg(j.error, true); });
}

function save(){
  fetch("/api/save",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({config:CFG})}).then(r=>r.json().then(j=>({ok:r.ok,j}))).then(({ok,j})=>{
    if(ok){ DIRTY=false; $("savebtn").disabled=true; msg("Tersimpan ✔", false); }
    else msg((j.errors||[j.error]).join(" · "), true);
  });
}

function toggleDaemon(){
  const start = !window.DAEMON_RUNNING;
  fetch("/api/daemon",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action:start?"start":"stop"})}).then(r=>r.json()).then(j=>{
    if(j.ok) msg(j.message, false); else msg(j.message||"gagal", true);
    poll(true);
  });
}

function msg(t, err){ $("msg").textContent = t; $("msg").style.color = err ? "var(--err)" : "var(--ok)"; }

function load(state){
  CFG = state.config;
  $("cfgpath").textContent = state.cfg_path;
  renderApps(); renderEditor();
}

function poll(force){
  fetch("/api/live").then(r=>r.json()).then(s=>{
    const f = $("focus"); f.textContent = s.focus || "(tidak ada)";
    window.DAEMON_RUNNING = s.daemon_running;
    $("dot").classList.toggle("on", s.daemon_running);
    $("dstate").textContent = s.daemon_running ? "daemon jalan" : "daemon mati";
    $("daemonbtn").textContent = s.daemon_running ? "Matikan daemon" : "Nyalakan daemon";
    $("loglines").textContent = (s.log||[]).join("\\n");
    const wl = $("wlist"); wl.innerHTML = "";
    (s.windows||[]).forEach(c => { const o=document.createElement("option"); o.value=c; wl.appendChild(o); });
    if(force) setTimeout(renderEditor, 0);
  }).catch(()=>{}).finally(()=> { if(!force) setTimeout(poll, 1500); });
}

fetch("/api/state").then(r=>r.json()).then(s=>{ load(s); poll(); }).catch(e=>{
  document.body.innerHTML = "<section><h2>Server tak merespons</h2><p>"+esc(e)+"</p></section>";
});
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="path app-mapping.json")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--open", action="store_true",
                    help="buka browser otomatis (xdg-open)")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config) if args.config else default_config_path()
    gui = Gui(cfg_path)
    Handler.gui = gui
    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/" % (args.host, args.port)
    print("anticater-gui: %s  (config: %s)" % (url, cfg_path))
    if args.open:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gui.stop_daemon()
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())