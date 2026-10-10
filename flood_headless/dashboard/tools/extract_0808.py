"""8/8 실제 자료 추출 -> dash_0808.json (대시보드 시안용, 읽기 전용)."""
import json, re, sys, glob
import numpy as np, pandas as pd
sys.path.insert(0, "/share/geosat-11/Urban_flooding/work/hjkim/FloodAgentic/flood_headless")
import config as C, rainfall as R, riskmodel as M
import geopandas as gpd

DAY = pd.Timestamp("2022-08-08")
OUT = sys.argv[1]
res = {"date": "2022-08-08"}

# ---- HSP 10분 유역평균 (mm/h)
st = M.load_static("polygon")[["px", "py"]]
fi, fj = R.radar_index(st.px.values, st.py.values)
win = R.radar_window(fi, fj)
cells = np.unique(np.stack([np.round(fj).astype(int) - win[2], np.round(fi).astype(int) - win[0]], 1), axis=0)
stamps = R.day_stamps(DAY)
cube, miss = R.hsp_cube(stamps, win)
hsp = cube[:, cells[:, 0], cells[:, 1]].mean(1)
hsp_max = cube[:, cells[:, 0], cells[:, 1]].max(1)
missing = [s[8:12] for s in stamps if not C.hsp_file(s).exists()]
res["hsp"] = {"rate": np.round(hsp, 2).tolist(), "cellmax": np.round(hsp_max, 1).tolist(), "missing": missing, "n_cells": int(len(cells))}

# ---- 서울시 AWS 10분 (mm/10min -> mm/h)
AD = "/share/geosat-11/Urban_flooding/data/03_서울시 AWS 10분자료(61개관측소)/03_서울시 AWS 10분자료(61개관측소)"
aws = {}
for name in ["관악구청(1108)", "신림펌프장(1109)", "구로구청(1112)", "동작구청(1120)", "영등포구청(1136)", "도림2(1135)"]:
    f = glob.glob(f"{AD}/{name}/*_2022.csv")[0]
    d = pd.read_csv(f, encoding="utf-8-sig")
    d.columns = ["t", "v"]
    d["t"] = pd.to_datetime(d.t)
    s = d.set_index("t").v.reindex(pd.date_range(DAY, periods=144, freq="10min"))
    aws[name.split("(")[0]] = [None if pd.isna(v) else round(float(v) * 6, 2) for v in s.values]
res["aws"] = aws

# ---- LDAPS 발령별 유역평균 (8/8 해당 시간, mm/h)
pts = M.load_static("polygon")[["px", "py"]].iloc[::50]
ld = {}
for ih in [3, 9, 15, 21]:
    iss = DAY + pd.Timedelta(hours=ih)
    vals = [None] * 24
    origin = None
    for h in range(1, 25):
        hour = ih + h - 1
        if hour >= 24:
            break
        p = C.ldaps_file(iss, h)
        if not p.exists():
            continue
        a, lat0, lon0, jpos = R._read_ldaps(p)
        if jpos != 1:
            a = a[::-1]
        if origin is None:
            import pyproj
            tf = pyproj.Transformer.from_crs("EPSG:4326", pyproj.CRS.from_dict(C.LDAPS_LCC), always_xy=True)
            origin = tf.transform(lon0, lat0)
            li, lj = R.ldaps_index(pts.px.values, pts.py.values, origin)
        v = R.bilinear(np.nan_to_num(a), li, lj)
        vals[hour] = round(float(np.mean(v)), 2)
    ld[f"{ih:02d}"] = vals
res["ldaps"] = ld

# ---- 하수관 수위 (유역 안) -> 10분 평균, 만관율
SJ = "/share/geosat-9/sjbae/PROJECT_FLOOD/data/processed"
sites = gpd.read_file(f"{SJ}/sewer_sites_basin.gpkg")
sites = sites[sites.in_basin]
lv = pd.read_parquet(f"{SJ}/sewer_levels_basin.parquet")
lv = lv[(lv.obs_time >= DAY) & (lv.obs_time < DAY + pd.Timedelta(days=1)) & lv.sensor_id.isin(sites.sensor_id)]
spec = pd.read_excel("/share/geosat-11/Urban_flooding/data/서울시 수위계(하수관로) 제원표_20260310.xlsx", header=1)
spec.columns = [str(c).replace("\n", "") for c in spec.columns]
spec = spec.rename(columns={"수위계번호(지점코드)": "sid", "관규격": "pipe", "기왕최대값(m)": "hist_max", "지점명": "spot"})


def pipe_h(s):
    s = str(s).replace(" ", "").lower().replace("×", "x")
    s = re.sub(r"^\d+@", "", s)
    m = re.findall(r"[\d.]+", s)
    if not m:
        return None
    nums = [float(v) for v in m]
    return nums[1] if "x" in s and len(nums) >= 2 else nums[0]


sew = []
for sid, g in lv.groupby("sensor_id"):
    row = spec[spec["sid"].astype(str) == sid]
    ph = pipe_h(row["pipe"].iloc[0]) if len(row) else None
    s = g.set_index("obs_time").level_m.resample("10min").mean().reindex(pd.date_range(DAY, periods=144, freq="10min"))
    meta = sites[sites.sensor_id == sid].iloc[0]
    sew.append({"id": sid, "gu": meta.gu_name, "loc": meta.location, "lat": float(meta.lat), "lon": float(meta.lon),
                "pipe": None if not len(row) else str(row["pipe"].iloc[0]), "pipe_h": ph,
                "spot": None if not len(row) else str(row.spot.iloc[0]),
                "hist_max": None if not len(row) else (None if pd.isna(row.hist_max.iloc[0]) else float(row.hist_max.iloc[0])),
                "level": [None if pd.isna(v) else round(float(v), 3) for v in s.values]})
res["sewer"] = sew

# ---- 03시 발령 예보 위험 (기존 계산 결과)
fc = json.load(open("/share/geosat-11/Urban_flooding/work/hjkim/FloodAgentic/flood_headless/outputs/20220808/forecast/summary.json"))
res["risk03"] = {"pixel_top_regions": fc["pixel"]["top_regions"][:6], "alert_area_km2": fc["pixel"]["alert_area_km2"]}
json.dump(res, open(OUT, "w"), ensure_ascii=False)
print("hsp day mm", round(float(hsp.sum() / 6), 1), "missing", missing)
print("aws day mm", {k: round(np.nansum([v or 0 for v in x]) / 6, 1) for k, x in aws.items()})
print("ldaps", {k: round(sum(v for v in x if v), 1) for k, x in ld.items()})
for s in sew:
    lvl = [v for v in s["level"] if v is not None]
    print(s["id"], s["spot"], s["pipe"], s["pipe_h"], s["hist_max"], "max", max(lvl) if lvl else None, "n", len(lvl))
