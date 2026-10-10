"""시연 장면별 블렌드 결과와 에이전트 브리핑을 data/tabs_0808.json 에 넣는다.

  python tools/update_scenes.py
입력: ../outputs/20220808/blend/<HHMM>_i*/ (flood_tool blend), ../outputs/20220808/observed/, briefings/<key>.md
"""
import json
from pathlib import Path

import pandas as pd

H = Path(__file__).resolve().parents[1]
OUT = H.parent / "outputs/20220808"
SCENES = ["0400", "1000", "1310", "1600", "2010", "2040", "2200"]


def regions(d: Path):
    r = pd.read_parquet(d / "region_from_polygon.parquet")
    r["w"] = r.mean_risk * r.n_units
    g = r.groupby(["drain_name", "gu"]).agg(w=("w", "sum"), n=("n_units", "sum"), p95=("p95_risk", "max"),
                                           na=("n_alert", "sum"), area=("area_alert_m2", "sum")).reset_index()
    g["m"] = g.w / g.n
    return [{"n": a.drain_name, "gu": a.gu, "mean": round(float(a.m), 4), "p95": round(float(a.p95), 4), "alert": int(a.na),
             "units": int(a.n), "area_km2": round(float(a.area) / 1e6, 3)} for a in g.itertuples()]


def ranked(d: Path):
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    r = pd.read_parquet(d / "region_ranked.parquet")
    o = {k: s["ranked"][k] for k in ["r3h_max_basin_mm", "stage", "top_pct", "n_top_units", "n_flagged_regions"]}
    o["regions"] = {a.drain_name: {"n_top": int(a.n_top), "share": round(float(a.top_share), 4), "n": int(a.n_units),
                                   "flag": bool(a.flagged)} for a in r.itertuples()}
    return s, o


tb = json.loads((H / "data/tabs_0808.json").read_text(encoding="utf-8"))
tb["blend"] = {}
for k in SCENES:
    d = next((OUT / "blend").glob(f"{k}_i*"))
    s, rk = ranked(d)
    sd = s["rain_provenance"]["same_day"]
    tb["blend"][k] = {"regions": regions(d), "ranked": rk, "dir": d.name, "pixel_alert_km2": s["pixel"]["alert_area_km2"],
                      "obs_mm": sd["obs_mm"]["mean"], "fc_mm": sd["fc_mm"]["mean"], "counts": s["polygon"]["class_counts"]}
s, rk = ranked(OUT / "observed")
tb["observed"], tb["observed_ranked"] = regions(OUT / "observed"), rk
tb["blend"]["final"] = {"regions": tb["observed"], "ranked": rk, "dir": "final_i080821", "pixel_alert_km2": s["pixel"]["alert_area_km2"],
                        "obs_mm": s["rain_basin"]["rain_mm"]["mean"], "fc_mm": 0.0, "counts": s["polygon"]["class_counts"]}
AG = {"obs-agent": "관측", "forecast-agent": "예측"}


def _summ(mode: str, j: dict) -> str:
    try:
        if mode == "rain":
            r = j["radar_basin_mean"]
            got = [f"{k} {v}" for k, v in r["reached"].items() if v]
            return (f"최근 1시간 {r['last_1h_mm']} mm · 3시간 {r['last_3h_mm']} mm · " +
                    ("기준 도달: " + ", ".join(got) if got else "기준 도달 없음"))
        if mode == "sewer":
            c = j["status_counts"]
            return " · ".join(f"{k} {v}" for k, v in c.items() if k != "정상") or "모두 정상"
        if mode == "status":
            b, p = j["blend"], j.get("prelim", {})
            return ("결합 계산 가능 (" + str(b.get("issue_kst")) + " 발령)") if b["ready"] else (
                "사전 예보 가능" if p.get("ready") else "계산 불가: " + b["reason"])
        if mode in ("blend", "prelim", "observed"):
            k = j["ranked"]
            return (f"강우 단계 {k['stage']} → 위험 상위 {k['top_pct']:g}% {k['n_top_units']:,}필지, "
                    f"위험 분구 {k['n_flagged_regions']}곳")
        if mode == "sensitivity":
            d = [x for x in j["drains"] if x.get("n_buildings")][:3]
            return " · ".join(f"{x['drain_name']} 상위10% {x['n_top']:,}동(반지하 {x['basement_in_top']:,})" for x in d)
    except Exception:
        return ""
    return ""


