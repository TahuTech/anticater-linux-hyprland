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
        for scope in (cfg["global"], *cfg["apps"].values()):
            for s in SLOT_ORDER:
                scope.setdefault(s, {"type": "noop", "keys": [], "description": ""})
            for act in scope.values():
                if act.get("type") == "command" and isinstance(act.get("keys"), str):
                    act["keys"] = shlex.split(act["keys"])
                if act.get("type") == "sequence":
                    for st in act.get("steps", []):
                        if st.get("type") == "command" and isinstance(st.get("keys"), str):
                            st["keys"] = shlex.split(st["keys"])
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
  :root { color-scheme: dark;
    --fg: #dcd7ce; --mut: #6e6f74; --bg: #0d0e10; --srf: #151619;
    --elv: #1c1d21; --bd: #2a2b2e; --acc: #c4a86c; --acc2: #5a6e82;
    --ok: #7fa882; --err: #c4746e;
    --key: #7a9ec0; --scroll: #6b9e8a; --mouse: #9b8ab8;
    --cmd: #8a9e78; --seq: #c49560; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 system-ui, -apple-system, sans-serif; }
  header { display:flex; align-items:center; gap:12px; padding:8px 16px;
           border-bottom:1px solid var(--bd); background:var(--srf);
           position:sticky; top:0; z-index:10; }
  header h1 { font-size:13px; margin:0; font-weight:500; letter-spacing:-.01em;
              color:var(--mut); }
  header h1::before { content:"● "; color:var(--acc); }
  .spacer { flex:1; }
  .badge { background:var(--elv); border:1px solid var(--bd); border-radius:4px;
           padding:1px 9px; font-family:ui-monospace,SF Mono,monospace; font-size:11px; }
  .dot { display:inline-block; width:6px; height:6px; border-radius:50%;
         background:var(--err); margin-right:5px; }
  .dot.on { background:var(--ok); }
  button { background:var(--elv); color:var(--fg); border:1px solid var(--bd);
           border-radius:4px; padding:4px 11px; cursor:pointer;
           font-family:system-ui,-apple-system,sans-serif; font-size:12px;
           transition:border-color .12s; }
  button:hover { border-color:var(--acc); }
  button.primary { background:var(--acc); border-color:var(--acc);
                   color:#0d0e10; font-weight:600; }
  button.on { border-color:var(--acc); color:var(--acc); background:var(--elv); }
  #editor { grid-column:2; grid-row:2; padding:0; overflow-y:auto;
            display:flex; justify-content:center; }
  #editor-inner { width:100%; max-width:640px; padding:20px; }
  #practicebar { grid-column:2; grid-row:1; margin:10px 16px 0; padding:7px 14px;
                 border:1px solid var(--acc); border-radius:4px; background:var(--elv);
                 color:var(--fg); font-size:12px; display:flex; gap:8px; align-items:center; }
  #practicebar b { color:var(--acc); }
  button:disabled { opacity:.4; cursor:default; }
  main { display:grid; grid-template-columns: 240px 1fr; gap:0; min-height:calc(100vh - 43px); }
  aside { border-right:1px solid var(--bd); padding:10px 12px; background:var(--srf); overflow-y:auto; }
  aside h2 { font-size:10px; text-transform:uppercase; letter-spacing:.1em;
             color:var(--mut); margin:16px 0 6px; font-weight:500; }
  .app { display:flex; align-items:center; justify-content:space-between; width:100%;
         background:none; border:1px solid transparent; border-radius:4px;
         padding:5px 8px; margin:1px 0; text-align:left;
         font-family:system-ui,-apple-system,sans-serif; font-size:12px;
         transition:border-color .1s; }
  .app:hover { border-color:var(--bd); }
  .app.active { border-color:var(--acc); background:#1a1b1d; }
  .app .x { color:var(--mut); margin-left:6px; font-size:10px; font-family:ui-monospace,monospace; }
  .app .x:hover { color:var(--err); }
  .addrow { display:flex; gap:5px; margin-top:8px; }
  .addrow input { flex:1; background:var(--elv); border:1px solid var(--bd); color:var(--fg);
                  border-radius:4px; padding:4px 7px; font-family:ui-monospace,monospace; font-size:11px; }
  section { padding:0; overflow-y:auto; }
  .slot { border:1px solid var(--bd); border-radius:4px; padding:14px 16px;
          margin-bottom:10px; background:var(--srf);
          transition:border-color .12s; }
  .slot h3 { margin:0 0 10px; font-size:12px; font-weight:500; color:var(--mut);
             text-transform:uppercase; letter-spacing:.06em; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .row label { color:var(--mut); font-size:11px; min-width:80px; }
  select, input[type=text] { background:var(--elv); border:1px solid var(--bd); color:var(--fg);
          border-radius:4px; padding:4px 7px; font-family:ui-monospace,SF Mono,monospace;
          font-size:11px; }
  select:focus, input[type=text]:focus { outline:none; border-color:var(--acc); }
  input[type=text].keys { min-width:260px; flex:1; }
  .hint { color:var(--mut); font-size:10px; margin-top:4px; }
  .knobwrap { display:flex; justify-content:center; margin-bottom:20px; }
  .knobwrap svg { width:100%; max-width:320px; height:auto; }
  .zone { cursor:pointer; }
  .zone:hover rect, .zone:hover circle, .zone:hover path { stroke-width:2; }
  .zone.sel rect, .zone.sel circle, .zone.sel path, .zone.sel polygon { stroke:var(--acc); stroke-width:2; }
  .zone.sel { filter:none; }
  .zone.sel text { fill:var(--acc); }
  .chips { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
  .chip { background:var(--elv); border:1px solid var(--bd); border-radius:3px;
          padding:2px 7px; font-family:ui-monospace,SF Mono,monospace;
          font-size:11px; color:var(--fg); }
  .slot { transition:border-color .12s; }
  .slot.sel { border-color:var(--acc); }
  #editor-inner h2 { font-size:14px; font-weight:500; margin:0 0 16px; color:var(--fg); }
  .record { background:var(--elv); border:1px solid var(--bd); border-radius:3px; padding:3px 9px;
            color:var(--mut); font-size:11px; cursor:pointer; white-space:nowrap; }
  .record:hover { border-color:var(--acc); color:var(--fg); }
  .record.armed { border-color:var(--acc); color:var(--acc); }
  .steps { display:flex; flex-direction:column; gap:5px; margin:6px 0 8px; }
  .steps .row label { min-width:0; }
  .record.dx { color:var(--err); padding:2px 8px; font-size:10px; }
  .record.add { align-self:flex-start; border-style:dashed; color:var(--mut); }
  .record.add:hover { color:var(--acc); }
  #savebar { position:sticky; bottom:0; display:flex; gap:10px; align-items:center;
             padding:8px 20px; background:linear-gradient(transparent, var(--bg) 35%);
             border-top:1px solid var(--bd); }
  #savebar .msg { color:var(--err); font-size:11px; }
  #logbox { position:fixed; right:0; bottom:0; width:400px; max-height:40vh;
            background:var(--srf); border:1px solid var(--bd); border-radius:4px 0 0 0;
            display:flex; flex-direction:column; }
  #logbox summary { cursor:pointer; padding:5px 12px; color:var(--mut); font-size:11px; }
  #loglines { overflow-y:auto; padding:0 12px 8px; margin:0;
              font:10px/1.4 ui-monospace,SF Mono,monospace;
              white-space:pre-wrap; word-break:break-all; color:var(--mut); }
  details#logbox:not([open]) #loglines { display:none; }
