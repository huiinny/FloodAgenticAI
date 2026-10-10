"""강우 단계별 실제 침수 필지 비율 (단계별 표시 % 보정용)."""
import sys
import numpy as np, pandas as pd, geopandas as gpd
sys.path.insert(0, "/share/geosat-11/Urban_flooding/work/hjkim/FloodAgentic/flood_headless")
import config as C, rainfall as R, riskmodel as M
units = gpd.read_file("/share/geosat-9/sjbae/PROJECT_FLOOD/data/processed/units.gpkg").to_crs(5186)
uid = "unit_id"; units["a"] = units.area
st = M.load_static("polygon")[["px", "py"]]
fi, fj = R.radar_index(st.px.values, st.py.values); win = R.radar_window(fi, fj)
cells = np.unique(np.stack([np.round(fj).astype(int) - win[2], np.round(fi).astype(int) - win[0]], 1), axis=0)
sj = gpd.read_file("/share/geosat-9/sjbae/PROJECT_FLOOD/data/processed/flood_traces_basin.gpkg")
FM = "/share/geosat-11/Urban_flooding/data/04_침수흔적도(2010-2025, EPSG_5186)"
def traces(day):
    d8 = f"{day:%Y%m%d}"
    if day.year == 2022:
        g = sj[(pd.to_datetime(sj.start) <= day) & (pd.to_datetime(sj.end) >= day)]
    else:
        g = gpd.read_file(f"{FM}/SEL_FLOODMAP_{day.year}.shp"); g = (g.set_crs(5186) if g.crs is None else g).to_crs(5186)
        s = g.F_SAT_YMD.astype(str); e = g.F_END_YMD.astype(str).where(g.F_END_YMD.notna(), s)
        g = g[(s <= d8) & (e >= d8)]
    return g[["geometry"]].assign(geometry=lambda x: x.buffer(0))
days = list(pd.date_range("2022-08-01", "2022-08-31")) + [pd.Timestamp(d) for d in ["2023-07-11", "2023-07-30", "2024-07-18"]]
rows = []
for day in days:
    cube, miss = R.hsp_cube(R.day_stamps(day), win)
    s = cube[:, cells[:, 0], cells[:, 1]].mean(1) / 6.0             # 10분 유역평균 mm
    cs = np.concatenate([[0], np.cumsum(s)])
    r3 = max(cs[i + 18] - cs[i] for i in range(len(s) - 17)); r6 = max(cs[i + 36] - cs[i] for i in range(len(s) - 35))
    tr = traces(day)
    if len(tr):
        ov = gpd.overlay(units[[uid, "a", "geometry"]], tr, how="intersection")
        cov = (ov.area.groupby(ov[uid]).sum() / units.set_index(uid).a).fillna(0)
        nf = int((cov >= 0.10).sum())
    else:
        nf = 0
    rows.append({"date": f"{day:%Y-%m-%d}", "rain_day": round(float(s.sum()), 1), "r3h": round(float(r3), 1), "r6h": round(float(r6), 1),
                 "flooded_units": nf, "flooded_pct": round(100 * nf / len(units), 3)})
    print(rows[-1], flush=True)
pd.DataFrame(rows).to_csv(sys.argv[1], index=False)