LABEL = {"rain": "강우·기준 도달 확인", "sewer": "하수관 수위계 확인", "status": "계산 가능 여부 확인", "blend": "오늘 위험 계산 (관측+예보)",
         "prelim": "내일 사전 예보 계산", "observed": "하루 관측으로 위험 계산", "forecast": "예보만으로 위험 계산", "sensitivity": "건물 민감도 조회"}


def trace(jl: Path) -> list:
    """stream-json 실행 기록 -> 활동 줄 목록 [{who, what, result}]"""
    sub, calls, out = {}, {}, []
    for line in jl.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        par = e.get("parent_tool_use_id")
        who = "총괄" if not par else AG.get(sub.get(par, ""), "하위")
        m = e.get("message")
        cont = m.get("content") if isinstance(m, dict) else None
        for c in cont if isinstance(cont, list) else []:
            if not isinstance(c, dict):
                continue
            if e["type"] == "assistant" and c.get("type") == "tool_use":
                inp = c.get("input", {})
                if c["name"] in ("Agent", "Task"):
                    sub[c["id"]] = inp.get("subagent_type", "")
                    out.append({"who": who, "what": f"{AG.get(inp.get('subagent_type'), '하위')} 에이전트에 맡김", "result": inp.get("description", "")})
                elif c["name"] == "Bash":
                    cmd = inp.get("command", "").replace(";", " ").replace("&&", " ").split()
                    k = next((i for i, x in enumerate(cmd) if x.endswith("flood_tool")), None)
                    if k is None or k + 1 >= len(cmd):
                        continue
                    mode = cmd[k + 1]
                    if mode == "sensitivity" and "--drains" in cmd:
                        mode_lbl = LABEL[mode] + " (" + cmd[cmd.index("--drains") + 1].replace(",", "·") + ")"
                    else:
                        mode_lbl = LABEL.get(mode, mode)
                    calls[c["id"]] = len(out)
                    out.append({"who": who, "what": mode_lbl, "result": "", "mode": mode})
            elif e["type"] == "user" and c.get("type") == "tool_result" and c.get("tool_use_id") in calls:
                i = calls[c["tool_use_id"]]
                txt = c.get("content")
                txt = txt if isinstance(txt, str) else "".join(x.get("text", "") for x in txt or [] if isinstance(x, dict))
                try:
                    out[i]["result"] = _summ(out[i]["mode"], json.loads(txt))
                except Exception:
                    out[i]["result"] = "오류" if "error" in txt else ""
            elif e["type"] == "assistant" and c.get("type") == "text" and par:
                first = next((l.strip("*# ") for l in c["text"].splitlines() if l.strip()), "")
                out.append({"who": who, "what": "보고 완료", "result": first[:60]})
    out.append({"who": "총괄", "what": "두 보고 결합 → 제안 행동·브리핑 작성", "result": ""})
    return out


tb["briefings"] = {}
for f in sorted((H / "briefings").glob("*.md")):
    raw = f.read_text(encoding="utf-8")
    act = None
    if "```actions" in raw:
        blk = raw.split("```actions", 1)[1].split("```", 1)[0]
        try:
            act = json.loads(blk)
        except Exception:
            act = None
        raw = raw.split("```actions", 1)[0] + raw.split("```actions", 1)[1].split("```", 1)[1]
    head, _, body = raw.partition("\n\n") if raw.startswith("> ") else ("", "", raw)
    q = head[2:].strip()
    txt = "\n".join(l for l in body.splitlines() if "/share/" not in l and "outputs/" not in l)
    meta = json.loads(f.with_suffix(".json").read_text(encoding="utf-8")) if f.with_suffix(".json").exists() else {}
    jl = f.with_suffix(".jsonl")
    tb["briefings"][f.stem] = {"md": txt, "trigger": q, "actions": act, "trace": trace(jl) if jl.exists() else [],
                               "model": ",".join(meta.get("modelUsage", {}).keys()), "cost": meta.get("total_cost_usd")}
(H / "data/tabs_0808.json").write_text(json.dumps(tb, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print("scenes", list(tb["blend"]), "briefings", list(tb["briefings"]))
