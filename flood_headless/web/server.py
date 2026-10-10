"""도림천 예보관 콘솔 — 실제 에이전트 웹 서버 (파이썬 표준 라이브러리만 사용).

  python web/server.py [--port 8765] [--host 0.0.0.0]

- 가상 시계로 2022-08-08 을 흘려보낸다 (실시간 자료 연결이 없어 기록 자료를 재생).
- 감시 루프: 예보 도착·침수 예/경보 기준 도달·수위계 첫 만관·하루 종료가 오면 에이전트(ask.sh, claude -p)를 스스로 실행한다.
  실행 중에는 시계를 멈추고, 직전 제안(승인 여부 포함)을 넘겨 이어서 판단하게 한다.
- 질문: 예보관이 질문창에 쓰면 지금 시계 시각 기준으로 같은 에이전트를 실행한다.
- 에이전트 활동은 실행 기록(stream-json)을 읽어 실행 중에도 한 줄씩 화면에 보낸다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

W = Path(__file__).resolve().parent
H = W.parent
sys.path.insert(0, str(W))
from agent_trace import Trace, split_answer  # noqa: E402

DASH = H / "dashboard"
QDIR = H / "outputs/queries"
BLEND = H / "outputs/20220808/blend"
OBS = H / "outputs/20220808/observed"
DAY = pd.Timestamp("2022-08-08")
END = DAY + pd.Timedelta(days=1, minutes=30)
LAG_H = 1
ENV = dict(os.environ, FLOOD_LDAPS_LAG_H=str(LAG_H),
           PROJ_DATA=str(Path.home() / "miniforge3/envs/floodagent/share/proj"))

LOCK = threading.RLock()
# 다른 사람(외부 주소)의 질문 허용: 한 사람당 2분에 1번, 서버 켠 뒤 전체 30번까지 (본인 claude 사용량 보호)
PUBLIC_ASK = {"on": True, "cooldown_s": 120, "daily_cap": 30, "used": 0, "last": {}}
STATE = {"now": DAY + pd.Timedelta(hours=3, minutes=50), "playing": False, "speed": 10.0,   # 가상 분 / 실제 초
         "auto": True, "fired": set(), "runs": [], "queue": []}


# ---------------------------------------------------------------- 상태 저장 (서버를 다시 켜도 시계·일지 유지)
SAVE = W / "state.json"


def save():
    with LOCK:
        d = {"now": f"{STATE['now']:%Y-%m-%d %H:%M:%S}", "auto": STATE["auto"], "speed": STATE["speed"],
             "fired": sorted(STATE["fired"]), "runs": [r for r in STATE["runs"] if r["status"] != "running"]}
    SAVE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def load():
    if not SAVE.exists():
        return
    try:
        d = json.loads(SAVE.read_text(encoding="utf-8"))
        STATE.update(now=pd.Timestamp(d["now"]), auto=d["auto"], speed=d["speed"], fired=set(d["fired"]), runs=d["runs"])
    except Exception:
        pass


# ---------------------------------------------------------------- 감시: 사건 목록 (기록 자료에서 미리 계산)
def triggers() -> list[dict]:
    d = json.loads((DASH / "data/dash_0808.json").read_text(encoding="utf-8"))
    ev = []
    for ih in (3, 9, 15, 21):
        ev.append((DAY + pd.Timedelta(hours=ih + LAG_H), f"{ih:02d}시 발표 수치예보가 도착했다. 상황을 판단하라."))
    dep = [v / 6 for v in d["hsp"]["rate"]]
    roll = lambda i, w: sum(dep[max(0, i - w + 1):i + 1])
    first = lambda f: next((i for i in range(144) if f(i)), None)
    i = first(lambda i: roll(i, 2) >= 20 or roll(i, 6) >= 55)
    if i is not None:
        ev.append((DAY + pd.Timedelta(minutes=10 * (i + 1)), "침수예보 기준 도달 감지: 레이더 유역평균 20분 20 mm 또는 1시간 55 mm 이상. 상황을 판단하고 조치를 제안하라."))
    i = first(lambda i: roll(i, 6) >= 50 and roll(i, 18) >= 90)
    if i is not None:
        ev.append((DAY + pd.Timedelta(minutes=10 * (i + 1)), "침수경보 기준 도달 감지: 1시간 50 mm 이상 그리고 3시간 90 mm 이상. 상황을 판단하고 조치를 제안하라."))
    for s in d["sewer"]:
        ph = s["pipe_h"] / 1000 if s["pipe_h"] and s["pipe_h"] > 20 else s["pipe_h"]
        s["_full"] = next((k for k, v in enumerate(s["level"]) if v is not None and ph and v / ph >= 1), None)
    k = min((s["_full"] for s in d["sewer"] if s["_full"] is not None), default=None)
    if k is not None:
        who = [s["spot"] for s in d["sewer"] if s["_full"] == k]
        ev.append((DAY + pd.Timedelta(minutes=10 * (k + 1)), f"하수관 수위계 첫 만관 감지: {', '.join(who)}. 상황을 판단하라."))
    ev.append((DAY + pd.Timedelta(days=1, minutes=30), "8/8 하루가 끝났다. 어제 상황과 너의 예측·제안이 맞았는지 검증하라."))
    ev.sort()
    merged = {}
    for t, x in ev:                                            # 같은 시각 사건은 한 번에 깨운다
        merged[t] = (merged[t].replace(" 상황을 판단하고 조치를 제안하라.", "").replace(" 상황을 판단하라.", "") + " / " + x) if t in merged else x
    return [{"t": t, "text": x, "key": f"{t:%m%d%H%M}"} for t, x in sorted(merged.items())]


TRIG = triggers()


# ---------------------------------------------------------------- 에이전트 실행
def prev_note() -> str:
    done = [r for r in STATE["runs"] if r["status"] == "done" and r.get("actions")]
    if not done:
        return ""
    r = done[-1]
    a = r["actions"]
    dec = {"approve": "예보관 승인", "edit": "예보관 수정 요청", None: "예보관 미승인"}.get(r.get("decision"), "예보관 미승인")
    return f"\n[직전 제안] {r['now'][11:16]} {a.get('level')} ({a.get('change')}): {a.get('level_reason', '')} ({dec})"


def start_run(kind: str, q: str, now: pd.Timestamp):
    rid = f"web_{time.strftime('%Y%m%d_%H%M%S')}_{len(STATE['runs'])}"
    run = {"id": rid, "kind": kind, "q": q, "now": f"{now:%Y-%m-%d %H:%M}", "status": "running", "lines": [],
           "actions": None, "md": "", "decision": None, "started": time.time()}
    STATE["runs"].append(run)
    threading.Thread(target=_run, args=(run, q + (prev_note() if kind == "auto" else ""), now), daemon=True).start()
    return run


def _run(run, q, now):
    jl = QDIR / f"{run['id']}.jsonl"
    env = dict(ENV, FLOOD_NOW=f"{now:%Y-%m-%d %H:%M}", FLOOD_RUN_ID=run["id"])
    p = subprocess.Popen([str(H / "ask.sh"), "--now", f"{now:%Y-%m-%d %H:%M}", q], cwd=H, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tr, pos = Trace(), 0
    while True:
        alive = p.poll() is None
        if jl.exists():
            with jl.open(encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    if line.endswith("\n"):
                        tr.feed(line)
                        pos += len(line.encode("utf-8"))
                    else:
                        break
            with LOCK:
                run["lines"] = [dict(x) for x in tr.lines]
        if not alive:
            break
        time.sleep(0.5)
    md = (QDIR / f"{run['id']}.md")
    body, act = split_answer(md.read_text(encoding="utf-8").split("\n\n", 1)[-1]) if md.exists() else ("", None)
    with LOCK:
        run.update(md=body, actions=act, status="done" if body else "error", sec=round(time.time() - run["started"]))
        run["lines"].append({"who": "총괄", "what": "두 보고 결합 → 제안 행동·브리핑 작성", "result": ""})
        if run["kind"] == "auto" and STATE.get("resume_after"):
            STATE["playing"], STATE["resume_after"] = not STATE.get("hold"), False
    save()


def busy() -> bool:
    return any(r["status"] == "running" for r in STATE["runs"])


def clock_loop():
    last = time.time()
    while True:
        time.sleep(0.25)
        now_t = time.time()
        dt, last = now_t - last, now_t
        with LOCK:
            if not STATE["playing"] or busy():
                continue
            old = STATE["now"]
            new = min(old + pd.Timedelta(minutes=STATE["speed"] * dt), END)
            due = [t for t in TRIG if old < t["t"] <= new and t["key"] not in STATE["fired"]] if STATE["auto"] else []
            if due:
                t = due[0]
                new = t["t"]
                STATE["fired"].add(t["key"])
                STATE["playing"], STATE["resume_after"] = False, True     # 에이전트가 판단하는 동안 시계 멈춤
                STATE["now"] = new
                start_run("auto", "[자동 실행] " + t["text"], new)
                continue
            STATE["now"] = new
            if new >= END:
                STATE["playing"] = False


# ---------------------------------------------------------------- 위험도 스냅숏 (에이전트가 계산한 blend 결과 중 지금 이전 최신)
_SNAP = {}


def snapshot(now: pd.Timestamp):
    if now >= DAY + pd.Timedelta(days=1) and (OBS / "summary.json").exists():
        d, key = OBS, "final"
    else:
        cands = []
        for d in BLEND.glob("*_i*"):
            t = DAY + pd.Timedelta(hours=int(d.name[:2]), minutes=int(d.name[2:4]))
            if t <= now and (d / "region_ranked.parquet").exists() and (d / "summary.json").exists():   # 쓰는 중인 폴더 제외
                cands.append((t, d))
        if not cands:
            return None, None
        t, d = max(cands)
        key = d.name
    if key in _SNAP:
        return key, _SNAP[key]
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    r = pd.read_parquet(d / "region_from_polygon.parquet")
    r["w"] = r.mean_risk * r.n_units
    g = r.groupby(["drain_name", "gu"]).agg(w=("w", "sum"), n=("n_units", "sum"), area=("area_alert_m2", "sum")).reset_index()
    regions = [{"n": a.drain_name, "gu": a.gu, "mean": round(float(a.w / a.n), 4), "area_km2": round(float(a.area) / 1e6, 3)}
               for a in g.itertuples()]
    rk = pd.read_parquet(d / "region_ranked.parquet")
    ranked = {k: s["ranked"][k] for k in ["r3h_max_basin_mm", "stage", "top_pct", "n_top_units", "n_flagged_regions"]}
    ranked["regions"] = {a.drain_name: {"n_top": int(a.n_top), "share": round(float(a.top_share), 4), "n": int(a.n_units),
                                        "flag": bool(a.flagged)} for a in rk.itertuples()}
    sd = s["rain_provenance"].get("same_day", {})
    out = {"regions": regions, "ranked": ranked, "dir": d.name if key != "final" else "final_i080821",
           "obs_mm": (sd.get("obs_mm") or {}).get("mean", s["rain_basin"]["rain_mm"]["mean"]),
           "fc_mm": (sd.get("fc_mm") or {}).get("mean", 0.0)}
    _SNAP[key] = out
    return key, out


# ---------------------------------------------------------------- 페이지
def page() -> str:
    t = (DASH / "console_tpl.html").read_text(encoding="utf-8")
    d = json.loads((DASH / "data/dash_0808.json").read_text(encoding="utf-8"))
    img = "data:image/jpeg;base64," + base64.b64encode((DASH / "data/terrain.jpg").read_bytes()).decode()
    t = (t.replace("/*DATA*/null", json.dumps(d, ensure_ascii=False, separators=(",", ":")))
          .replace("/*GEO*/null", (DASH / "data/geo.json").read_text(encoding="utf-8"))
          .replace("/*TABS*/null", (DASH / "data/tabs_0808.json").read_text(encoding="utf-8"))
          .replace("/*TERRAIN*/", img))
    live = (W / "live.js").read_text(encoding="utf-8")
    css = (W / "live.css").read_text(encoding="utf-8")
    head = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
    return head + t.replace("</style>", css + "\nbody{margin:0}\n</style>", 1) + f"\n<script>\n{live}\n</script>\n</body></html>"


def state(snap_have: str | None) -> dict:
    with LOCK:
        now = STATE["now"]
        key, snap = snapshot(now)
        nxt = next((x for x in TRIG if x["t"] > now and x["key"] not in STATE["fired"]), None)
        return {"now": f"{now:%Y-%m-%d %H:%M}", "n": int(min(144, max(0, (now - DAY) / pd.Timedelta(minutes=10)))),
                "playing": STATE["playing"], "speed": STATE["speed"], "auto": STATE["auto"], "busy": busy(),
                "next": {"t": f"{nxt['t']:%H:%M}", "text": nxt["text"].split(".")[0]} if nxt else None,
                "snap_key": key, "snap": snap if key != snap_have else None,
                "runs": [{k: v for k, v in r.items() if k != "started"} for r in STATE["runs"]]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            html = page()
            if not self.local():
                html = html.replace("<script>\n// 실제 에이전트 연결판", "<script>window.VIEW_ONLY=1;</script>\n<script>\n// 실제 에이전트 연결판", 1)
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path.startswith("/api/state"):
            have = self.path.split("snap=", 1)[1] if "snap=" in self.path else None
            return self._send(200, json.dumps(state(have), ensure_ascii=False))
        self._send(404, "{}")

    def local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_POST(self):
        if not self.local():                                   # 다른 사람은 질문만 (조작·승인은 m14 안에서만)
            if self.path != "/api/ask" or not PUBLIC_ASK["on"]:
                return self._send(403, json.dumps({"error": "보기 전용입니다"}))
            ip, now_t = self.client_address[0], time.time()
            with LOCK:
                last = PUBLIC_ASK["last"].get(ip, 0)
                if now_t - last < PUBLIC_ASK["cooldown_s"] or PUBLIC_ASK["used"] >= PUBLIC_ASK["daily_cap"]:
                    return self._send(429, json.dumps({"error": "잠시 후 다시 질문해 주세요"}))
                PUBLIC_ASK["last"][ip] = now_t
                PUBLIC_ASK["used"] += 1
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        with LOCK:
            if self.path == "/api/clock":
                a = body.get("action")
                if a == "play":
                    STATE["playing"] = True
                elif a == "pause":
                    STATE["playing"] = False
                elif a == "hold":                                   # 자동 시연 중: 판단이 끝나도 시계를 다시 흘리지 않음
                    STATE["hold"] = bool(body.get("on", True))
                elif a == "set":
                    STATE["now"] = min(max(pd.Timestamp(body["now"]), DAY), END)
                    STATE["fired"] = {t["key"] for t in TRIG if t["t"] <= STATE["now"]}   # 건너뛴 사건은 다시 깨우지 않음
                if "speed" in body:
                    STATE["speed"] = float(body["speed"])
            elif self.path == "/api/auto":
                STATE["auto"] = bool(body.get("on", True))
            elif self.path == "/api/ask":
                q = str(body.get("q", "")).strip()[:500]
                if q and not busy():
                    STATE["playing"] = False
                    start_run("ask", q, STATE["now"])
            elif self.path == "/api/rerun":
                r = next((x for x in STATE["runs"] if x["id"] == body.get("id")), None)
                if r and not busy():
                    start_run("ask", "[재분석 요청] " + r["q"].split("\n")[0], pd.Timestamp(r["now"]))
            elif self.path == "/api/decision":
                r = next((x for x in STATE["runs"] if x["id"] == body.get("id")), None)
                if r:
                    r["decision"] = body.get("decision")
            elif self.path == "/api/reset":
                STATE.update(now=DAY + pd.Timedelta(hours=3, minutes=50), playing=False, fired=set(), runs=[], hold=False)
            else:
                return self._send(404, "{}")
        save()
        self._send(200, json.dumps({"ok": True}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")   # 다른 사람은 보기 전용, 조작·질문은 m14 안(127.0.0.1)에서만
    a = ap.parse_args()
    load()
    threading.Thread(target=clock_loop, daemon=True).start()
    threading.Thread(target=lambda: [time.sleep(10) or save() for _ in iter(int, 1)], daemon=True).start()
    print(f"도림천 콘솔: http://{a.host}:{a.port}  (사건 {len(TRIG)}개: " + ", ".join(f"{t['t']:%H:%M}" for t in TRIG) + ")", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
