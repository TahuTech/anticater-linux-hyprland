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
from anticaterd import (KEY_ALIASES, KEY_BY_NAME, check_config,  # noqa: E402
                        default_config_path, keycode, required_keycodes)

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


def key_table():
    """Nama tombol yang diterima validator daemon -> [keycode, dasar].

    dasar=False: lolos validator, tetapi daemon hanya mendaftarkannya ke uinput
    bila dipakai di profil global (required_keycodes() melewati profil app)."""
    base = required_keycodes({"global": {}, "apps": {}})
    names = set(KEY_ALIASES)
    names.update(n[4:].lower() for n in KEY_BY_NAME
                 if isinstance(n, str) and n.startswith("KEY_"))
    table = {}
    for name in sorted(names):
        try:
            code = keycode(name)
        except KeyError:
            continue
        table[name] = [code, code in base]
    return table


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


def precheck(cfg):
    """Tolak isian yang akan membuat normalize()/check_config() melempar
    exception, dengan pesan yang menunjuk profil dan gesturnya."""
    errs = []
    scopes = [("global", cfg.get("global", {})), *cfg.get("apps", {}).items()]
    for app, scope in scopes:
        for slot, act in scope.items():
            acts = [("%s/%s" % (app, slot), act)]
            if act.get("type") == "sequence":
                acts = [("%s/%s langkah %d" % (app, slot, i), st)
                        for i, st in enumerate(act.get("steps", []), 1)]
            for where, a in acts:
                if not isinstance(a, dict):
                    continue  # dilaporkan check_config
                keys = a.get("keys")
                if a.get("type") == "command" and isinstance(keys, str):
                    try:
                        shlex.split(keys)
                    except ValueError as e:
                        why = ("kutip belum ditutup" if "quotation" in str(e)
                               else "garis miring terbalik (\\) di akhir")
                        errs.append("%s: command tidak bisa dibaca, %s" % (where, why))
                elif a.get("type") == "mouse" and "keys" in a and not keys:
                    errs.append("%s: tombol mouse belum diisi (left, right, middle)"
                                % where)
    return errs


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
        errs = precheck(cfg)
        if errs:
            return errs
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
            try:
                errs = g.save(cfg)
            except Exception as e:  # selalu jawab, jangan putus koneksi diam-diam
                why = "%s: %s" % (type(e).__name__, e)
                g.note("gagal menyimpan config: %s" % why)
                return self._json(500, {"errors": [why]})
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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Ccircle cx='12' cy='12' r='9' fill='none' stroke='%23e0b95b' stroke-width='3'/%3E%3C/svg%3E">
<style>
  :root { color-scheme: dark;
    /* neutral */
    --bg: #101114; --surface: #17191d; --surface-2: #1f2227; --surface-3: #292d33;
    --border: #31353d; --border-control: #686f7b;
    --text: #eceef1; --text-muted: #a9aeb8;
    /* satu aksen: hanya "aktif / dapat ditindaklanjuti" */
    --accent: #e0b95b; --accent-hover: #ecca78; --accent-soft: rgba(224,185,91,.12);
    --on-accent: #17130a;
    /* hanya aksi destruktif */
    --danger: #ff8d85; --danger-border: #a85650; --danger-soft: rgba(255,141,133,.12);
    /* spasi 4/8 */
    --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s6: 24px; --s8: 32px;
    --radius: 8px; --control-h: 36px;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", monospace; }
  * { box-sizing: border-box; }
  [hidden] { display:none !important; }
  html, body { height:100%; }
  body { margin:0; display:flex; flex-direction:column; overflow:hidden;
         background:var(--bg); color:var(--text);
         font:14px/1.5 system-ui, -apple-system, sans-serif; }
  .icon { width:16px; height:16px; flex:none; fill:none; stroke:currentColor; stroke-width:2;
          stroke-linecap:round; stroke-linejoin:round; }

  /* ---- kontrol ---- */
  button { display:inline-flex; align-items:center; justify-content:center; gap:var(--s2);
           height:var(--control-h); padding:0 var(--s3); white-space:nowrap; cursor:pointer;
           background:var(--surface-2); color:var(--text); border:1px solid var(--border-control);
           border-radius:var(--radius); font:500 14px/1 system-ui, -apple-system, sans-serif;
           transition:background-color .12s, border-color .12s; }
  button:hover:not(:disabled) { background:var(--surface-3); border-color:var(--text-muted); }
  button:focus-visible, input:focus-visible, select:focus-visible, pre:focus-visible {
           outline:2px solid var(--accent); outline-offset:2px; }
  button:disabled { cursor:default; color:var(--text-muted); border-color:var(--border);
                    background:transparent; }
  button.primary:not(:disabled) { background:var(--accent); border-color:var(--accent);
                    color:var(--on-accent); font-weight:600; }
  button.primary:hover:not(:disabled) { background:var(--accent-hover); border-color:var(--accent-hover); }
  button.danger { background:transparent; border-color:var(--danger-border); color:var(--danger); }
  button.danger:hover:not(:disabled) { background:var(--danger-soft); border-color:var(--danger); }
  button.on { border-color:var(--accent); color:var(--accent); background:var(--accent-soft); }
  button.icon-btn { width:28px; height:28px; padding:0; background:transparent;
                    border-color:transparent; color:var(--text-muted); }
  button.icon-btn:hover:not(:disabled) { color:var(--text); border-color:transparent; }
  button.icon-btn.dx:hover:not(:disabled) { color:var(--danger); background:var(--danger-soft); }
  select, input[type=text], input:not([type]) {
           height:var(--control-h); min-width:0; padding:0 var(--s3);
           background:var(--surface-2); color:var(--text); border:1px solid var(--border-control);
           border-radius:var(--radius); font:13px var(--mono); }
  input::placeholder { color:var(--text-muted); opacity:1; }
  input[list] { padding-right:var(--s8); text-overflow:ellipsis; }
  label { font-size:13px; font-weight:500; color:var(--text-muted); }

  /* ---- header ---- */
  header { flex:none; display:grid; align-items:center; gap:var(--s4);
           grid-template-columns:minmax(max-content,1fr) minmax(0,auto) minmax(max-content,1fr);
           height:56px; padding:0 var(--s4); background:var(--surface);
           border-bottom:1px solid var(--border); }
  .brand { display:flex; align-items:center; gap:var(--s2); }
  .brand .icon { width:24px; height:24px; color:var(--text); }
  .brand h1 { margin:0; font-size:16px; font-weight:600; letter-spacing:.04em; }
  .brand small { font-size:13px; color:var(--text-muted); }
  .status { display:flex; gap:var(--s2); min-width:0; justify-content:center; }
  .pill { display:flex; align-items:center; gap:var(--s2); height:32px; padding:0 var(--s3);
          border:1px solid var(--border); border-radius:16px; font-size:13px;
          color:var(--text-muted); white-space:nowrap; }
  .pill.focus { min-width:0; }
  .pill.focus b { overflow:hidden; text-overflow:ellipsis; color:var(--text);
                  font:500 13px var(--mono); }
  .dot { width:8px; height:8px; border-radius:50%; border:1.5px solid var(--text-muted); }
  .pill.on { color:var(--text); }
  .pill.on .dot { background:var(--accent); border-color:var(--accent); }
  .actions { display:flex; gap:var(--s2); justify-content:flex-end; }
  .actions #savebtn { min-width:104px; }

  /* ---- strip mode latihan ---- */
  #practicebar { flex:none; display:flex; align-items:center; gap:var(--s2); height:40px;
                 padding:0 var(--s4); background:var(--accent-soft);
                 border-bottom:1px solid var(--border); font-size:13px; white-space:nowrap; }
  #practicebar .icon { color:var(--accent); }
  #practicebar .ptext { overflow:hidden; text-overflow:ellipsis; }

  /* ---- tab profil ---- */
  nav { flex:none; display:flex; align-items:center; gap:var(--s2); height:52px;
        padding:0 var(--s4); border-bottom:1px solid var(--border); }
  #tabs { display:flex; gap:var(--s2); min-width:0; overflow-x:auto; padding:var(--s1);
          scrollbar-width:thin; }
  .tab { flex:none; display:flex; align-items:center; height:var(--control-h);
         border:1px solid var(--border); border-radius:var(--radius); background:var(--surface); }
  .tab:hover { border-color:var(--border-control); }
  .tab .app { height:100%; background:transparent; border:0; border-radius:var(--radius);
              color:var(--text-muted); }
  .tab .app:hover:not(:disabled) { background:transparent; color:var(--text); }
  .tab .x { margin-right:var(--s1); }
  .tab:has(.app.active) { border-color:var(--accent); background:var(--accent-soft); }
  .tab .app.active, .tab .app.active:hover:not(:disabled) { color:var(--accent); font-weight:600; }
  #addbtn { flex:none; width:var(--control-h); padding:0; }

  /* ---- area utama ---- */
  main { flex:1; min-height:0; overflow:auto; display:flex; padding:var(--s4); }
  .workspace { --knob:clamp(400px, calc(100vh - 480px), 460px);
               margin:auto; width:100%; max-width:1440px; display:grid; gap:var(--s4);
               grid-template-columns:minmax(0,1fr) clamp(340px, 30vw, 460px); }
  .panel { background:var(--surface); border:1px solid var(--border); border-radius:12px; }
  #knobcol { padding:var(--s4) var(--s4) var(--s6); min-width:0; }
  .head h2 { margin:0; font-size:18px; font-weight:600; overflow-wrap:anywhere; }
  .head p { margin:var(--s1) 0 var(--s4); font-size:13px; color:var(--text-muted); }
  .knobgrid { display:grid; justify-content:center; column-gap:var(--s2); row-gap:var(--s2);
              grid-template-columns:minmax(136px,1fr) var(--knob) minmax(136px,1fr);
              max-width:calc(var(--knob) + 496px); margin:0 auto; }
  .side { display:grid; grid-template-rows:repeat(3, minmax(0,1fr)); align-items:center; }
  .knobsvg { display:block; width:100%; aspect-ratio:1; align-self:center; }
  .pressrow { grid-column:2; justify-self:center; width:min(240px,100%); margin-top:calc(var(--s8) * -1);
              position:relative; }
  .glabel { flex-direction:column; align-items:stretch; gap:var(--s1); width:100%; height:auto;
            min-height:80px; padding:var(--s2) var(--s3); text-align:left;
            background:var(--surface-2); border-color:var(--border); min-width:0; }
  .glabel span { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .glabel .gname { display:flex; align-items:center; gap:var(--s2); font-size:13px;
                   font-weight:600; color:var(--text-muted); white-space:normal; line-height:1.3; }
  .glabel .gaction { font-size:15px; line-height:1.35; white-space:normal; overflow-wrap:anywhere;
                     display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
  .glabel .gkeys { font:13px/1.4 var(--mono); color:var(--text-muted); }
  .glabel.off .gaction { color:var(--text-muted); font-weight:400; }
  .glabel.off { border-style:dashed; border-color:var(--border-control); background:transparent; }
  .glabel.sel, .glabel.sel:hover:not(:disabled) { border:1px solid var(--accent);
                   background:var(--accent-soft); box-shadow:inset 0 0 0 1px var(--accent); }
  .glabel.sel .gname { color:var(--accent); }

  .knobsvg .body { fill:var(--surface-2); stroke:var(--border-control); stroke-width:1.5; }
  .knobsvg .knurl { fill:none; stroke:var(--border-control); stroke-width:10; stroke-dasharray:2 7; opacity:.6; }
  .zone { cursor:pointer; color:var(--text-muted); }
  .zone.off { color:var(--border-control); }
  .zone:hover { color:var(--text); }
  .zone.sel { color:var(--accent); }
  .zone .hit { fill:none; stroke:transparent; stroke-width:32; pointer-events:stroke; }
  .zone .arc { fill:none; stroke:currentColor; stroke-width:6; stroke-linecap:round; }
  .zone .arc.hold { stroke-dasharray:2 10; }
  .zone.sel .arc { stroke-width:9; }
  .zone .head-tip { fill:currentColor; stroke:none; }
  .zone .cap { fill:var(--surface); stroke:currentColor; stroke-width:2; }
  .zone.sel .cap { fill:var(--accent-soft); stroke-width:4; }
  .zone .capdot { fill:currentColor; }

  /* ---- editor aksi ---- */
  #editor { padding:var(--s4); min-width:0; }
  .slot { display:none; flex-direction:column; gap:var(--s4); }
  .slot.sel { display:flex; }
  .slot h3 { display:flex; align-items:center; gap:var(--s2); margin:0; padding-bottom:var(--s3);
             border-bottom:1px solid var(--border); font-size:16px; font-weight:600; }
  .field { display:flex; flex-direction:column; gap:var(--s1); }
  .row { display:flex; gap:var(--s2); align-items:center; }
  .row input { flex:1; }
  .chips { display:flex; flex-wrap:wrap; gap:var(--s1); }
  .chips:empty { display:none; }
  .chip { background:var(--surface-2); border:1px solid var(--border); border-bottom-width:3px;
          border-radius:4px; padding:var(--s1) var(--s2); font:13px/1.3 var(--mono);
          overflow-wrap:anywhere; }
  .steps { display:flex; flex-direction:column; gap:var(--s2); }
  .record.armed, .record.armed:hover:not(:disabled) { border-color:var(--accent);
          color:var(--accent); background:var(--accent-soft); }
  .record.add { align-self:flex-start; border-style:dashed; }
  .hint { margin:0; font-size:13px; color:var(--text-muted); }
  .field-note { display:flex; align-items:flex-start; gap:var(--s2); margin:0; font-size:13px;
                font-weight:600; }
  .field-note .icon { margin-top:2px; }
  input[aria-invalid=true] { border:2px solid var(--text); }
  input[readonly] { border-style:dashed; color:var(--text-muted); }

  /* ---- log & bar bawah ---- */
  body:has(#logbox:not([hidden])) .workspace { --knob:400px; }
  #logbox { flex:none; height:min(200px, 30vh); display:flex; background:var(--surface);
            border-top:1px solid var(--border); }
  #loglines { flex:1; overflow:auto; margin:0; padding:var(--s3) var(--s4);
              font:13px/1.5 var(--mono); white-space:pre-wrap; overflow-wrap:anywhere;
              color:var(--text-muted); }
  #loglines:empty::before { content:"Belum ada log."; }
  footer { flex:none; display:flex; align-items:center; gap:var(--s4); min-height:44px;
           padding:var(--s1) var(--s4); background:var(--surface);
           border-top:1px solid var(--border); font-size:13px; }
  .msg { flex:1; min-width:0; display:flex; align-items:center; gap:var(--s2); }
  .msg span { overflow-wrap:anywhere; max-height:4.5em; overflow-y:auto; }
  .msg:not(.show) .icon { display:none; }
  .msg.err { font-weight:600; }
  .path { display:flex; min-width:0; max-width:50%; color:var(--text-muted); font:13px var(--mono); }
  .path .dir { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .path .file { flex:none; color:var(--text); }
  footer button { flex:none; height:32px; font-size:13px; }
  footer button .chev { transition:transform .12s; }
  footer button[aria-expanded=true] .chev { transform:rotate(180deg); }

  /* ---- dialog tambah profil ---- */
  dialog { width:min(440px, calc(100vw - 32px)); padding:0; color:var(--text);
           background:var(--surface); border:1px solid var(--border-control); border-radius:12px; }
  dialog::backdrop { background:rgba(0,0,0,.6); }
  .dlg-head { display:flex; align-items:center; justify-content:space-between;
              padding:var(--s3) var(--s4); border-bottom:1px solid var(--border); }
  .dlg-head h2 { margin:0; font-size:16px; }
  .dlg-sec { display:flex; flex-direction:column; gap:var(--s2); padding:var(--s4); }
  .dlg-sec + .dlg-sec { border-top:1px solid var(--border); }
  .dlg-sec h3 { margin:0; font-size:14px; }
  .dlg-sec p { margin:0; overflow-wrap:anywhere; }
  .dlg-err { display:flex; align-items:center; gap:var(--s2); font-size:13px; font-weight:600; }
  .dlg-actions { display:flex; justify-content:flex-end; gap:var(--s2); margin-top:var(--s2); }
  .row select { flex:1; }
  .steps .row select { flex:none; }

  /* ---- tab tipe aksi ---- */
  #editor { container-type:inline-size; }
  .types { display:flex; flex-wrap:wrap; gap:var(--s1); }
  .types button { height:32px; padding:0 var(--s3); font-size:13px; color:var(--text-muted);
                  border-color:var(--border); background:transparent; }
  .types button[aria-pressed=true], .types button[aria-pressed=true]:hover:not(:disabled) {
                  color:var(--accent); border-color:var(--accent); background:var(--accent-soft);
                  font-weight:600; }

  /* ---- pemilih tombol visual ---- */
  .kbfields { display:grid; grid-template-columns:minmax(0,1fr); gap:var(--s4); align-items:start; }
  .picker { display:flex; flex-direction:column; gap:var(--s2); width:100%; max-width:1200px; }
  .kbhead { display:flex; align-items:center; justify-content:space-between; gap:var(--s2);
            flex-wrap:wrap; }
  .kbos { display:flex; align-items:center; gap:var(--s1); }
  .kbos button { height:28px; padding:0 var(--s2); font-size:13px; }
  .kbmedia { display:flex; flex-wrap:wrap; gap:var(--s1); }
  .kbmedia .kbkey { height:36px; min-width:88px; margin:0; padding:0 var(--s3); }
  .kb { display:grid; gap:var(--s2) var(--s3); grid-template-columns:3.75fr 4fr 7.25fr;
        grid-template-areas:"main main main" "nav num ."; }
  .kbblock { display:grid; row-gap:var(--s1); align-content:start;
             grid-template-rows:40px var(--s1) repeat(5,40px); }
  .kbblock.main { grid-area:main; grid-template-columns:repeat(60,minmax(0,1fr)); }
  .kbblock.nav { grid-area:nav; grid-template-columns:repeat(12,minmax(0,1fr)); }
  .kbblock.num { grid-area:num; grid-template-columns:repeat(16,minmax(0,1fr)); }
  .kbblock.nav button.kbkey { margin:0 1px; padding:0; }
  button.kbkey { height:100%; min-width:0; margin:0 2px; padding:0 2px; overflow:hidden;
             font-size:13px; border-radius:6px; }
  button.kbkey.on, button.kbkey.on:hover:not(:disabled) { background:var(--accent);
             border-color:var(--accent); color:var(--on-accent); font-weight:700; }
  button.kbkey.blocked, button.kbkey.blocked:hover:not(:disabled) { background:transparent;
             border:1px dashed #4d535d; color:#808692; }
  button.kbkey.blocked[aria-disabled=true] { cursor:not-allowed; }
  button.kbkey.blocked.on, button.kbkey.blocked.on:hover:not(:disabled) {
             border-color:var(--accent); background:var(--accent-soft); color:var(--accent); }
  #kbtip { position:fixed; z-index:20; max-width:300px; padding:var(--s2) var(--s3);
           background:var(--surface-3); border:1px solid var(--border-control);
           border-radius:var(--radius); font-size:13px; pointer-events:none; }
  @container (min-width:640px) { .kbfields { grid-template-columns:minmax(0,3fr) minmax(0,2fr); } }
  @container (min-width:880px) {
    .kb { grid-template-columns:15fr 3.75fr 4fr; grid-template-areas:"main nav num"; }
  }
  .workspace.with-kb { --knob:400px; max-width:none; grid-template-columns:712px minmax(0,1fr); }
  .with-kb #knobcol { padding-left:var(--s2); padding-right:var(--s2); }
  @media (max-width:1439px) {
    .workspace.with-kb { max-width:1200px; grid-template-columns:minmax(0,1fr); }
  }

  /* ---- responsif ---- */
  @media (max-width:1279px) {
    .workspace { --knob:400px; grid-template-columns:minmax(0,1fr) 340px; }
    #knobcol { padding-left:var(--s1); padding-right:var(--s1); }
    .knobgrid { column-gap:var(--s1); }
    .head { padding:0 var(--s2); }
    .brand small { display:none; }
  }
  @media (max-width:1079px) {
    .workspace { grid-template-columns:minmax(0,1fr); max-width:760px; }
    .brand h1 { display:none; }
  }
</style>
</head>
<body>
<svg xmlns="http://www.w3.org/2000/svg" hidden>
  <symbol id="i-logo" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v4"/></symbol>
  <symbol id="i-globe" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></symbol>
  <symbol id="i-window" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></symbol>
  <symbol id="i-practice" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></symbol>
  <symbol id="i-power" viewBox="0 0 24 24"><path d="M12 3v9M6.3 7a8 8 0 1 0 11.4 0"/></symbol>
  <symbol id="i-save" viewBox="0 0 24 24"><path d="M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM7 3v6h8V3M7 21v-7h10v7"/></symbol>
  <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></symbol>
  <symbol id="i-keyboard" viewBox="0 0 24 24"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 4L2 20h20zM12 10v4M12 17h.01"/></symbol>
  <symbol id="i-log" viewBox="0 0 24 24"><path d="M4 7l5 5-5 5M12 17h8"/></symbol>
  <symbol id="i-chevron" viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></symbol>
  <symbol id="i-ccw" viewBox="0 0 24 24"><path d="M3 4v6h6M3.5 10a9 9 0 1 1 .5 6"/></symbol>
  <symbol id="i-cw" viewBox="0 0 24 24"><path d="M21 4v6h-6M20.5 10a9 9 0 1 0-.5 6"/></symbol>
  <symbol id="i-press" viewBox="0 0 24 24"><path d="M12 3v9M8 9l4 4 4-4M4 20h16"/></symbol>
</svg>

<header>
  <div class="brand">
    <svg class="icon" aria-hidden="true"><use href="#i-logo"/></svg>
    <h1>ANTICATER</h1> <small>kontrol knob</small>
  </div>
  <div class="status">
    <span class="pill" id="dpill"><span class="dot" id="dot"></span><span id="dstate">memuat…</span></span>
    <span class="pill focus"><svg class="icon" aria-hidden="true"><use href="#i-window"/></svg>Fokus: <b id="focus">…</b></span>
  </div>
  <div class="actions">
    <button id="practicebtn" aria-pressed="false" onclick="togglePractice()" title="Klik gestur untuk melihat aksi yang akan dijalankan — tanpa memindah fokus ke editor"><svg class="icon" aria-hidden="true"><use href="#i-practice"/></svg>Mode latihan</button>
    <button id="daemonbtn" onclick="toggleDaemon()"><svg class="icon" aria-hidden="true"><use href="#i-power"/></svg><span id="daemonlabel">Nyalakan daemon</span></button>
    <button class="primary" id="savebtn" onclick="save()" disabled><svg class="icon" aria-hidden="true"><use href="#i-save"/></svg>Simpan</button>
  </div>
</header>
<div id="practicebar" role="status" hidden>
  <svg class="icon" aria-hidden="true"><use href="#i-practice"/></svg>
  <span class="ptext"><b>Mode latihan</b> · profil <b id="pprofile"></b> ·
    <span id="phint">klik gestur pada knob untuk melihat aksinya.</span>
    <span id="presult" hidden>gestur <b id="pgesture"></b> akan menjalankan <b id="paction"></b></span></span>
</div>
<nav aria-label="Profil">
  <div id="tabs">
    <div class="tab"><button class="app active" id="app-__global__" onclick="select('__global__', this)"><svg class="icon" aria-hidden="true"><use href="#i-globe"/></svg>global</button></div>
    <div id="applist" style="display:contents"></div>
  </div>
  <button id="addbtn" onclick="openAdd()" aria-label="Tambah app atau terapkan template" title="Tambah app / template" aria-haspopup="dialog"><svg class="icon" aria-hidden="true"><use href="#i-plus"/></svg></button>
</nav>
<main onscroll="hideTip()">
  <div class="workspace">
    <section id="knobcol" class="panel" aria-label="Knob"></section>
    <aside id="editor" class="panel" aria-label="Editor aksi"><div id="editor-inner"></div></aside>
  </div>
</main>
<section id="logbox" aria-label="Log daemon" hidden><pre id="loglines" tabindex="0"></pre></section>
<footer>
  <div class="msg" id="msgbox" role="status" aria-live="polite"><svg class="icon" aria-hidden="true"><use id="msgicon" href="#i-check"/></svg><span id="msg"></span></div>
  <span class="path" id="cfgpath"><span class="dir"></span><span class="file"></span></span>
  <button id="logbtn" aria-expanded="false" aria-controls="logbox" onclick="toggleLog()"><svg class="icon" aria-hidden="true"><use href="#i-log"/></svg>Log daemon<svg class="icon chev" aria-hidden="true"><use href="#i-chevron"/></svg></button>
</footer>

<dialog id="adddlg" aria-labelledby="addtitle" onclick="if(event.target===this) this.close()">
  <div class="dlg-head">
    <h2 id="addtitle">Tambah profil</h2>
    <button class="icon-btn" onclick="$('adddlg').close()" aria-label="Tutup"><svg class="icon" aria-hidden="true"><use href="#i-x"/></svg></button>
  </div>
  <div class="dlg-sec">
    <h3>Tambah app</h3>
    <label for="newapp">WM_CLASS aplikasi</label>
    <div class="row">
      <input id="newapp" list="wlist" placeholder="mis. kitty" autocomplete="off" onkeydown="if(event.key==='Enter'){event.preventDefault();addApp();}">
      <datalist id="wlist"></datalist>
      <button onclick="addApp()">Tambah</button>
    </div>
    <p class="hint">Pilih dari jendela yang sedang terbuka atau ketik manual.</p>
  </div>
  <div class="dlg-sec">
    <h3>Template</h3>
    <label for="tpl">Terapkan ke profil aktif: <b id="tpltarget"></b></label>
    <div class="row">
      <select id="tpl" onchange="$('tplerr').hidden=true">
        <option value="browser">Browser</option>
        <option value="code">Menulis kode</option>
        <option value="media">Media</option>
        <option value="desain">Desain</option>
        <option value="kliping">Kliping</option>
      </select>
      <button onclick="applyTpl()">Terapkan</button>
    </div>
    <p class="hint">Menimpa aksi kelima gestur. Tersimpan setelah Anda klik Simpan.</p>
    <p class="dlg-err" id="tplerr" role="alert" hidden><svg class="icon" aria-hidden="true"><use href="#i-alert"/></svg><span id="tplerrtext"></span></p>
  </div>
</dialog>

<dialog id="deldlg" aria-labelledby="deltitle" aria-describedby="deldesc" onclick="if(event.target===this) this.close()">
  <div class="dlg-head"><h2 id="deltitle">Hapus profil?</h2></div>
  <div class="dlg-sec">
    <p id="deldesc">Profil <b id="delname"></b> beserta aksi gesturnya akan dihapus. File config baru berubah setelah Anda klik Simpan.</p>
    <div class="dlg-actions">
      <button id="delcancel" onclick="$('deldlg').close()">Batal</button>
      <button id="delconfirm" class="danger" onclick="confirmRemove()">Hapus profil</button>
    </div>
  </div>
</dialog>
<div id="kbtip" role="tooltip" hidden></div>
<script>
"use strict";
const $ = id => document.getElementById(id);
const SLOTS = ["rotate_left","rotate_right","press","press_rotate_left","press_rotate_right"];
const SLOT_NAMES = {rotate_left:"Putar kiri",rotate_right:"Putar kanan",press:"Tekan",
  press_rotate_left:"Tahan + putar kiri",press_rotate_right:"Tahan + putar kanan"};
const SLOT_ICONS = {rotate_left:"ccw",rotate_right:"cw",press:"press",
  press_rotate_left:"ccw",press_rotate_right:"cw"};
const TEMPLATES = /*TEMPLATES*/null;   // diisi server dari TEMPLATES (Python)
const KEYTABLE = /*KEYTABLE*/null;     // nama -> [keycode, dasar], diturunkan dari daemon
const TYPE_TABS = [["shortcut","Shortcut"],["key","Tombol / Multimedia"],["mouse","Mouse"],
  ["scroll","Scroll"],["command","Perintah"],["sequence","Urutan"],["noop","Tanpa aksi"]];
let CFG = null, SEL = "__global__", DIRTY = false;

function esc(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",
  '"':"&quot;","'":"&#39;"}[c])); }

