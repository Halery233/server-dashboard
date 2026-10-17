#!/usr/bin/env python3
"""Tiny self-hosted server dashboard: metrics + service status + start/stop.

Runs as root (systemd) on 127.0.0.1:8088, meant to be reverse-proxied by Caddy.
Auth via HTTP Basic (DASH_USER / DASH_PASS env vars).
"""
import os
import re
import subprocess
import threading
import time
from collections import deque

import psutil
from flask import Flask, Response, jsonify, render_template, request

APP_USER = os.environ.get("DASH_USER", "admin")
APP_PASS = os.environ.get("DASH_PASS", "change-me")

MC_DIR = "/home/ubuntu/GT_New_Horizons_2.8.4_Server_Java_17-25"
MC_LOG = "/home/ubuntu/gtnh-run.log"
CS2_START = "/home/ubuntu/start_cs2.sh"
WEBSpeak_DIR = "/home/ubuntu/WebSpeak-client-for-TeamSpeak"
PIIK_DIR = "/home/ubuntu/piik"
TS3_DIR = "/home/ubuntu/ts3"

app = Flask(__name__)
_start_time = time.time()

# Network throughput history: one sample every 3s, keep ~6 minutes.
NET_HIST = deque(maxlen=120)
_net_lock = threading.Lock()


def _net_sampler():
    last = psutil.net_io_counters()
    last_t = time.time()
    while True:
        time.sleep(3)
        cur = psutil.net_io_counters()
        now = time.time()
        dt = max(now - last_t, 0.001)
        rec = {"t": int(now),
               "rx": (cur.bytes_recv - last.bytes_recv) / dt,
               "tx": (cur.bytes_sent - last.bytes_sent) / dt}
        with _net_lock:
            NET_HIST.append(rec)
        last, last_t = cur, now


threading.Thread(target=_net_sampler, daemon=True).start()


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def ok(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True).returncode == 0


@app.before_request
def _auth():
    a = request.authorization
    if not (a and a.username == APP_USER and a.password == APP_PASS):
        return Response("auth required", 401,
                        {"WWW-Authenticate": 'Basic realm="server-dashboard"'})


# ---------------------------------------------------------------- metrics
def get_metrics():
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    with _net_lock:
        last = NET_HIST[-1] if NET_HIST else {"rx": 0.0, "tx": 0.0}
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "cpu_cores": psutil.cpu_count(),
        "load": [round(x, 2) for x in os.getloadavg()],
        "mem_used": vm.used, "mem_total": vm.total, "mem_pct": vm.percent,
        "disk_used": du.used, "disk_total": du.total, "disk_pct": du.percent,
        "net_rx": last["rx"], "net_tx": last["tx"],
        "uptime": int(time.time() - psutil.boot_time()),
    }


# ---------------------------------------------------------------- services
def mc_players():
    players = set()
    try:
        with open(MC_LOG, "r", errors="ignore") as f:
            for line in f:
                m = re.search(r"([A-Za-z0-9_]{1,16}) joined the game", line)
                if m:
                    players.add(m.group(1))
                    continue
                m = re.search(r"([A-Za-z0-9_]{1,16}) left the game", line)
                if m:
                    players.discard(m.group(1))
    except OSError:
        pass
    return sorted(players)


def docker_running(name):
    rc, out, _ = run(f"docker inspect -f '{{{{.State.Running}}}}' {name}", 10)
    return rc == 0 and out.strip() == "true"


def svc_status():
    players = mc_players()
    return {
        "teamspeak": {"running": ok("pgrep -x ts3server")},
        "webspeak": {"running": docker_running("webspeak-client-for-teamspeak-webspeak-1")},
        "mc": {"running": ok("pgrep -x java"), "online": len(players), "players": players},
        "cs2": {"running": ok("pgrep -x cs2")},
        "piik": {"running": docker_running("piik-piik-1")},
    }


def bg(fn):
    threading.Thread(target=fn, daemon=True).start()


# Screen sessions / game servers belong to the "ubuntu" user, but this
# service runs as root, so screen must be invoked as that user.
def _u(cmd, timeout=40):
    return run(f"runuser -u ubuntu -- {cmd}", timeout)


def start_ts3():
    run(f"cd {TS3_DIR} && runuser -u ubuntu -- ./ts3server_startscript.sh start", 40)


def stop_ts3():
    run(f"cd {TS3_DIR} && runuser -u ubuntu -- ./ts3server_startscript.sh stop", 40)


def start_mc():
    _u("screen -S gtnh -X quit", 5)
    run(f"cd {MC_DIR} && runuser -u ubuntu -- screen -dmS gtnh bash -c "
        f"'bash startserver-java9.sh > {MC_LOG} 2>&1'", 20)


def stop_mc():
    _u("screen -S gtnh -X stuff $'stop\\r'", 5)
    time.sleep(25)
    _u("screen -S gtnh -X quit", 5)
    run("pkill -f startserver-java9.sh", 5)
    time.sleep(2)
    run("pkill -x java", 5)


def start_cs2():
    _u("screen -S cs2 -X quit", 5)
    run(f"cd /home/ubuntu && runuser -u ubuntu -- bash {CS2_START}", 40)


def stop_cs2():
    _u("screen -S cs2 -X stuff $'stop\\r'", 5)
    time.sleep(20)
    run("pkill -x cs2", 5)
    time.sleep(3)
    run("pkill -9 -x cs2", 5)
    _u("screen -S cs2 -X quit", 5)


def docker_up(d):
    run(f"cd {d} && docker compose up -d", 180)


def docker_stop(d):
    run(f"cd {d} && docker compose stop", 90)


SERVICES = {
    "teamspeak": (start_ts3, stop_ts3),
    "webspeak": (lambda: docker_up(WEBSpeak_DIR), lambda: docker_stop(WEBSpeak_DIR)),
    "mc": (start_mc, stop_mc),
    "cs2": (start_cs2, stop_cs2),
    "piik": (lambda: docker_up(PIIK_DIR), lambda: docker_stop(PIIK_DIR)),
}


# ---------------------------------------------------------------- routes
@app.route("/api/status")
def api_status():
    with _net_lock:
        hist = list(NET_HIST)
    return jsonify(metrics=get_metrics(), services=svc_status(), net_history=hist)


@app.route("/api/service/<name>/<action>", methods=["POST"])
def api_service(name, action):
    if name not in SERVICES:
        return jsonify(ok=False, error="unknown service"), 404
    if action == "start":
        bg(SERVICES[name][0])
        return jsonify(ok=True, msg=f"{name} starting")
    if action == "stop":
        bg(SERVICES[name][1])
        return jsonify(ok=True, msg=f"{name} stopping")
    return jsonify(ok=False, error="bad action"), 400


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8088, threaded=True)
