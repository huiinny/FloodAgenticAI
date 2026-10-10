"""관측 근거와 민감도 도구 (모델 계산 없이 자료만 읽는다). 기준 시각 이후 자료는 보지 않는다.

  rain_evidence(day, now)    레이더(HSP) 유역평균·최대, AWS 6곳: 최근 20분/1·3시간/오늘 누적, 기준 도달 시각
  sewer_evidence(day, now)   하수관 수위계: 만관율 현재값·1시간 변화·오늘 최고, 만관·수신 없음
  sensitivity(drains, top)   건물 정적 위험지도(results/building_risk.gpkg): 분구별 민감도 상위 건물과 원인

기준값 (서울시 침수 예·경보, 기상청 호우특보)
  침수예보: 15분 20 mm 이상(여기서는 20분 합으로 근사) 또는 1시간 55 mm 이상
  침수경보: 1시간 50 mm 이상 그리고 3시간 90 mm 이상 (상황판단회의 후 발령)
  호우주의보 3시간 60 mm, 호우경보 3시간 90 mm
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import rainfall as R

STEP = pd.Timedelta(minutes=C.RADAR_STEP_MIN)
AWS_DIR = Path("/share/geosat-11/Urban_flooding/data/03_서울시 AWS 10분자료(61개관측소)/03_서울시 AWS 10분자료(61개관측소)")
AWS_STATIONS = ["관악구청(1108)", "신림펌프장(1109)", "구로구청(1112)", "동작구청(1120)", "영등포구청(1136)", "도림2(1135)"]
SEWER_LEVELS = Path("/share/geosat-9/sjbae/PROJECT_FLOOD/data/processed/sewer_levels_basin.parquet")
SEWER_SITES = Path("/share/geosat-9/sjbae/PROJECT_FLOOD/data/processed/sewer_sites_basin.gpkg")
SEWER_SPEC = Path("/share/geosat-11/Urban_flooding/data/서울시 수위계(하수관로) 제원표_20260310.xlsx")
ROOT = C.HERE.parent
BLD_RISK = ROOT / "results/building_risk.gpkg"
BLD_FEAT = ROOT / "features/building_features.parquet"

THRESH = {"침수예보": "20분 20 mm 이상 또는 1시간 55 mm 이상", "침수경보": "1시간 50 mm 이상 그리고 3시간 90 mm 이상",
          "호우주의보": "3시간 60 mm 이상", "호우경보": "3시간 90 mm 이상"}
USE_NAME = {"01000": "단독주택", "02000": "공동주택", "03000": "제1종근린생활", "04000": "제2종근린생활",
            "07000": "판매", "10000": "교육연구", "11000": "노유자", "14000": "업무", "17000": "공장", "18000": "창고"}


def _t(ts) -> str:
    return f"{ts:%H:%M}"


def _windows(depth: np.ndarray, times: pd.DatetimeIndex) -> dict:
    """depth: 10분 강수량(mm) 시계열 -> 최근 창 합과 기준 도달 시각."""
    cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(depth))])
    n = len(depth)
    roll = lambda w: np.array([cs[i + 1] - cs[max(0, i + 1 - w)] for i in range(n)])
    r20, r1, r3 = roll(2), roll(6), roll(18)
    first = lambda m: _t(times[int(np.argmax(m))] + STEP) if m.any() else None
    return {"last_20min_mm": round(float(r20[-1]), 1) if n else 0.0,
            "last_1h_mm": round(float(r1[-1]), 1) if n else 0.0,
            "last_3h_mm": round(float(r3[-1]), 1) if n else 0.0,
            "today_mm": round(float(cs[-1]), 1),
            "max_1h_mm": round(float(r1.max()), 1) if n else 0.0,
            "max_3h_mm": round(float(r3.max()), 1) if n else 0.0,
            "reached": {"침수예보": first((r20 >= 20) | (r1 >= 55)), "침수경보": first((r1 >= 50) & (r3 >= 90)),
                        "호우주의보": first(r3 >= 60), "호우경보": first(r3 >= 90)}}


def rain_evidence(day: pd.Timestamp, now: pd.Timestamp) -> dict:
    asof, n = R.blend_split(day, now)
    times = pd.date_range(day, periods=n, freq=STEP)
    st = __import__("riskmodel").load_static("polygon")[["px", "py"]]
    fi, fj = R.radar_index(st.px.values, st.py.values)
    win = R.radar_window(fi, fj)
    cells = np.unique(np.stack([np.round(fj).astype(int) - win[2], np.round(fi).astype(int) - win[0]], 1), axis=0)
    stamps = R.day_stamps(day)[:n]
    cube, miss = R.hsp_cube(stamps, win) if n else (np.zeros((0, 1, 1)), 0)
    sub = cube[:, cells[:, 0], cells[:, 1]] / 6.0 if n else np.zeros((0, 1))
    out = {"as_of_kst": f"{asof:%Y-%m-%d %H:%M}", "thresholds": THRESH,
           "radar_basin_mean": _windows(sub.mean(1), times) if n else None,
           "radar_cell_max_last_10min_mmh": round(float(cube[-1].max()), 1) if n else 0.0,
           "radar_missing_10min": [s[8:12] for s in stamps if not C.hsp_file(s).exists()],
           "aws": []}
    for name in AWS_STATIONS:
        f = glob.glob(str(AWS_DIR / name / f"*_{day:%Y}.csv"))
        if not f:
            continue
        d = pd.read_csv(f[0], encoding="utf-8-sig")
        d.columns = ["t", "v"]
        s = d.assign(t=pd.to_datetime(d.t)).set_index("t").v.reindex(times)
        w = _windows(s.values.astype(float), times)
        w.update(station=name.split("(")[0], missing_10min=int(s.isna().sum()))
        out["aws"].append(w)
    out["aws"].sort(key=lambda x: -x["last_1h_mm"])
    return out


def _pipe_h(s) -> float | None:
    s = re.sub(r"^\d+@", "", str(s).replace(" ", "").lower().replace("×", "x"))
    m = [float(v) for v in re.findall(r"[\d.]+", s)]
    if not m:
        return None
    h = m[1] if "x" in s and len(m) >= 2 else m[0]
    return h / 1000 if h > 20 else h


def sewer_evidence(day: pd.Timestamp, now: pd.Timestamp) -> dict:
    import geopandas as gpd
    asof, _ = R.blend_split(day, now)
    sites = gpd.read_file(SEWER_SITES)
    sites = sites[sites.in_basin]
    lv = pd.read_parquet(SEWER_LEVELS, filters=[("obs_time", ">=", day), ("obs_time", "<", asof)])
    lv = lv[lv.sensor_id.isin(sites.sensor_id)]
    spec = pd.read_excel(SEWER_SPEC, header=1)
    spec.columns = [str(c).replace("\n", "") for c in spec.columns]
    spec = spec.set_index(spec["수위계번호(지점코드)"].astype(str))
    rows = []
    for _, s in sites.iterrows():
        g = lv[lv.sensor_id == s.sensor_id].set_index("obs_time").level_m.sort_index()
        sp = spec.loc[s.sensor_id] if s.sensor_id in spec.index else None
        ph = _pipe_h(sp["관규격"]) if sp is not None else None
        row = {"sensor_id": s.sensor_id, "spot": None if sp is None else str(sp["지점명"]), "gu": s.gu_name,
               "pipe": None if sp is None else str(sp["관규격"]), "lon": round(float(s.lon), 5), "lat": round(float(s.lat), 5)}
        if g.empty or ph is None:
            row.update(status="수신 없음" if g.empty else "관 높이 없음")
            rows.append(row)
            continue
        pct = g / ph * 100
        last_t = g.index[-1]
        prev = pct[pct.index <= last_t - pd.Timedelta(hours=1)]
        row.update(last_kst=_t(last_t), full_pct=round(float(pct.iloc[-1]), 0),
                   change_1h_pct=None if prev.empty else round(float(pct.iloc[-1] - prev.iloc[-1]), 0),
                   max_today_pct=round(float(pct.max()), 0),
                   first_full_kst=_t(pct.index[int(np.argmax(pct.values >= 100))]) if (pct >= 100).any() else None,
                   stale=bool(asof - last_t > pd.Timedelta(minutes=30)))
        row["status"] = ("수신 끊김" if row["stale"] else "만관" if row["full_pct"] >= 100 else
                         "주의(80%↑)" if row["full_pct"] >= 80 else "관심(50%↑)" if row["full_pct"] >= 50 else "정상")
        rows.append(row)
    rows.sort(key=lambda r: -(r.get("full_pct") or -1))
    cnt = pd.Series([r["status"] for r in rows]).value_counts().to_dict()
    return {"as_of_kst": f"{asof:%Y-%m-%d %H:%M}", "n_sensors": len(rows), "status_counts": cnt,
            "note": "만관율 = 수위 / 관 높이(박스는 높이, 원형은 지름). 피크에서 수신이 끊기거나 측정 상한에 고정되는 수위계가 있으니 '수신 끊김'도 위험 신호로 본다.",
            "sensors": rows}


def _buildings() -> pd.DataFrame:
    f = C.CACHE_DIR / "buildings_sensitivity.parquet"
    if f.exists():
        return pd.read_parquet(f)
    import pyogrio
    g = pyogrio.read_dataframe(BLD_RISK, columns=["bld_id", "gu_name", "risk_pct", "risk_class", "reason1", "reason2", "reason3"])
    c = g.geometry.centroid.to_crs(4326)
    g = pd.DataFrame(g.drop(columns="geometry")).assign(lon=c.x.round(5), lat=c.y.round(5))
    ft = pd.read_parquet(BLD_FEAT, columns=["bld_id", "drain_basin", "use_code", "floors_below", "bld_age", "hand_flow"])
    b = g.merge(ft, on="bld_id", how="left")
    C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    b.to_parquet(f, index=False)
    return b


def sensitivity(drains: list[str] | None = None, top_pct: float = 10.0, n_list: int = 5) -> dict:
    """건물 정적 위험지도: 비와 상관없이 '원래 잘 잠기는' 건물. risk_pct = 유역 건물 중 위험 백분위(높을수록 위험)."""
    b = _buildings()
    cut = 100 - top_pct
    hi = b[b.risk_pct >= cut]
    groups = [d for d in (drains or sorted(b.drain_basin.dropna().unique()))]
    out = []
    for d in groups:
        s = b[b.drain_basin == d]
        h = hi[hi.drain_basin == d].sort_values("risk_pct", ascending=False)
        if s.empty:
            out.append({"drain_name": d, "n_buildings": 0})
            continue
        out.append({"drain_name": d, "n_buildings": int(len(s)), "n_top": int(len(h)),
                    "top_share": round(len(h) / len(s), 3),
                    "basement_in_top": int((h.floors_below.fillna(0) > 0).sum()),
                    "top_buildings": [{"bld_id": int(r.bld_id), "risk_pct": round(float(r.risk_pct), 1),
                                       "use": USE_NAME.get(str(r.use_code), "기타"), "floors_below": int(r.floors_below or 0),
                                       "age": None if pd.isna(r.bld_age) else int(r.bld_age),
                                       "reasons": [x for x in (r.reason1, r.reason2, r.reason3) if isinstance(x, str) and x],
                                       "lon": r.lon, "lat": r.lat} for r in h.head(n_list).itertuples()]})
    out.sort(key=lambda x: -x.get("n_top", 0))
    return {"source": "건물 정적 위험지도 (results/building_risk.gpkg, XGBoost 25개 특성, 강우 미사용)",
            "validation": "2010·2011 사건 독립 검증 ROC-AUC 0.788 / 0.736 (REPORT 5-1)",
            "rule": f"유역 건물 중 위험 상위 {top_pct:g}% (risk_pct ≥ {cut:g})",
            "drains": out[:12] if not drains else out}