</style>
</head>
<body>
<header>
  <h1>ANTICATER — kontrol knob</h1>
  <span class="badge">fokus: <b id="focus">…</b></span>
  <span class="badge"><span class="dot" id="dot"></span><span id="dstate">daemon mati</span></span>
  <span class="spacer"></span>
  <button id="practicebtn" onclick="togglePractice()" title="Klik zona untuk melihat aksi yang akan dijalankan — tanpa fokus kartu">🛠️ Mode latihan</button>
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
  <div id="practicebar" hidden>🛠️ <b id="pprofile">global</b> — gestur <b id="pgesture">…</b> akan menjalankan <b id="paction">…</b></div>
  <section id="editor"><div id="editor-inner"></div></section>
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
const TYPES = ["key","shortcut","mouse","scroll","command","sequence","noop"];
const TYPE_COLORS = {key:"#7a9ec0", shortcut:"#7a9ec0", scroll:"#6b9e8a",
  mouse:"#9b8ab8", command:"#8a9e78", sequence:"#c49560", noop:"#3a3c40"};
let CFG = null, SEL = "__global__", DIRTY = false;

function esc(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",
  '"':"&quot;","'":"&#39;"}[c])); }

function actSum(a){
  if(!a || a.type === "noop") return "noop";
  if(a.type === "sequence")
    return "sequence " + ((a.steps||[]).length) + " langkah";
  return a.type + ((a.keys && a.keys.length) ? " " + a.keys.join(" + ") : "");
}