function ico(n){ return '<svg class="icon" aria-hidden="true"><use href="#i-'+n+'"/></svg>'; }

// 'keys' bisa berupa string selagi aksi command sedang diketik (belum disimpan)
function keyText(k, sep){ return Array.isArray(k) ? k.join(sep) : String(k||""); }

/* ---------- command <-> argv ----------
   Di disk argv berupa array. Di input ia ditampilkan berkutip sehingga
   shlex.split (Python, saat menyimpan) menghasilkan array yang sama. */
function quoteArg(a){
  a = String(a);
  if(a && !/[\\s'"\\\\]/.test(a)) return a;
  if(!a.includes("'")) return "'" + a + "'";
  return '"' + a.replace(/[\\\\"]/g, "\\\\$&") + '"';
}
function cmdText(k){ return Array.isArray(k) ? k.map(quoteArg).join(" ") : String(k||""); }
// padanan shlex.split (POSIX): hasilnya {args} atau {error}
function splitCmd(s){
  const args = []; let cur = null, q = "";
  for(let i=0; i<s.length; i++){
    const c = s[i];
    if(c === "\\\\" && q !== "'"){
      const n = s[++i];
      if(n === undefined) return {error:"Garis miring terbalik (\\\\) di akhir tidak diikuti karakter."};
      // di dalam "..." backslash hanya meloloskan " dan backslash
      cur = (cur||"") + (q === '"' && n !== '"' && n !== "\\\\" ? c + n : n);
    } else if(q){
      if(c === q) q = ""; else cur += c;
    } else if(c === "'" || c === '"'){ q = c; cur = cur||""; }
    else if(" \\t\\r\\n".includes(c)){ if(cur !== null){ args.push(cur); cur = null; } }
    else cur = (cur||"") + c;
  }
  if(q) return {error:"Kutip " + (q === "'" ? "tunggal (')" : 'ganda (")') + " belum ditutup."};
  if(cur !== null) args.push(cur);
  return {args};
}
// catatan di bawah input command
function cmdNote(inp){
  const el = document.createElement("p"); el.className = "field-note";
  el.setAttribute("role", "status"); el.innerHTML = ico("alert") + "<span></span>";
  const check = (type, keys) => {
    let t = ""; inp.readOnly = false;
    if(type === "command"){
      if(Array.isArray(keys) && keys.some(a => /[\\r\\n]/.test(a))){
        inp.readOnly = true;   // input satu baris akan membuang baris barunya
        t = "Argumen berisi baris baru. Ubah lewat file config agar tidak rusak.";
      } else if(!Array.isArray(keys)){
        const e = splitCmd(String(keys||"")).error;
        if(e) t = e + " Belum bisa disimpan.";
      }
    }
    el.hidden = !t; el.lastChild.textContent = t;
    inp.setAttribute("aria-invalid", !!t && !inp.readOnly);
  };
  return {el, check};
}

function actSum(a){
  if(!a || a.type === "noop") return "noop";
  if(a.type === "sequence")
    return "sequence " + ((a.steps||[]).length) + " langkah";
  const k = a.type === "command" ? cmdText(a.keys) : keyText(a.keys, " + ");
  return a.type + (k ? " " + k : "");
}

function curScope(){ return SEL==="__global__" ? CFG.global : (CFG.apps[SEL] || {}); }
function profileName(){ return SEL==="__global__" ? "global" : SEL; }

function markDirty(){
  if(!DIRTY){ DIRTY=true; $("savebtn").disabled=false; }
  updateKnob(); renderPractice();
}

/* ---------- knob ---------- */
function renderKnob(){
  const C = 200, R = 180;
  const pt = (deg, r) => [(C + r*Math.cos(deg*Math.PI/180)).toFixed(1),
                          (C - r*Math.sin(deg*Math.PI/180)).toFixed(1)];
  // busur dari sudut a ke b; kepala panah di ujung b, searah putaran
  const arc = (slot, a, b, hold) => {
    const d = Math.sign(b - a);
    const path = "M"+pt(a,R)+" A"+R+","+R+" 0 0,"+(d>0?0:1)+" "+pt(b,R);
    const tip = [pt(b + d*7, R), pt(b - d*1, R+11), pt(b - d*1, R-11)].join(" ");
    return '<g class="zone" data-slot="'+slot+'" onclick="focusSlot(\\'' +slot+ '\\')"><title></title>'+
      '<path class="hit" d="'+path+'"/><path class="arc'+(hold?' hold':'')+'" d="'+path+'"/>'+
      '<polygon class="head-tip" points="'+tip+'"/></g>';
  };
  const label = slot => '<button type="button" class="glabel" data-slot="'+slot+'" onclick="focusSlot(\\'' +slot+ '\\')">'+
    '<span class="gname">'+ico(SLOT_ICONS[slot])+esc(SLOT_NAMES[slot])+'</span>'+
    '<span class="gaction"></span><span class="gkeys"></span></button>';
  return '<div class="knobgrid">'+
    '<div class="side">'+label("press_rotate_left")+label("rotate_left")+'</div>'+
    '<svg class="knobsvg" viewBox="0 0 400 400" aria-hidden="true">'+
      '<circle class="body" cx="200" cy="200" r="146"/>'+
      '<circle class="knurl" cx="200" cy="200" r="134"/>'+
      arc("press_rotate_left", 114, 142, true) + arc("press_rotate_right", 66, 38, true) +
      arc("rotate_left", 158, 196, false) + arc("rotate_right", 22, -16, false) +
      '<g class="zone" data-slot="press" onclick="focusSlot(\\'press\\')"><title></title>'+
        '<circle class="cap" cx="200" cy="200" r="92"/><circle class="capdot" cx="200" cy="200" r="14"/></g>'+
    '</svg>'+
    '<div class="side">'+label("press_rotate_right")+label("rotate_right")+'</div>'+
    '<div class="pressrow">'+label("press")+'</div></div>';
}

function updateKnob(){
  if(!CFG) return;
  const scope = curScope();
  SLOTS.forEach(slot => {
    const a = scope[slot] || {type:"noop"};
    const off = !a.type || a.type === "noop";
    const summary = off ? "noop" : actSum(a);
    const z = document.querySelector('.zone[data-slot="'+slot+'"]');
    if(z){ z.classList.toggle("off", off);
      z.querySelector("title").textContent = SLOT_NAMES[slot]+": "+summary; }
    const l = document.querySelector('.glabel[data-slot="'+slot+'"]');
    if(!l) return;
    l.classList.toggle("off", off);
    const keys = a.type === "sequence" ? (a.steps||[]).length + " langkah" :
      a.type === "command" ? cmdText(a.keys) : keyText(a.keys, " + ");
    const main = off ? "Tanpa aksi" : (a.description || keys || "(belum diisi)");
    const sub = off ? "noop" : a.description ? summary : a.type;
    l.querySelector(".gaction").textContent = main;
    l.querySelector(".gkeys").textContent = sub;
    l.title = SLOT_NAMES[slot]+": "+(a.description ? a.description+" — " : "")+summary;
  });
}

let selSlot = "rotate_left";
function markSel(slot){
  if(slot !== selSlot) hideTip();
  selSlot = slot;
  if(rec && rec.slot !== slot) recStop();   // perekam tak boleh aktif di panel tersembunyi
  document.querySelectorAll(".zone").forEach(z => z.classList.toggle("sel", z.dataset.slot === slot));
  document.querySelectorAll(".glabel").forEach(l => {
    const on = l.dataset.slot === slot;
    l.classList.toggle("sel", on); l.setAttribute("aria-pressed", on); });
  document.querySelectorAll(".slot").forEach(c => c.classList.toggle("sel", c.id === "slot-"+slot));
  const card = $("slot-"+slot);
  document.querySelector(".workspace").classList.toggle("with-kb", !!card && card.dataset.kb === "1");
}
function focusSlot(slot){
  const el = document.getElementById("slot-"+slot);
  if(!el) return;
  markSel(slot);
  if(PRACTICE){ practicePreview(slot); return; }
  el.scrollIntoView({behavior:"smooth", block:"nearest"});
  const i = el.querySelector("input.keys"); if(i) i.focus({preventScroll:true});
}

/* ---------- mode latihan ---------- */
let rec = null;
let PRACTICE = false, PSLOT = null;
function togglePractice(){
  PRACTICE = !PRACTICE; PSLOT = null;
  $("practicebtn").classList.toggle("on", PRACTICE);
  $("practicebtn").setAttribute("aria-pressed", PRACTICE);
  $("practicebar").hidden = !PRACTICE;
  renderPractice();
}
function practicePreview(slot){ PSLOT = slot; renderPractice(); }
function renderPractice(){
  if(!PRACTICE || !CFG) return;
  $("pprofile").textContent = profileName();
  $("phint").hidden = !!PSLOT; $("presult").hidden = !PSLOT;
  if(!PSLOT) return;
  const act = curScope()[PSLOT] || {type:"noop"};
  $("pgesture").textContent = SLOT_NAMES[PSLOT];
  $("paction").textContent = (!act.type || act.type==="noop") ? "— (tidak ada aksi)" :
    (act.description ? act.description + " (" + actSum(act) + ")" : actSum(act));
}

/* ---------- pemilih tombol visual ---------- */
// tombol: [nama daemon, label, lebar dalam unit]; nama null = celah
const gap = w => [null, "", w];
const keys1 = s => s.split(" ").map(c => [c, c.toUpperCase()]);
const KB_MAIN = [
  [["esc","Esc"], gap(1), ...keys1("f1 f2 f3 f4"), gap(.5), ...keys1("f5 f6 f7 f8"), gap(.5),
   ...keys1("f9 f10 f11 f12")],
  [["backquote","`"], ...keys1("1 2 3 4 5 6 7 8 9 0"), ["minus","-"], ["equal","="],
   ["backspace","Backspace",2]],
  [["tab","Tab",1.5], ...keys1("q w e r t y u i o p"), ["bracketleft","["], ["bracketright","]"],
   ["backslash","\\\\",1.5]],
  [["capslock","Caps",1.75], ...keys1("a s d f g h j k l"), ["semicolon",";"], ["quote","'"],
   ["return","Enter",2.25]],
  [["shift","Shift",2.25], ...keys1("z x c v b n m"), ["comma",","], ["period","."], ["slash","/"],
   ["shift","Shift",2.75,"R"]],
  [["control","Ctrl",1.25], ["super","Win",1.25], ["alt","Alt",1.25], ["space","Space",6.25],
   ["alt","Alt",1.25,"R"], ["super","Win",1.25,"R"], ["compose","Menu",1.25],
   ["control","Ctrl",1.25,"R"]]];
const KB_NAV = [
  [["sysrq","PrtSc"], ["scrolllock","ScrLk"], ["pause","Pause"]],
  [["insert","Ins"], ["home","Home"], ["pageup","PgUp"]],
  [["delete","Del"], ["end","End"], ["pagedown","PgDn"]],
  [],
  [gap(1), ["up","↑"]],
  [["left","←"], ["down","↓"], ["right","→"]]];
// numpad: [nama, label, kolom, baris, lebar, tinggi]
const KB_NUM = [["numlock","Num",0,1],["kpslash","/",1,1],["kpasterisk","*",2,1],["kpminus","-",3,1],
  ["kp7","7",0,2],["kp8","8",1,2],["kp9","9",2,2],["kpplus","+",3,2,1,2],
  ["kp4","4",0,3],["kp5","5",1,3],["kp6","6",2,3],
  ["kp1","1",0,4],["kp2","2",1,4],["kp3","3",2,4],["kpenter","Ent",3,4,1,2],
  ["kp0","0",0,5,2],["kpdot",".",2,5]];
const KB_MEDIA = [["volume_down","Vol −"],["mute","Mute"],["volume_up","Vol +"],
  ["previous_track","Sebelumnya"],["play_pause","Play / pause"],["next_track","Berikutnya"],
  ["stop","Stop"]];
// toggle Mac/Windows: hanya label
const OS_LABELS = {
  win:{control:"Ctrl", shift:"Shift", alt:"Alt", super:"Win", return:"Enter", backspace:"Backspace"},
  mac:{control:"⌃ Ctrl", shift:"⇧ Shift", alt:"⌥ Opt", super:"⌘ Cmd", return:"Return", backspace:"Delete"}};
let KBOS = "win";
try{ if(localStorage.getItem("anticater.kbos") === "mac") KBOS = "mac"; }catch(e){}

const MODS = ["control","shift","alt","super"];
const RIGHT = {control:"rightctrl", shift:"rightshift", alt:"rightalt", super:"rightmeta"};
// sama dengan keycode() daemon: trim + huruf kecil
function keyInfo(n){
  const k = String(n).trim().toLowerCase();
  return Object.hasOwn(KEYTABLE, k) ? KEYTABLE[k] : null;
}
function keyList(k){ return Array.isArray(k) ? k.map(String) : String(k||"").split(/\\s+/).filter(Boolean); }
function famOf(n){
  const i = keyInfo(n); if(!i) return null;
  return MODS.find(f => keyInfo(f)[0] === i[0] || keyInfo(RIGHT[f])[0] === i[0]) || null;
}
const ON_BOARD = new Set([...KB_MAIN.flat(), ...KB_NAV.flat(), ...KB_NUM, ...KB_MEDIA,
  ...Object.values(RIGHT).map(n => [n])].map(k => k[0] && keyInfo(k[0])).filter(Boolean).map(i => i[0]));
// keycode yang dipakai profil global: ikut didaftarkan daemon ke uinput
function globalCodes(){
  const out = new Set();
  Object.values(CFG.global||{}).forEach(a => {
    (a.type === "sequence" ? a.steps||[] : [a]).forEach(x => {
      if(x && (x.type === "key" || x.type === "shortcut"))
        keyList(x.keys).forEach(n => { const i = keyInfo(n); if(i) out.add(i[0]); });
    });
  });
  return out;
}
// daftar keys baru setelah tombol 'name' diklik; lit = tombol itu sedang menyala
function pickKey(type, keys, name, lit){
  if(type === "key") return lit && keys.length === 1 ? [] : [name];
  const fam = MODS.includes(name) ? name : null;
  if(fam){
    const rest = keys.filter(k => famOf(k) !== fam);
    if(lit) return rest;
    let i = rest.findIndex(k => { const f = famOf(k); return !f || MODS.indexOf(f) > MODS.indexOf(fam); });
    if(i < 0) i = rest.length;
    return [...rest.slice(0,i), name, ...rest.slice(i)];
  }
  if(lit) return keys.filter(k => !keyInfo(k) || keyInfo(k)[0] !== keyInfo(name)[0]);
  return [...keys.filter(k => famOf(k)), name];    // ganti tombol utama, modifier tetap
}
// catatan untuk isi input "Kunci"
function keyProblems(keys){
  const unknown = [], unsent = [], off = [], inGlobal = globalCodes();
  keys.forEach(n => {
    const i = keyInfo(n);
    if(!i){ unknown.push(n); return; }
    if(SEL !== "__global__" && !i[1] && !inGlobal.has(i[0])) unsent.push(n);
    if(!ON_BOARD.has(i[0])) off.push(n);
  });
  const out = [];
  if(unknown.length) out.push("Tidak diterima daemon: " + unknown.join(", ") + ". Belum bisa disimpan.");
  if(unsent.length) out.push("Tidak akan terkirim di profil app: " + unsent.join(", ") +
    " (keterbatasan daemon: hanya terdaftar bila dipakai di profil global).");
  if(off.length) out.push("Tidak ada di keyboard visual: " + off.join(", ") + " (disimpan apa adanya).");
  return {text: out.join(" "), invalid: unknown.length > 0};
}

function showTip(btn){
  const why = btn.dataset.why, t = $("kbtip");
  if(!why) return hideTip();
  t.textContent = why; t.hidden = false;
  const r = btn.getBoundingClientRect();
  t.style.left = Math.max(8, Math.min(innerWidth - t.offsetWidth - 8,
    r.left + r.width/2 - t.offsetWidth/2)) + "px";
  const top = r.top - t.offsetHeight - 6;
  t.style.top = (top < 8 ? r.bottom + 6 : top) + "px";
}
function hideTip(){ $("kbtip").hidden = true; }
function setKbOs(os){
  KBOS = os;
  try{ localStorage.setItem("anticater.kbos", os); }catch(e){}
  document.querySelectorAll(".kbkey[data-mod-label]").forEach(b =>
    b.textContent = OS_LABELS[os][b.dataset.key]);
  document.querySelectorAll(".kbos button").forEach(b => {
    b.classList.toggle("on", b.dataset.os === os); b.setAttribute("aria-pressed", b.dataset.os === os); });
}

// onPick(daftarBaru) dipanggil saat tombol diklik; refresh() menyorot sesuai scope[slot].keys
function keyPicker(slot, scope, onPick){
  const type = scope[slot].type, isGlobal = SEL === "__global__";
  const el = document.createElement("div"); el.className = "picker";
  el.innerHTML = '<div class="kbhead"><label>' + (type === "shortcut" ?
      "Klik modifier untuk menyalakan/mematikan, lalu satu tombol utama" : "Klik satu tombol") +
    '</label><span class="kbos" role="group" aria-label="Label keyboard">' +
    '<button type="button" data-os="win">Windows</button>' +
    '<button type="button" data-os="mac">Mac</button></span></div>' +
    '<div class="kbmedia" role="group" aria-label="Multimedia"></div>' +
    '<div class="kb" role="group" aria-label="Keyboard virtual"><div class="kbblock main"></div>' +
    '<div class="kbblock nav"></div><div class="kbblock num"></div></div>' +
    '<p class="hint kblegend">Tombol abu-abu bergaris putus-putus: keterbatasan daemon, bukan tombol ' +
    'yang hilang. Sorot untuk keterangan.</p>';
  el.querySelectorAll(".kbos button").forEach(b => b.onclick = () => setKbOs(b.dataset.os));
  const buttons = [];
  const make = (parent, name, label, side) => {
    const b = document.createElement("button"); b.type = "button"; b.className = "kbkey";
    b.dataset.key = name; if(side) b.dataset.side = side;
    if(Object.hasOwn(OS_LABELS.win, name)){ b.dataset.modLabel = "1"; label = OS_LABELS[KBOS][name]; }
    b.textContent = label;
    b.onclick = () => {
      if(b.getAttribute("aria-disabled") === "true"){ showTip(b); return; }
      recStop(); hideTip();
      onPick(pickKey(type, keyList(scope[slot].keys), name, b.classList.contains("on")));
    };
    b.onpointerenter = b.onfocus = () => showTip(b);
    b.onpointerleave = b.onblur = hideTip;
    parent.appendChild(b); buttons.push(b);
    return b;
  };
  [["main", KB_MAIN], ["nav", KB_NAV]].forEach(([cls, rows]) => {
    rows.forEach((row, r) => {
      let col = 1;
      row.forEach(([name, label, w = 1, side]) => {
        if(name){
          const b = make(el.querySelector(".kbblock."+cls), name, label, side);
          b.style.gridColumn = col + " / span " + w*4;
          b.style.gridRow = r === 0 ? 1 : r + 2;
        }
        col += w*4;
      });
    });
  });
  KB_NUM.forEach(([name, label, c, r, w = 1, h = 1]) => {
    const b = make(el.querySelector(".kbblock.num"), name, label);
    b.style.gridColumn = (c*4+1) + " / span " + w*4;
    b.style.gridRow = (r+2) + " / span " + h;
  });
  const media = el.querySelector(".kbmedia");
  KB_MEDIA.forEach(([name, label]) => make(media, name, label));

  const refresh = () => {
    const codes = new Set(keyList(scope[slot].keys).map(keyInfo).filter(Boolean).map(i => i[0]));
    const inGlobal = globalCodes();
    buttons.forEach(b => {
      const name = b.dataset.key, i = keyInfo(name);
      const lit = !!i && (codes.has(i[0]) ||
        (b.dataset.side === "R" && codes.has(keyInfo(RIGHT[name])[0])));
      let why = "";
      if(!i) why = "Keterbatasan daemon: validator menolak “" + name + "” di sistem ini.";
      else if(!isGlobal && !i[1]){
        why = "Keterbatasan daemon: tombol ini hanya terkirim bila dipakai di profil global.";
        if(lit) why = (inGlobal.has(i[0]) ? "Terkirim karena juga dipakai di profil global. " :
          why + " ") + "Klik untuk melepas.";
      }
      b.classList.toggle("on", lit); b.setAttribute("aria-pressed", lit);
      b.classList.toggle("blocked", !!why);
      b.setAttribute("aria-disabled", !!why && !lit);
      b.dataset.why = why;
      if(why) b.setAttribute("aria-label", b.textContent + ". " + why);
      else b.removeAttribute("aria-label");
    });
    media.hidden = !(type === "key" || KB_MEDIA.some(([n]) => keyInfo(n) && codes.has(keyInfo(n)[0])));
    el.querySelector(".kblegend").hidden = !buttons.some(b =>
      b.classList.contains("blocked") && !b.closest("[hidden]"));
    el.querySelectorAll(".kbos button").forEach(b => {
      b.classList.toggle("on", b.dataset.os === KBOS); b.setAttribute("aria-pressed", b.dataset.os === KBOS); });
  };
  return {el, refresh};
}

/* ---------- perekam tombol ---------- */
const RECKEY = { " ":"space", Tab:"tab", Enter:"return", Backspace:"backspace", Delete:"delete",
  ArrowUp:"up", ArrowDown:"down", ArrowLeft:"left", ArrowRight:"right",
  Home:"home", End:"end", PageUp:"pageup", PageDown:"pagedown", Insert:"insert",
  AudioVolumeUp:"volume_up", AudioVolumeDown:"volume_down", AudioVolumeMute:"mute",
  MediaPlayPause:"play_pause", MediaTrackNext:"next_track", MediaTrackPrevious:"previous_track",
  MediaStop:"stop", ",":"comma", ".":"period", "/":"slash", ";":"semicolon", "'":"quote",
  "`":"backquote", "-":"minus", "=":"equal", "[":"bracketleft", "]":"bracketright" };
const REC_IDLE = () => ico("keyboard") + "Rekam";
function recStop(){
  if(rec){ rec.btn.innerHTML = REC_IDLE(); rec.btn.classList.remove("armed"); rec = null; }
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

/* ---------- profil ---------- */
function select(app, btn){
  SEL = app;
  document.querySelectorAll(".app").forEach(b => {
    const on = b.id === "app-"+app;
    b.classList.toggle("active", on); b.setAttribute("aria-current", on);
    if(on) b.parentElement.scrollIntoView({block:"nearest", inline:"nearest"});
  });
  renderEditor(); renderPractice();
}

function renderApps(){
  const list = $("applist"); list.innerHTML = "";
  const add = (c, active) => {
    const tab = document.createElement("div"); tab.className = "tab";
    const b = document.createElement("button"); b.type = "button";
    b.className = "app" + (active ? " active" : "");
    b.id = "app-"+c; b.textContent = c; b.onclick = () => select(c, b);
    b.setAttribute("aria-current", active);
    const x = document.createElement("button"); x.type = "button"; x.className = "icon-btn dx x";
    x.innerHTML = ico("x"); x.title = "Hapus profil " + c; x.setAttribute("aria-label", x.title);
    x.setAttribute("aria-haspopup", "dialog");
    x.onclick = e => { e.stopPropagation(); askRemove(c); };
    tab.append(b, x); list.appendChild(tab);
  };
  Object.keys(CFG.apps||{}).sort().forEach(c => add(c, c===SEL));
  const g = $("app-__global__");
  g.classList.toggle("active", SEL==="__global__");
  g.setAttribute("aria-current", SEL==="__global__");
}

function setType(slot, type){
  const scope = curScope(), a = scope[slot] = scope[slot] || {};
  if(a.type === type) return;
  const isKeys = t => t === "key" || t === "shortcut";
  if(!(isKeys(a.type) && isKeys(type))) a.keys = [];   // Shortcut <-> Tombol: isi dipertahankan
  a.type = type;
  renderEditor(); markDirty();
  document.querySelector('#slot-'+slot+' .types [data-type="'+type+'"]').focus();
}

function field(text, control, id){
  const f = document.createElement("div"); f.className = "field";
  const lb = document.createElement("label"); lb.textContent = text; lb.htmlFor = id;
  control.id = id; f.append(lb);
  return f;
}

function renderEditor(){
  recStop(); hideTip();
  const ed = $("editor-inner"); ed.innerHTML = "";
  const scope = curScope();
  $("knobcol").innerHTML = '<div class="head"><h2></h2><p></p></div>' + renderKnob();
  $("knobcol").querySelector("h2").textContent =
    SEL==="__global__" ? "Profil global" : "Profil: "+SEL;
  $("knobcol").querySelector("p").textContent = SEL==="__global__" ?
    "Berlaku untuk semua app lain. Klik gestur untuk mengatur aksinya." :
    "Berlaku saat app ini fokus. Klik gestur untuk mengatur aksinya.";
  for (const slot of SLOTS){
    const act = scope[slot] || {type:"noop", keys:[], description:""};
    const card = document.createElement("div"); card.className = "slot"; card.id = "slot-"+slot;
    const h3 = document.createElement("h3"); h3.innerHTML = ico(SLOT_ICONS[slot]);
    h3.append(SLOT_NAMES[slot]); card.appendChild(h3);

    const types = document.createElement("div"); types.className = "types";
    types.setAttribute("role", "group"); types.setAttribute("aria-label", "Tipe aksi");
    TYPE_TABS.forEach(([t, text]) => {
      const b = document.createElement("button"); b.type = "button"; b.textContent = text;
      b.dataset.type = t; b.setAttribute("aria-pressed", t === act.type);
      b.onclick = () => setType(slot, t); types.appendChild(b);
    });
    card.appendChild(types);
    const visual = act.type === "key" || act.type === "shortcut";
    if(visual) card.dataset.kb = "1";
    let picker = null, checkKeys = () => {}, keysField = null;

    if(act.type === "sequence"){
      const STYPES = ["key","shortcut","mouse","scroll","command"];
      const stepsBox = document.createElement("div"); stepsBox.className = "steps";
      const renderSteps = () => {
        stepsBox.innerHTML = '<label>Langkah (berurutan)</label>';
        scope[slot].steps = scope[slot].steps || [{type:"key", keys:[]}];
        scope[slot].steps.forEach((st, i) => {
          const sr = document.createElement("div"); sr.className = "row step";
          const ts = document.createElement("select");
          ts.setAttribute("aria-label", "Jenis langkah "+(i+1));
          STYPES.forEach(t => { const o = document.createElement("option"); o.value=t;
            o.textContent=t; if(t===st.type) o.selected=true; ts.appendChild(o); });
          const ip = document.createElement("input"); ip.type="text"; ip.className="keys";
          ip.setAttribute("aria-label", "Isi langkah "+(i+1));
          const note = cmdNote(ip);
          const refresh = () => {
            ip.placeholder = ts.value==="command" ? "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+" :
              ts.value==="mouse" ? "left|right|middle" : ts.value==="scroll" ? "up|down|left|right" :
              "control shift tab";
            ip.value = ts.value==="command" ? cmdText(st.keys) : keyText(st.keys, " ");
            note.check(ts.value, st.keys);
          };
          ts.onchange = () => { st.type = ts.value;
            st.keys = ts.value==="command" ? "" : []; refresh(); updateChips(); markDirty(); };
          ip.oninput = () => { st.keys = ts.value==="command" ? ip.value :
            ip.value.split(/\\s+/).filter(Boolean);
            note.check(ts.value, st.keys); updateChips(); markDirty(); };
          // jangan tulis ulang ip.value di sini: spasi yang baru diketik akan hilang
          const dx = document.createElement("button"); dx.type="button"; dx.className="icon-btn dx";
          dx.innerHTML = ico("x"); dx.title = "Hapus langkah "+(i+1);
          dx.setAttribute("aria-label", dx.title);
          dx.onclick = () => { scope[slot].steps.splice(i,1);
            if(!scope[slot].steps.length) scope[slot].steps.push({type:"key", keys:[]});
            renderSteps(); updateChips(); markDirty(); };
          sr.append(ts, ip, dx); refresh(); stepsBox.append(sr, note.el);
        });
        const add = document.createElement("button"); add.type="button"; add.className="record add";
        add.innerHTML = ico("plus") + "Langkah";
        add.onclick = () => { scope[slot].steps.push({type:"key", keys:[]});
          renderSteps(); markDirty(); };
        stepsBox.appendChild(add);
      };
      renderSteps();
      card.appendChild(stepsBox);
    } else {
      const r2 = document.createElement("div"); r2.className = "row";
      const inp = document.createElement("input"); inp.type="text"; inp.className="keys";
      inp.placeholder = act.type==="command" ? "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+" :
        act.type==="mouse" ? "left|right|middle" : act.type==="scroll" ? "up|down|left|right" :
        "control shift tab";
      inp.value = act.type==="command" ? cmdText(act.keys) : keyText(act.keys, " ");
      const note = cmdNote(inp);
      inp.oninput = () => { scope[slot] = scope[slot] || {};
        const v = inp.value.split(/\\s+/).filter(Boolean);
        scope[slot].keys = act.type==="command" ? inp.value : v;
        note.check(act.type, scope[slot].keys); sync(); markDirty(); };
      const f2 = field(act.type==="command" ? "Perintah (argv)" : act.type==="mouse" ? "Tombol" :
        act.type==="scroll" ? "Arah" : "Kunci", inp, "keys-"+slot);
      r2.append(inp);
      if(act.type !== "command" && act.type !== "mouse"){
        const rb = document.createElement("button"); rb.type="button"; rb.className="record";
        rb.innerHTML = REC_IDLE();
        rb.onclick = () => {
          if(rec && rec.btn !== rb) recStop();
          rec = {btn: rb, slot: slot, inp: inp, scope: scope, upd: sync};
          rb.classList.add("armed"); rb.innerHTML = ico("keyboard") + "Rekam… Esc batal";
          inp.focus();
        };
        r2.append(rb);
      }
      note.check(act.type, act.keys);
      f2.append(r2, note.el); card.appendChild(f2);
      if(visual){
        keysField = f2;
        const kn = document.createElement("p"); kn.className = "field-note keynote";
        kn.setAttribute("role", "status"); kn.innerHTML = ico("alert") + "<span></span>";
        f2.append(kn);
        checkKeys = () => {
          const r = keyProblems(keyList(scope[slot].keys));
          kn.hidden = !r.text; kn.lastChild.textContent = r.text;
          inp.setAttribute("aria-invalid", r.invalid);
        };
        picker = keyPicker(slot, scope, list => {
          scope[slot].keys = list; inp.value = list.join(" "); sync(); markDirty();
        });
      }
    }
    const chips = document.createElement("div"); chips.className = "chips"; card.appendChild(chips);
    const updateChips = () => {
      chips.innerHTML = "";
      if(scope[slot] && scope[slot].type === "sequence"){
        (scope[slot].steps || []).forEach(st => {
          const c = document.createElement("span"); c.className = "chip";
          const ks = st.type==="command" ? cmdText(st.keys) : keyText(st.keys, " ");
          c.textContent = (st.type==="key" || st.type==="shortcut") ? ks :
            st.type + (ks ? " "+ks : "");
          chips.appendChild(c);
        });
        return;
      }
      let ks = (scope[slot] && scope[slot].keys) || [];
      if(!Array.isArray(ks)) ks = scope[slot].type === "command" ? (splitCmd(String(ks)).args || []) :
        String(ks).trim() ? ks.split(/\\s+/) : [];
      (ks||[]).forEach(k => {
        const c = document.createElement("span"); c.className = "chip";
        c.textContent = k === "" ? "''" : k;
        chips.appendChild(c);
      });
    };
    const sync = () => { updateChips(); checkKeys(); if(picker) picker.refresh(); };
    sync();

    const d = document.createElement("input"); d.type="text"; d.className="keys";
    d.placeholder = "keterangan opsional"; d.value = act.description || "";
    d.oninput = () => { scope[slot] = scope[slot] || {}; scope[slot].description = d.value; markDirty(); };
    const f3 = field("Catatan", d, "desc-"+slot); f3.append(d); card.appendChild(f3);
    if(picker){   // Kunci + chip dan Catatan sebaris, keyboard di bawahnya
      keysField.append(chips);
      const row = document.createElement("div"); row.className = "kbfields";
      row.append(keysField, f3); card.append(row, picker.el);
    }
    card.addEventListener("focusin", () => markSel(slot));
    ed.appendChild(card);
  }
  updateKnob();
  if(selSlot) markSel(selSlot);
}

function openAdd(){
  recStop();                       // perekam tak boleh menangkap ketikan di dialog
  $("tplerr").hidden = true;
  $("tpltarget").textContent = profileName();
  $("adddlg").showModal();
  $("newapp").focus();
}
function addApp(){
  const c = $("newapp").value.trim(); if(!c) return;
  CFG.apps[c] = CFG.apps[c] || {}; SLOTS.forEach(s => CFG.apps[c][s] = CFG.apps[c][s] || {type:"noop",keys:[],description:""});
  $("newapp").value = ""; $("adddlg").close(); renderApps(); select(c); markDirty();
}
function removeApp(c){ delete CFG.apps[c]; renderApps(); if(SEL===c) select("__global__"); markDirty(); }

let DELAPP = null;
function askRemove(c){
  recStop();
  DELAPP = c;
  $("delname").textContent = c;
  $("deldlg").showModal();
  $("delcancel").focus();          // Enter tak sengaja = batal
}
function confirmRemove(){
  const c = DELAPP; if(c === null) return;
  DELAPP = null; $("deldlg").close();
  removeApp(c);
  document.querySelector(".app.active").focus();
  msg("Profil "+c+" dihapus. Klik Simpan untuk menyimpan.", false);
}

// Template diterapkan di sisi klien saja; tertulis ke server & disk lewat Simpan.
function applyTpl(){
  const sel = $("tpl"), name = sel.value;
  const fail = why => {
    const t = "Gagal menerapkan template: " + why;
    $("tplerrtext").textContent = t; $("tplerr").hidden = false; msg(t, true);
  };
  if(!TEMPLATES || !Object.hasOwn(TEMPLATES, name)) return fail("template tidak dikenal.");
  if(SEL!=="__global__" && !Object.hasOwn(CFG.apps, SEL)) return fail("profil aktif tidak ditemukan.");
  const label = sel.selectedOptions[0].textContent;
  const copy = JSON.parse(JSON.stringify(TEMPLATES[name]));
  // sama dengan /api/template: global ditimpa per slot, profil app diganti seluruhnya
  if(SEL==="__global__") Object.assign(CFG.global, copy); else CFG.apps[SEL] = copy;
  $("adddlg").close(); renderEditor(); markDirty();
  msg("Template "+label+" diterapkan ke "+profileName()+". Klik Simpan untuk menyimpan.", false);
}

/* ---------- server ---------- */
let SAVING = false, SAVE_TIMEOUT_MS = 10000;
async function save(){
  if(SAVING) return;
  SAVING = true; $("savebtn").disabled = true;
  msg("Menyimpan…", false); $("msgbox").classList.remove("show");
  const sent = JSON.stringify({config:CFG});
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), SAVE_TIMEOUT_MS);
  let why = "";
  try{
    const r = await fetch("/api/save", {method:"POST", headers:{"Content-Type":"application/json"},
      body:sent, signal:ctl.signal});
    const j = await r.json().catch(() => null);
    if(ctl.signal.aborted) throw new Error("timeout");
    if(!(r.ok && j && j.ok === true))
      why = (j && Array.isArray(j.errors) && j.errors.join(" · ")) || (j && j.error) ||
        "respons server tidak valid (HTTP " + r.status + ").";
  }catch(e){
    why = ctl.signal.aborted ?
      "server tidak menjawab dalam " + SAVE_TIMEOUT_MS/1000 + " detik. Perubahan mungkin belum tersimpan." :
      "server GUI tidak terjangkau. Pastikan anticater-gui masih berjalan, lalu coba lagi.";
  }
  clearTimeout(timer); SAVING = false;
  if(why) msg("Gagal menyimpan: " + why, true);
  else if(JSON.stringify({config:CFG}) === sent){ DIRTY = false; msg("Tersimpan", false); }
  else msg("Tersimpan, kecuali perubahan yang dibuat saat menyimpan. Klik Simpan lagi.", false);
  $("savebtn").disabled = !DIRTY;
}

let DAEMON_BUSY = false, DAEMON_TIMEOUT_MS = 10000;
async function toggleDaemon(){
  if(DAEMON_BUSY) return;
  DAEMON_BUSY = true; $("daemonbtn").disabled = true;
  const start = !window.DAEMON_RUNNING;
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), DAEMON_TIMEOUT_MS);
  let why = "", done = "";
  try{
    const r = await fetch("/api/daemon", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:start?"start":"stop"}), signal:ctl.signal});
    const j = await r.json().catch(() => null);
    if(ctl.signal.aborted) throw new Error("timeout");
    if(r.ok && j && j.ok === true) done = j.message;
    else why = (j && (j.message || j.error)) || "respons server tidak valid (HTTP " + r.status + ").";
  }catch(e){
    why = ctl.signal.aborted ? "server tidak menjawab dalam " + DAEMON_TIMEOUT_MS/1000 + " detik." :
      "server GUI tidak terjangkau. Pastikan anticater-gui masih berjalan, lalu coba lagi.";
  }
  clearTimeout(timer); DAEMON_BUSY = false; $("daemonbtn").disabled = false;
  if(why) msg("Gagal " + (start ? "menyalakan" : "mematikan") + " daemon: " + why, true);
  else msg(done, false);
  poll(true);
}

