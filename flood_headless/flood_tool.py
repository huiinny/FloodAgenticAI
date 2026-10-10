"""도림천 하루 침수 위험 도구 (헤드리스 에이전트가 Bash 로 부르는 CLI).

  python flood_tool.py status   --date 2022-08-08   # 지금 무엇을 계산할 수 있나
  python flood_tool.py observed --date 2022-08-08   # 관측: 당일·선행 강우 모두 HSP
  python flood_tool.py forecast --date 2022-08-08   # 예보: 당일 LDAPS, 선행 HSP(대상일 이전만)
  python flood_tool.py blend    --date 2022-08-08   # 결합: 00시~지금 HSP 관측 + 지금~24시 최신 LDAPS 발령
  python flood_tool.py prelim   --date 2022-08-09   # 사전 예보: 밤(21시 발령 도착 후)에 내일 하루를 예보만으로
  python flood_tool.py rain     --date 2022-08-08   # 관측 근거: 레이더·AWS 최근 강우와 침수 예·경보 기준 도달
  python flood_tool.py sewer    --date 2022-08-08   # 관측 근거: 하수관 수위계 만관율
  python flood_tool.py sensitivity --date 2022-08-08 --drains 도림1,도림2   # 건물 정적 위험지도 (비 무관)

같은 sjbae 모델(4개 학습기)을 쓰고 입력 중 '당일 강우 6개'만 바꾼다.
결과는 outputs/<YYYYMMDD>/<mode>/ (blend 는 <mode>/<HHMM>_i<MMDDHH>/) 에 parquet 로 쓰고, 요약 JSON 을 stdout 에 낸다.

예보 트리거(대상일 D, KST):
  D-1 21시 발령 h004~h006 -> D 00~03시
  D   03시 발령 h001~h021 -> D 03~24시     => D 03시 자료 도착 후 계산
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pyproj

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C          # noqa: E402
import rainfall              # noqa: E402
import riskmodel             # noqa: E402
import evidence              # noqa: E402

warnings.filterwarnings("ignore")
_LL = pyproj.Transformer.from_crs(C.WORK_CRS, "EPSG:4326", always_xy=True)


def _ll(df):
    lon, lat = _LL.transform(df["px"].values, df["py"].values)
    return df.assign(lon=np.round(lon, 5), lat=np.round(lat, 5)).drop(columns=["px", "py"])


def _rec(df, cols, n=5):
    d = df[cols].head(n).copy()
    for c in d.select_dtypes("float").columns:
        d[c] = d[c].round(4)
    return json.loads(d.to_json(orient="records", force_ascii=False))


def now_kst() -> pd.Timestamp:
    """기준 시각. FLOOD_NOW="YYYY-MM-DD HH:MM" 이면 그 시각으로 가정(과거 사례 재현용), 없으면 서버 시각(KST)."""
    v = os.environ.get("FLOOD_NOW")
    return pd.Timestamp(v) if v else pd.Timestamp.now().floor("min")


def hsp_ante_upto(day: pd.Timestamp, now: pd.Timestamp) -> bool:
    """선행강우 7일 중 now 이전에 끝난 날의 HSP 가 있는지 (끝나지 않은 날은 사전 예보에서 부분 관측+예보로 채운다)."""
    days = [day - pd.Timedelta(days=k) for k in range(1, C.ANTECEDENT_DAYS + 1)]
    return all(C.hsp_file(f"{d:%Y%m%d}2350").exists() for d in days if d + pd.Timedelta(days=1) <= now)


def status(day: pd.Timestamp) -> dict:
    """대상일에 대해 지금 무엇을 계산할 수 있는지. 에이전트가 모드를 고르기 전에 본다."""
    now = now_kst()
    lag = pd.Timedelta(hours=float(os.environ.get("FLOOD_LDAPS_LAG_H", "0")))
    plan = rainfall.ldaps_plan(day)
    issues = sorted({i for i, _ in plan})
    have = [h for i, h in plan if C.ldaps_file(i, h).exists()]
    fc_time = max(issues) + lag
    ante = [day - pd.Timedelta(days=k) for k in range(1, C.ANTECEDENT_DAYS + 1)]
    hsp_ante = all(C.hsp_file(f"{d:%Y%m%d}2350").exists() for d in ante)
    hsp_day = C.hsp_file(f"{day:%Y%m%d}2350").exists()
    obs_ok = now >= day + pd.Timedelta(days=1) and hsp_day and hsp_ante
    fc_ok = now >= fc_time and len(have) == len(plan) and hsp_ante
    why_obs = ("가능" if obs_ok else
               "대상일이 아직 끝나지 않음" if now < day + pd.Timedelta(days=1) else "HSP 관측 파일 없음")
    why_fc = ("가능" if fc_ok else
              f"{fc_time:%Y-%m-%d %H:%M} 이후 가능 (03시 발령 도착 전)" if now < fc_time else
              f"LDAPS 파일 {len(plan) - len(have)}/{len(plan)}개 없음" if len(have) < len(plan) else
              "선행강우용 HSP 관측 없음")
    # 블렌딩: 00시~now 관측 + 최신 장기 발령으로 now~24시. 발령이 24시까지 닿아야 한다(D 03시 발령 이후).
    issue = rainfall.latest_issuance(day, now, float(os.environ.get("FLOOD_LDAPS_LAG_H", "0")))
    asof, n_obs = rainfall.blend_split(day, now)
    reach = issue is not None and issue + pd.Timedelta(hours=24) >= day + pd.Timedelta(days=1)
    partial = issue is not None and not reach and issue + pd.Timedelta(hours=24) > asof   # 새벽: 21시 발령이 21시까지만 닿음
    bplan = rainfall.blend_plan(day, asof, issue) if ((reach or partial) and asof < day + pd.Timedelta(days=1)) else []
    bfiles = all(C.ldaps_file(i, h).exists() for i, h in bplan if h <= 24)
    if now < day:
        why_bl, bl_ok = "대상일이 아직 시작되지 않음 (전날 21시 발령 도착 후에는 prelim 사전 예보)", False
    elif now >= day + pd.Timedelta(days=1):
        bl_ok, why_bl = obs_ok, ("가능 (하루가 끝나 observed 와 같음)" if obs_ok else why_obs)
    elif not reach and not partial:
        bl_ok, why_bl = False, f"{fc_time:%Y-%m-%d %H:%M} 이후 가능 (03시 발령 도착 전에는 24시까지 예보가 닿지 않음)"
    elif not reach and bfiles and hsp_ante:
        bl_ok, why_bl = True, (f"가능 (새벽: 전날 21시 발령이 21시까지만 닿아 21~24시는 비어 있음, "
                               f"{fc_time:%H:%M} 03시 발령 도착 후 다시 계산)")
    elif not bfiles:
        bl_ok, why_bl = False, f"LDAPS {issue:%m-%d %H}시 발령 파일 일부 없음"
    elif not hsp_ante:
        bl_ok, why_bl = False, "선행강우용 HSP 관측 없음"
    else:
        bl_ok, why_bl = True, "가능"
    # 사전 예보: 대상일이 아직 시작 전(또는 03시 발령 도착 전)이고 전날 21시 발령이 도착했을 때
    pre_ok = issue is not None and issue < day and now < day and hsp_ante_upto(day, now)
    why_pre = ("가능 (예보만, 21~24시 비어 있음, 과소예보 가능)" if pre_ok else
               "대상일이 이미 시작돼 blend 를 쓴다" if now >= day else
               "전날 21시 발령 도착 전" if issue is None or issue >= day else "선행강우용 HSP 관측 없음")
    return {"now_kst": f"{now:%Y-%m-%d %H:%M}", "date": f"{day:%Y-%m-%d}",
            "prelim": {"ready": bool(pre_ok), "reason": why_pre},
            "blend": {"ready": bool(bl_ok), "reason": why_bl, "as_of_kst": f"{asof:%Y-%m-%d %H:%M}",
                      "issue_kst": f"{issue:%Y-%m-%d %H}시" if issue is not None else None},
            "observed": {"ready": bool(obs_ok), "reason": why_obs},
            "forecast": {"ready": bool(fc_ok), "reason": why_fc,
                         "issues_kst": [f"{i:%Y-%m-%d %H}시" for i in issues]},
            "data_range": {"LDAPS": "2022-08-01 ~ 2022-09-01", "HSP": "2019-12-31 ~ 2025-08-01"}}


def run(mode: str, day: pd.Timestamp, scales: list[str]) -> dict:
    t0 = time.time()
    st = status(day)
    if not st[mode]["ready"]:
        raise RuntimeError(f"{day:%Y-%m-%d} {mode} 계산 불가 (기준 {st['now_kst']}): {st[mode]['reason']}")

    statics = {s: riskmodel.load_static(s) for s in scales}
    now, issue = now_kst(), None
    if mode in ("blend", "prelim"):
        issue = rainfall.latest_issuance(day, now, float(os.environ.get("FLOOD_LDAPS_LAG_H", "0")))
    rain, prov = rainfall.build_rain_table(day, mode, {s: s_[["px", "py"]] for s, s_ in statics.items()},
                                           now=now, issue=issue)

    out_dir = C.OUT_DIR / f"{day:%Y%m%d}" / mode
    if mode == "prelim":
        out_dir = out_dir / (f"{now:%m%d%H%M}" + (f"_i{issue:%m%d%H}" if issue is not None else ""))
    if mode == "blend":                                       # 시점마다 snapshot 하나
        asof, _ = rainfall.blend_split(day, now)
        out_dir = out_dir / (f"{asof:%H%M}" + (f"_i{issue:%m%d%H}" if issue is not None else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"date": f"{day:%Y-%m-%d}", "mode": mode,
               "model": "sjbae PROJECT_FLOOD 4-learner ensemble (risk=평균)",
               "rain_provenance": prov, "outputs": {}}

    if "polygon" in scales:
        r = riskmodel.predict("polygon", statics["polygon"], rain["polygon"])
        rr = pd.concat([r, rain["polygon"]], axis=1)
        rr.to_parquet(out_dir / "polygon_risk.parquet", index=False)
        reg = riskmodel.rollup_polygon(r)
        reg.to_parquet(out_dir / "region_from_polygon.parquet", index=False)
        summary["outputs"]["polygon"] = str(out_dir / "polygon_risk.parquet")
        summary["outputs"]["region_from_polygon"] = str(out_dir / "region_from_polygon.parquet")
        rp = rain["polygon"]
        summary["rain_basin"] = {k: {"mean": round(float(rp[k].mean()), 2), "max": round(float(rp[k].max()), 2)}
                                 for k in rp.columns}
        top = _ll(r.sort_values("risk", ascending=False))
        summary["polygon"] = {
            "n_units": int(len(r)),
            "class_counts": r.risk_class.value_counts().reindex(C.RISK_LABELS, fill_value=0).to_dict(),
            "n_alert(risk>=0.5)": int((r.risk >= C.ALERT_THR).sum()),
            "mean_risk": round(float(r.risk.mean()), 4),
            "learner_mean_prob": {k: round(float(r[k].mean()), 4) for k in r.columns if k.startswith("p_")},
            "median_confidence": round(float(r.confidence.median()), 3),
            "top_regions": _rec(reg, ["drain_name", "gu", "mean_risk", "p95_risk", "n_alert", "area_alert_m2", "n_units"]),
            "top_units": _rec(top, ["unit_id", "lc_group", "drain_name", "gu", "risk", "confidence", "lon", "lat"]),
        }
        r3 = float(rp["r3h_max_mm"].mean())
        stage, pct = riskmodel.rain_stage(r3)
        rk, thr = riskmodel.ranked_regions(r, pct)
        rk.to_parquet(out_dir / "region_ranked.parquet", index=False)
        summary["ranked"] = {
            "rule": "위치 = 위험 순위, 범위 = 3시간 최대 강우(유역평균) 단계별 상위 %",
            "r3h_max_basin_mm": round(r3, 1), "stage": stage, "top_pct": pct,
            "n_top_units": int(rk.n_top.sum()), "risk_cut": None if pct <= 0 else round(thr, 4),
            "flag_rule": f"분구 필지 중 표시 대상 {C.DRAIN_FLAG_SHARE:.0%} 이상이면 위험 표시 분구",
            "n_flagged_regions": int(rk.flagged.sum()),
            "flagged_regions": _rec(rk[rk.flagged], ["drain_name", "gu", "n_top", "top_share", "n_units"], n=30),
        }

    if "pixel" in scales:
        r = riskmodel.predict("pixel", statics["pixel"], rain["pixel"])
        r[["pixel_id", "sector_id", "drain_idx", "p_lightgbm", "p_xgboost", "p_catboost", "p_randomforest",
           "risk", "confidence"]].to_parquet(out_dir / "pixel_risk.parquet", index=False)
        sec = _ll(riskmodel.rollup_pixel(r, ["sector_id", "sector_i", "sector_j"]))
        reg = riskmodel.rollup_pixel(r, ["drain_idx", "drain_name", "gu"]).drop(columns=["px", "py"])
        sec.to_parquet(out_dir / "sector_from_pixel.parquet", index=False)
        reg.to_parquet(out_dir / "region_from_pixel.parquet", index=False)
        summary["outputs"].update(pixel=str(out_dir / "pixel_risk.parquet"),
                                  sector_from_pixel=str(out_dir / "sector_from_pixel.parquet"),
                                  region_from_pixel=str(out_dir / "region_from_pixel.parquet"))
        summary["pixel"] = {
            "n_pixels": int(len(r)),
            "alert_area_km2": round(float((r.risk >= C.ALERT_THR).sum() * 1e-4), 3),
            "mean_risk": round(float(r.risk.mean()), 4),
            "learner_mean_prob": {k: round(float(r[k].mean()), 4) for k in r.columns if k.startswith("p_")},
            "top_sectors": _rec(sec, ["sector_id", "risk_mean", "risk_p90", "alert_frac", "area_alert_km2", "lon", "lat"]),
            "top_regions": _rec(reg, ["drain_name", "gu", "risk_mean", "risk_p90", "alert_frac", "area_alert_km2", "n_pixel"]),
        }

    summary["caveats"] = [
        "모델은 2022-08 한 달, HSR 레이더(AWS 보정) 강우로 학습됨. 관측 입력은 HSP 로 대체(총량비 ~1.0).",
        "polygon 과 pixel 은 입력이 다른 두 모델이라 불일치는 근거로 쓸 것.",
    ]
    if mode == "prelim":
        summary["caveats"].append("사전 예보: 대상일 강우 전부를 전날 21시 LDAPS 발령으로 채웠고 21~24시는 비어 있다(0). "
                                  "LDAPS 는 강한 비를 크게 과소예보할 수 있어 참고용이다. 대상일 03시 발령 도착 후 blend 로 다시 계산한다.")
    if mode in ("forecast", "blend", "prelim"):
        summary["caveats"].append("예보: LDAPS 1.5 km·1시간 강수로 당일 특성을 근사. 대류성 국지호우는 크게 과소예보될 수 있음. "
                                  "예보 강우가 작다고 위험이 없다고 단정하지 말 것.")
    summary["elapsed_s"] = round(time.time() - t0, 1)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["status", "blend", "prelim", "observed", "forecast", "rain", "sewer", "sensitivity"])
    ap.add_argument("--date", required=True, help="대상일 KST, YYYY-MM-DD")
    ap.add_argument("--drains", default="", help="sensitivity: 배수분구 이름 (쉼표). 비우면 상위 분구")
    ap.add_argument("--top-pct", type=float, default=10.0, help="sensitivity: 건물 위험 상위 % (기본 10)")
    ap.add_argument("--scales", default="polygon,pixel", help="polygon,pixel 중 선택 (쉼표)")
    a = ap.parse_args()
    try:
        day = pd.Timestamp(a.date).normalize()
        if a.mode == "status":
            print(json.dumps(status(day), ensure_ascii=False, indent=1))
            return
        if a.mode in ("rain", "sewer"):                      # 관측 근거: 기준 시각까지만
            fn = evidence.rain_evidence if a.mode == "rain" else evidence.sewer_evidence
            print(json.dumps(fn(day, min(now_kst(), day + pd.Timedelta(days=1))), ensure_ascii=False, indent=1))
            return
        if a.mode == "sensitivity":
            dr = [x for x in a.drains.split(",") if x]
            print(json.dumps(evidence.sensitivity(dr or None, a.top_pct), ensure_ascii=False, indent=1))
            return
        s = run(a.mode, pd.Timestamp(a.date).normalize(), [x for x in a.scales.split(",") if x])
    except Exception as e:                                   # 에이전트가 읽을 수 있게 JSON 으로
        print(json.dumps({"error": f"{type(e).__name__}: {e}", "mode": a.mode, "date": a.date},
                         ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(s, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
