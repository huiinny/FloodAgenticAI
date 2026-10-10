"""위험 지도·레이더 영상 탭용 자료 -> tabs_0808.json (읽기 전용)."""
import json, sys, base64
import numpy as np, pandas as pd, geopandas as gpd, pyproj
sys.path.insert(0, "/share/geosat-11/Urban_flooding/work/hjkim/FloodAgentic/flood_headless")
import config as C, rainfall as R

DAY = pd.Timestamp("2022-08-08")
OUT = sys.argv[1]
FH = "/share/geosat-11/Urban_flooding/work/hjkim/FloodAgentic/flood_headless/outputs/20220808"
res = {}

# ---- 배수분구 위험 (03시 예보 / 하루 종료 후 관측)
for m in ["forecast", "observed"]:
    r = pd.read_parquet(f"{FH}/{m}/region_from_polygon.parquet")
    r["w"] = r.mean_risk * r.n_units
    g = r.groupby(["drain_name", "gu"]).agg(w=("w", "sum"), n=("n_units", "sum"), p95=("p95_risk", "max"),
                                           na=("n_alert", "sum"), area=("area_alert_m2", "sum")).reset_index()
    g["mean"] = g.w / g.n
    res[m] = [{"n": a.drain_name, "gu": a.gu, "mean": round(float(a.mean), 4), "p95": round(float(a.p95), 4),
               "alert": int(a.na), "units": int(a.n), "area_km2": round(float(a.area) / 1e6, 3)} for a in g.itertuples()]

# ---- HSP 레이더 프레임 (지도 범위)
lon0, lon1, lat0, lat1 = 126.852, 126.992, 37.425, 37.540
tf = pyproj.Transformer.from_crs("EPSG:4326", pyproj.CRS.from_dict(C.RADAR_LCC), always_xy=True)
xs, ys = tf.transform([lon0, lon1, lon0, lon1], [lat0, lat0, lat1, lat1])
fi = np.array(xs) / C.RADAR_DXY + C.RADAR_I0
fj = np.array(ys) / C.RADAR_DXY + C.RADAR_J0
win = (int(np.floor(fi.min())) - 1, int(np.ceil(fi.max())) + 2, int(np.floor(fj.min())) - 1, int(np.ceil(fj.max())) + 2)
cube, miss = R.hsp_cube(R.day_stamps(DAY), win)            # (144, J, I) mm/h
ny, nx = cube.shape[1:]
q = np.clip(np.round(cube), 0, 255).astype(np.uint8)
res["radar"] = {"ny": ny, "nx": nx, "b64": base64.b64encode(q.tobytes()).decode()}
# 셀 모서리 위경도 (셀 중심 = 정수 인덱스)
ii, jj = np.meshgrid(np.arange(win[0], win[1] + 1) - 0.5, np.arange(win[2], win[3] + 1) - 0.5)
inv = pyproj.Transformer.from_crs(pyproj.CRS.from_dict(C.RADAR_LCC), "EPSG:4326", always_xy=True)
lo, la = inv.transform((ii - C.RADAR_I0) * C.RADAR_DXY, (jj - C.RADAR_J0) * C.RADAR_DXY)
res["radar"]["lon"] = np.round(lo, 5).ravel().tolist()
res["radar"]["lat"] = np.round(la, 5).ravel().tolist()

# ---- AWS 위치 (유역 안 6곳)
a = gpd.read_file("/share/geosat-11/Urban_flooding/data/03_서울시 AWS 10분자료(61개관측소)/03_서울시 AWS 10분자료(61개관측소)/00_서울시 강우량계 위치정보(EPSG_5186)/SEL_AWS_5186.shp", encoding="cp949").to_crs(4326)
want = ["관악구청", "신림펌프장", "구로구청", "동작구청", "영등포구청", "도림2"]
pts = {}
for w in want:
    hit = a[a.AWS_NM.astype(str).str.replace(" ", "") == w.replace(" ", "")]
    if len(hit) == 0:
        hit = a[a.AWS_NM.astype(str).str.contains(w[:2])]
    if len(hit):
        p = hit.geometry.iloc[0]
        pts[w] = [round(p.x, 5), round(p.y, 5)]
res["aws_pts"] = pts
json.dump(res, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
print("radar", ny, nx, "miss", miss, "max", float(cube.max()))
print("aws", pts)
print("obs top", sorted(res["observed"], key=lambda d: -d["mean"])[:3])