function markDirty(){ if(!DIRTY){ DIRTY=true; $("savebtn").disabled=false; } }

function renderKnob(scope){
  const G = (slot, path, label) => {
    const a = scope[slot] || {type:"noop"};
    const on = a.type !== "noop";
    const col = TYPE_COLORS[a.type] || TYPE_COLORS.noop;
    const sc = on ? col : "var(--bd)";
    const sw = on ? "1.5" : "1";
    return '<g class="zone" data-slot="'+slot+'" onclick="focusSlot(\\''+slot+'\\')">'+
      '<title>'+esc(SLOT_NAMES[slot]+": "+actSum(a))+'</title>'+
      path(sc, sw)+label(sc, on)+'</g>';
  };
  let p = '<div class="knobwrap"><svg viewBox="0 0 320 240">';
  // Subtle dot grid
  p += '<defs><pattern id="g" width="20" height="20" patternUnits="userSpaceOnUse">'+
    '<circle cx="10" cy="10" r=".4" fill="var(--bd)" opacity=".35"/></pattern></defs>';
  p += '<rect width="320" height="240" fill="url(#g)"/>';
  // Knob body — concentric rings
  p += '<circle cx="160" cy="142" r="66" fill="none" stroke="var(--bd)" stroke-width=".8"/>';
  p += '<circle cx="160" cy="142" r="50" fill="none" stroke="var(--bd)" stroke-width=".5" stroke-dasharray="3 4"/>';
  p += '<circle cx="160" cy="142" r="34" fill="none" stroke="var(--bd)" stroke-width=".8" opacity=".5"/>';
  p += '<circle cx="160" cy="142" r="10" fill="none" stroke="var(--bd)" stroke-width="1.5"/>';
  // Crosshair
  p += '<line x1="90" y1="142" x2="230" y2="142" stroke="var(--bd)" stroke-width=".4" stroke-dasharray="8 6"/>';
  p += '<line x1="160" y1="72" x2="160" y2="212" stroke="var(--bd)" stroke-width=".4" stroke-dasharray="8 6"/>';
  // Tick marks at 45° on outer ring
  for(let a=45;a<360;a+=90){
    const ra = a*Math.PI/180;
    p += '<line x1="'+(160+60*Math.cos(ra)).toFixed(1)+'" y1="'+(142-60*Math.sin(ra)).toFixed(1)+
      '" x2="'+(160+66*Math.cos(ra)).toFixed(1)+'" y2="'+(142-66*Math.sin(ra)).toFixed(1)+
      '" stroke="var(--bd)" stroke-width=".6"/>';
  }
  // Measurement annotation
  p += '<text x="227" y="146" font-size="8" font-family="system-ui,sans-serif" fill="var(--mut)">ø55mm</text>';

  // Zone 1: rotate_left — arc indicator left side
  p += G("rotate_left",
    (sc,sw)=>'<path d="M94,142 A66,66 0 0,1 110,98" fill="none" stroke="'+sc+'" stroke-width="'+sw+
      '"/><polygon points="107,95 114,90 113,99" fill="'+sc+'"/>'+
      '<rect x="30" y="108" width="54" height="32" rx="3" fill="'+sc+'" fill-opacity=".08" stroke="'+sc+'" stroke-width="'+sw+'" stroke-dasharray="4 3"/>',
    (sc,on)=>'<text x="57" y="130" text-anchor="middle" font-size="10" font-family="system-ui,sans-serif" fill="'+(on?sc:'var(--mut)')+'">rotasi kiri</text>');
  // Zone 2: rotate_right
  p += G("rotate_right",
    (sc,sw)=>'<path d="M226,142 A66,66 0 0,1 210,98" fill="none" stroke="'+sc+'" stroke-width="'+sw+
      '"/><polygon points="213,95 206,90 207,99" fill="'+sc+'"/>'+
      '<rect x="236" y="108" width="54" height="32" rx="3" fill="'+sc+'" fill-opacity=".08" stroke="'+sc+'" stroke-width="'+sw+'" stroke-dasharray="4 3"/>',
    (sc,on)=>'<text x="263" y="130" text-anchor="middle" font-size="10" font-family="system-ui,sans-serif" fill="'+(on?sc:'var(--mut)')+'">rotasi kanan</text>');
  // Zone 3: press (center)
  p += G("press",
    (sc,sw)=>'<circle cx="160" cy="142" r="32" fill="'+sc+'" fill-opacity=".08" stroke="'+sc+'" stroke-width="'+sw+'"/>'+
      '<circle cx="160" cy="142" r="8" fill="'+sc+'" fill-opacity="'+(sw==="1.5"?"1":".25")+'"/>',
    (sc,on)=>'');
  // Zone 4: press_rotate_left
  p += G("press_rotate_left",
    (sc,sw)=>'<path d="M106,142 A54,54 0 0,1 118,108" fill="none" stroke="'+sc+'" stroke-width="'+sw+
      '" stroke-dasharray="2 3"/>'+
      '<rect x="10" y="52" width="64" height="30" rx="3" fill="'+sc+'" fill-opacity=".08" stroke="'+sc+'" stroke-width="'+sw+'" stroke-dasharray="4 3"/>',
    (sc,on)=>'<text x="42" y="66" text-anchor="middle" font-size="9" font-family="system-ui,sans-serif" fill="'+(on?sc:'var(--mut)')+'">tahan+putar</text>'+
      '<text x="42" y="77" text-anchor="middle" font-size="9" font-family="system-ui,sans-serif" fill="var(--mut)">kiri</text>');
  // Zone 5: press_rotate_right
  p += G("press_rotate_right",
    (sc,sw)=>'<path d="M214,142 A54,54 0 0,0 202,108" fill="none" stroke="'+sc+'" stroke-width="'+sw+
      '" stroke-dasharray="2 3"/>'+
      '<rect x="246" y="52" width="64" height="30" rx="3" fill="'+sc+'" fill-opacity=".08" stroke="'+sc+'" stroke-width="'+sw+'" stroke-dasharray="4 3"/>',
    (sc,on)=>'<text x="278" y="66" text-anchor="middle" font-size="9" font-family="system-ui,sans-serif" fill="'+(on?sc:'var(--mut)')+'">tahan+putar</text>'+
      '<text x="278" y="77" text-anchor="middle" font-size="9" font-family="system-ui,sans-serif" fill="var(--mut)">kanan</text>');
  // "tekan" label below press zone
  p += '<text x="160" y="188" text-anchor="middle" font-size="9" font-family="system-ui,sans-serif" fill="var(--mut)">tekan</text>';
  // ID mark
  p += '<text x="75" y="224" font-size="8" font-family="ui-monospace,monospace" fill="var(--bd)" opacity=".5">ANTICATER VK-01</text>';
  // Ruler marks at bottom
  p += '<line x1="150" y1="216" x2="245" y2="216" stroke="var(--bd)" stroke-width=".5"/>';
  for(let i=0;i<=10;i++){
    const h = i%5===0?5:(i%2===0?3:2);
    p += '<line x1="'+(150+i*9.5)+'" y1="216" x2="'+(150+i*9.5)+'" y2="'+(216+h)+'" stroke="var(--bd)" stroke-width=".4"/>';
  }
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
  if(PRACTICE){ practicePreview(slot); return; }
  el.scrollIntoView({behavior:"smooth", block:"center"});
  const i = el.querySelector("input.keys"); if(i) i.focus();
}
let rec = null;
let PRACTICE = false;
function togglePractice(){
  PRACTICE = !PRACTICE;
  $("practicebtn").classList.toggle("on", PRACTICE);
  $("practicebar").hidden = !PRACTICE;
}
function practicePreview(slot){
  const scope = SEL==="__global__" ? CFG.global : (CFG.apps[SEL]||{});
  const act = scope[slot] || {type:"noop"};
  $("pprofile").textContent = SEL==="__global__" ? "global" : SEL;
  $("pgesture").textContent = SLOT_NAMES[slot];
  $("paction").textContent = act.type==="noop" ? "— (tidak ada aksi)" : actSum(act);
}
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
  const ed = $("editor-inner"); ed.innerHTML = "";
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

    if(act.type === "sequence"){
      const STYPES = ["key","shortcut","mouse","scroll","command"];
      const stepsBox = document.createElement("div"); stepsBox.className = "steps";
      const renderSteps = () => {
        stepsBox.innerHTML = "";
        scope[slot].steps = scope[slot].steps || [{type:"key", keys:[]}];
        scope[slot].steps.forEach((st, i) => {
          const sr = document.createElement("div"); sr.className = "row step";
          const ts = document.createElement("select");
          STYPES.forEach(t => { const o = document.createElement("option"); o.value=t;
            o.textContent=t; if(t===st.type) o.selected=true; ts.appendChild(o); });
          const ip = document.createElement("input"); ip.type="text"; ip.className="keys";
          const refresh = () => {
            ip.placeholder = ts.value==="command" ? "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+" :
              ts.value==="mouse" ? "left|right|middle" : ts.value==="scroll" ? "up|down|left|right" :
              "control shift tab";
            ip.value = Array.isArray(st.keys) ? st.keys.join(" ") : String(st.keys||"");
          };
          ts.onchange = () => { st.type = ts.value;
            st.keys = ts.value==="command" ? "" : []; refresh(); markDirty(); };
          ip.oninput = () => { st.keys = ts.value==="command" ? ip.value :
            ip.value.split(/\\s+/).filter(Boolean); refresh(); updateChips(); markDirty(); };
          const dx = document.createElement("button"); dx.type="button"; dx.className="record dx";
          dx.textContent = "✕";
          dx.onclick = () => { scope[slot].steps.splice(i,1);
            if(!scope[slot].steps.length) scope[slot].steps.push({type:"key", keys:[]});
            renderSteps(); updateChips(); markDirty(); };
          sr.append(ts, ip, dx); refresh(); stepsBox.appendChild(sr);
        });
        const add = document.createElement("button"); add.type="button"; add.className="record add";
        add.textContent = "+ langkah";
        add.onclick = () => { scope[slot].steps.push({type:"key", keys:[]});
          renderSteps(); markDirty(); };
        stepsBox.appendChild(add);
      };
      renderSteps();
      card.appendChild(stepsBox);
    } else {
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
    }
    const chips = document.createElement("div"); chips.className = "chips"; card.appendChild(chips);
    const updateChips = () => {
      chips.innerHTML = "";
      if(scope[slot] && scope[slot].type === "sequence"){
        (scope[slot].steps || []).forEach(st => {
          const c = document.createElement("span"); c.className = "chip";
          const ks = Array.isArray(st.keys) ? st.keys.join(" ") : String(st.keys||"");
          c.textContent = (st.type==="key" || st.type==="shortcut") ? ks :
            st.type + (ks ? " "+ks : "");
          chips.appendChild(c);
        });
        return;
      }
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