function msg(t, err){
  $("msg").textContent = t;
  $("msgbox").classList.add("show");
  $("msgbox").classList.toggle("err", !!err);
  $("msgicon").setAttribute("href", err ? "#i-alert" : "#i-check");
}

function toggleLog(){
  const open = $("logbox").hidden;
  $("logbox").hidden = !open;
  $("logbtn").setAttribute("aria-expanded", open);
  if(open) $("loglines").scrollTop = $("loglines").scrollHeight;
}

function load(state){
  CFG = state.config;
  const p = String(state.cfg_path), cut = p.lastIndexOf("/") + 1;
  $("cfgpath").title = p;
  $("cfgpath").querySelector(".dir").textContent = p.slice(0, cut);
  $("cfgpath").querySelector(".file").textContent = p.slice(cut);
  renderApps(); renderEditor();
}

function poll(force){
  fetch("/api/live").then(r=>r.json()).then(s=>{
    const f = $("focus"); f.textContent = s.focus || "(tidak ada)"; f.title = f.textContent;
    window.DAEMON_RUNNING = s.daemon_running;
    $("dpill").classList.toggle("on", s.daemon_running);
    $("dstate").textContent = s.daemon_running ? "daemon jalan" : "daemon mati";
    $("daemonlabel").textContent = s.daemon_running ? "Matikan daemon" : "Nyalakan daemon";
    $("daemonbtn").classList.toggle("danger", s.daemon_running);
    // tulis hanya bila berubah, agar posisi gulir & seleksi teks tidak terganggu
    const lg = $("loglines"), text = (s.log||[]).join("\\n");
    if(lg.textContent !== text){
      const stick = lg.scrollHeight - lg.scrollTop - lg.clientHeight < 24;
      lg.textContent = text;
      if(stick) lg.scrollTop = lg.scrollHeight;
    }
    const wl = $("wlist"), wins = (s.windows||[]).join("\\n");
    if(wl.dataset.wins !== wins){   // jangan bangun ulang saat daftar saran terbuka
      wl.dataset.wins = wins; wl.innerHTML = "";
      (s.windows||[]).forEach(c => { const o=document.createElement("option"); o.value=c; wl.appendChild(o); });
    }
    if(force) setTimeout(renderEditor, 0);
  }).catch(()=>{}).finally(()=> { if(!force) setTimeout(poll, 1500); });
}

fetch("/api/state").then(r=>r.json()).then(s=>{ load(s); poll(); }).catch(e=>{
  document.body.innerHTML = '<div style="padding:24px"><h2>Server tak merespons</h2><p>'+esc(e)+"</p></div>";
});
</script>
</body>
</html>
"""
PAGE = PAGE.replace("/*TEMPLATES*/null", json.dumps(TEMPLATES))
PAGE = PAGE.replace("/*KEYTABLE*/null", json.dumps(key_table()))


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