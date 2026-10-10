"""강우 특성: 관측(HSP 레이더)과 예보(LDAPS)를 sjbae 모델 입력 10개로 만든다.

sjbae s02_rainfall.py 의 정의를 따른다.
  당일 6개  rain_mm, rmax10_mmhr, r1h_max_mm, r3h_max_mm, r6h_max_mm, wet10_n
  선행 4개  api1_mm, api3_mm, api7_mm, api_decay_mm  (대상일 전날까지 1/3/7일 합, 0.9^k 가중합)
특성은 격자에서 계산한 뒤 각 지점(폴리곤 대표점, 픽셀 중심)으로 쌍선형 보간한다.

차이점(의도적):
  * 관측 강우는 sjbae 의 HSR(Z-R + AWS 보정 x1.288) 대신 HSP(직접 QPE, mm/h) 를 쓴다.
    sjbae README 기준 HSP/AWS 총량비 1.006 이라 보정 없이 쓴다.
  * 예보는 LDAPS 1시간 누적이라 10분 해상도 특성은 근사한다.
      rmax10_mmhr ~ 최대 1시간 강수(mm/h, 시간평균 강도 -> 실제 10분 최대보다 작음)
      wet10_n     ~ 6 x (1 mm 이상인 시간 수)
"""
from __future__ import annotations

import gzip
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
import pyproj

import config as C

# --------------------------------------------------------------------------- 보간
def bilinear(grid: np.ndarray, fi: np.ndarray, fj: np.ndarray) -> np.ndarray:
    """grid[j, i] 를 소수 인덱스 (fi, fj) 에서 쌍선형 보간."""
    nj, ni = grid.shape
    ci = np.clip(fi, 0, ni - 1.001)
    cj = np.clip(fj, 0, nj - 1.001)
    i0, j0 = np.floor(ci).astype(int), np.floor(cj).astype(int)
    wi, wj = ci - i0, cj - j0
    return (grid[j0, i0] * (1 - wi) * (1 - wj) + grid[j0, i0 + 1] * wi * (1 - wj)
            + grid[j0 + 1, i0] * (1 - wi) * wj + grid[j0 + 1, i0 + 1] * wi * wj).astype("float32")


def features_from_series(depth: np.ndarray, step_min: int, n_day: int) -> dict:
    """depth: (T, J, I) 구간 강수량(mm). 마지막 n_day 스텝이 대상일, 앞쪽은 이동합용 여유분.

    sjbae 와 같이 이동합 창은 '그 창이 끝나는 시각'이 대상일에 속하면 대상일 것으로 친다.
    """
    per_h = 60 // step_min
    day = depth[-n_day:]
    out = {"rain_mm": day.sum(0)}
    rate = day * per_h                                        # mm/h
    out["rmax10_mmhr"] = rate.max(0)
    cs = np.concatenate([np.zeros_like(depth[:1]), np.cumsum(depth, 0)], 0)
    T = depth.shape[0]
    for hours, name in [(1, "r1h_max_mm"), (3, "r3h_max_mm"), (6, "r6h_max_mm")]:
        w = hours * per_h
        ends = np.arange(T - n_day, T) + 1                    # 대상일 각 스텝의 창 끝
        ends = ends[ends - w >= 0]
        out[name] = (cs[ends] - cs[ends - w]).max(0)
    out["wet10_n"] = (rate >= 1.0).sum(0).astype("float32")     # 1 mm/h 이상인 스텝 수
    return {k: np.asarray(v, dtype="float32") for k, v in out.items()}


# --------------------------------------------------------------------------- HSP
def radar_index(px, py):
    tf = pyproj.Transformer.from_crs(C.WORK_CRS, pyproj.CRS.from_dict(C.RADAR_LCC), always_xy=True)
    x, y = tf.transform(np.asarray(px), np.asarray(py))
    return np.asarray(x) / C.RADAR_DXY + C.RADAR_I0, np.asarray(y) / C.RADAR_DXY + C.RADAR_J0


def radar_window(fi, fj, pad=3):
    return (int(np.floor(fi.min())) - pad, int(np.ceil(fi.max())) + pad,
            int(np.floor(fj.min())) - pad, int(np.ceil(fj.max())) + pad)


_WIN = {}


def _init(win):
    _WIN["w"] = win


def _read_hsp(stamp):
    i0, i1, j0, j1 = _WIN["w"]
    p = C.hsp_file(stamp)
    if not p.exists():
        return None
    with gzip.open(p, "rb") as f:
        raw = f.read()
    v = np.frombuffer(raw[C.RADAR_HEADER:], dtype="<i2").reshape(C.RADAR_NY, C.RADAR_NX)
    v = v[j0:j1, i0:i1].astype("float32")
    out = np.zeros(v.shape, dtype="float32")                  # no-echo, 무영역 -> 0 (sjbae 와 동일)
    wet = v > C.RADAR_NO_ECHO
    out[wet] = v[wet] / C.RADAR_SCALE
    return out


def hsp_cube(stamps, win, workers=8):
    """(T, J, I) mm/h 와 누락 파일 수. 누락 스텝은 0."""
    shape = (win[3] - win[2], win[1] - win[0])
    cube = np.zeros((len(stamps), *shape), dtype="float32")
    missing = 0
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(win,)) as ex:
        for k, arr in enumerate(ex.map(_read_hsp, stamps, chunksize=6)):
            if arr is None:
                missing += 1
            else:
                cube[k] = arr
    return cube, missing


def day_stamps(day: pd.Timestamp, start_h=0):
    return [f"{day:%Y%m%d}{h:02d}{m:02d}" for h in range(start_h, 24) for m in range(0, 60, C.RADAR_STEP_MIN)]


def hsp_daily_total(day: pd.Timestamp, win) -> tuple[np.ndarray, int]:
    """대상 KST 하루의 일강우 격자(mm). flood_headless/cache 에 캐시."""
    C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = C.CACHE_DIR / f"hsp_daily_{day:%Y%m%d}_{'_'.join(map(str, win))}.npz"
    if f.exists():
        z = np.load(f)
        return z["rain"], int(z["missing"])
    cube, miss = hsp_cube(day_stamps(day), win)
    rain = cube.sum(0) * C.RADAR_STEP_MIN / 60.0
    if day + pd.Timedelta(days=1) <= pd.Timestamp.now().normalize():   # 끝난 날만 캐시
        np.savez_compressed(f, rain=rain, missing=miss)
    return rain, miss


def _partial_day_total(d: pd.Timestamp, now: pd.Timestamp, issue: pd.Timestamp, win):
    """아직 끝나지 않은 날 d 의 일강우: 00시~now HSP 관측 + now~24시 issue 발령 예보 (사전 예보용)."""
    asof, n_obs = blend_split(d, now)
    cube, miss = hsp_cube(day_stamps(d)[:n_obs], win) if n_obs else (np.zeros((0, win[3] - win[2], win[1] - win[0]), "float32"), 0)
    total = cube.sum(0) * C.RADAR_STEP_MIN / 60.0 if n_obs else np.zeros((win[3] - win[2], win[1] - win[0]), "float32")
    plan = [(i, h) for i, h in blend_plan(d, asof, issue) if 1 <= h <= 24 and C.ldaps_file(i, h).exists()]
    per_h = 60 // C.RADAR_STEP_MIN
    for k, (i, h) in enumerate(plan):
        a, lat0, lon0, jpos = _read_ldaps(C.ldaps_file(i, h))
        a = a[::-1] if jpos != 1 else a
        g = np.clip(np.nan_to_num(_ldaps_on_radar(np.nan_to_num(a, nan=0.0), lat0, lon0, win), nan=0.0), 0, None)
        frac = (per_h - (n_obs % per_h)) / per_h if k == 0 else 1.0
        total = total + g * frac
    return total.astype("float32"), {"date": f"{d:%Y-%m-%d}", "observed_until": f"{asof:%H:%M}",
                                     "forecast_rest": f"{issue:%m-%d %H}시 발령" if plan else None, "missing_10min_files": miss}


def antecedent_grids(day: pd.Timestamp, win, now=None, issue=None):
    """대상일 전날부터 7일 HSP 관측 일강우로 선행강우 4개.
    now 가 주어지고 전날이 아직 끝나지 않았으면(사전 예보) 그날은 now 까지 관측 + 나머지 issue 예보로 채운다."""
    days = [day - pd.Timedelta(days=k) for k in range(1, C.ANTECEDENT_DAYS + 1)]
    grids, prov = [], []
    for d in days:
        if now is not None and d + pd.Timedelta(days=1) > now:
            g, p = _partial_day_total(d, now, issue, win)
            grids.append(g)
            prov.append(p)
            continue
        g, miss = hsp_daily_total(d, win)
        grids.append(g)
        prov.append({"date": f"{d:%Y-%m-%d}", "missing_10min_files": miss})
    G = np.stack(grids)                                       # [0] = 전날
    out = {"api1_mm": G[0], "api3_mm": G[:3].sum(0), "api7_mm": G.sum(0),
           "api_decay_mm": sum(C.API_DECAY_K ** (k + 1) * G[k] for k in range(len(G)))}
    return {k: v.astype("float32") for k, v in out.items()}, prov


def observed_day_grids(day: pd.Timestamp, win):
    """대상일 당일 6개 특성, HSP 관측. 이동합용으로 전날 18:10 부터 읽는다."""
    prev = day - pd.Timedelta(days=1)
    tail = day_stamps(prev, 18)[1:]                           # 18:10..23:50 = 35 스텝
    stamps = tail + day_stamps(day)
    cube, _ = hsp_cube(stamps, win)
    n_day = len(day_stamps(day))
    depth = cube * C.RADAR_STEP_MIN / 60.0
    feats = features_from_series(depth, C.RADAR_STEP_MIN, n_day)
    miss_day = sum(1 for s in day_stamps(day) if not C.hsp_file(s).exists())
    return feats, {"date": f"{day:%Y-%m-%d}", "missing_10min_files": miss_day, "expected": n_day}


# --------------------------------------------------------------------------- LDAPS
def ldaps_plan(day: pd.Timestamp):
    """대상 KST 하루 00~24시를 채우는 (발령, 리드) 24개.

    hNNN = 발령+N시에 끝나는 1시간 누적.
      전날 21시 발령 h004~h006 -> 00~03시
      당일 03시 발령 h001~h021 -> 03~24시
    """
    i21 = day - pd.Timedelta(hours=3)
    i03 = day + pd.Timedelta(hours=3)
    return [(i21, h) for h in (4, 5, 6)] + [(i03, h) for h in range(1, 22)]


def _read_ldaps(path):
    import xarray as xr
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})  # 원본 폴더에 .idx 를 쓰지 않음
    try:
        a = ds["ncpcp"].values.astype("float32")
        lat0, lon0 = float(ds.latitude[0, 0]), float(ds.longitude[0, 0])
        jpos = int(ds["ncpcp"].attrs.get("GRIB_jScansPositively", 1))
    finally:
        ds.close()
    a[a >= C.LDAPS_MISSING] = np.nan
    return a, lat0, lon0, jpos


def forecast_day_grids(day: pd.Timestamp):
    """대상일 당일 6개 특성, LDAPS. 반환: 특성 격자, 격자 원점, 출처."""
    plan = ldaps_plan(day)
    missing = [str(C.ldaps_file(i, h)) for i, h in plan if not C.ldaps_file(i, h).exists()]
    if missing:
        raise FileNotFoundError(f"LDAPS 파일 {len(missing)}개 없음 (예: {missing[0]})")
    hours, geo = [], None
    for i, h in plan:
        a, lat0, lon0, jpos = _read_ldaps(C.ldaps_file(i, h))
        if jpos != 1:
            a = a[::-1]
        hours.append(a)
        geo = geo or (lat0, lon0)
    depth = np.nan_to_num(np.stack(hours), nan=0.0)           # (24, J, I) mm/h
    feats = features_from_series(depth, 60, 24)
    feats["rmax10_mmhr"] = depth.max(0)                       # 근사: 최대 1시간 강도
    feats["wet10_n"] = (depth >= 1.0).sum(0).astype("float32") * 6.0   # 근사: 젖은 시간 x 6
    tf = pyproj.Transformer.from_crs("EPSG:4326", pyproj.CRS.from_dict(C.LDAPS_LCC), always_xy=True)
    x0, y0 = tf.transform(geo[1], geo[0])
    prov = [{"issue_kst": f"{i:%Y-%m-%d %H}시", "lead": f"h{h:03d}",
             "valid_kst": f"{(i.hour + h - 1) % 24:02d}-{(i.hour + h - 1) % 24 + 1:02d}시"}
            for i, h in plan]
    return feats, (x0, y0), prov


# --------------------------------------------------------------------------- 블렌딩
def latest_issuance(day: pd.Timestamp, now: pd.Timestamp, lag_h: float):
    """now 에 쓸 수 있는 최신 장기 발령(KST). 후보: D-1 21시, D 03/09/15/21시. 도착 = 발령 + lag."""
    lag = pd.Timedelta(hours=lag_h)
    cands = [day - pd.Timedelta(hours=3)] + [day + pd.Timedelta(hours=h) for h in C.LONG_ISSUE_HOURS]
    ok = [i for i in cands if i + lag <= now]
    return max(ok) if ok else None


def blend_split(day: pd.Timestamp, now: pd.Timestamp):
    """관측/예보 경계(10분 단위 내림). 반환: (as_of, 관측 스텝 수)."""
    asof = min(max(now.floor(f"{C.RADAR_STEP_MIN}min"), day), day + pd.Timedelta(days=1))
    return asof, int((asof - day) / pd.Timedelta(minutes=C.RADAR_STEP_MIN))


def blend_plan(day: pd.Timestamp, asof: pd.Timestamp, issue: pd.Timestamp):
    """as_of 가 속한 시간부터 23시까지 (발령, 리드). hNNN = 발령+N시에 끝나는 1시간."""
    h0 = int((asof - day) / pd.Timedelta(hours=1))
    return [(issue, int((day + pd.Timedelta(hours=h) - issue) / pd.Timedelta(hours=1)) + 1) for h in range(h0, 24)]


def _ldaps_on_radar(a, lat0, lon0, win):
    """LDAPS 격자 a 를 레이더 창(win) 셀 중심에서 쌍선형 보간."""
    i0, i1, j0, j1 = win
    ii, jj = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1))
    inv = pyproj.Transformer.from_crs(pyproj.CRS.from_dict(C.RADAR_LCC), pyproj.CRS.from_dict(C.LDAPS_LCC), always_xy=True)
    x, y = inv.transform((ii - C.RADAR_I0) * C.RADAR_DXY, (jj - C.RADAR_J0) * C.RADAR_DXY)
    tf = pyproj.Transformer.from_crs("EPSG:4326", pyproj.CRS.from_dict(C.LDAPS_LCC), always_xy=True)
    x0, y0 = tf.transform(lon0, lat0)
    return bilinear(a, (x - x0) / C.LDAPS_DXY, (y - y0) / C.LDAPS_DXY)


def blended_day_grids(day: pd.Timestamp, now: pd.Timestamp, issue: pd.Timestamp, win):
    """관측·예보 결합 당일 6개 특성 (레이더 격자).

    00:00~as_of 는 HSP 10분 관측, as_of~24:00 은 최신 LDAPS 발령 1시간 강수를 10분에 고르게 나눈다.
    예보 구간은 rmax10_mmhr 가 시간평균 강도, wet10_n 이 6 x 습윤시간이 되어 forecast 모드 근사와 같다.
    """
    asof, n_obs = blend_split(day, now)
    n_day = len(day_stamps(day))
    prev = day - pd.Timedelta(days=1)
    stamps = day_stamps(prev, 18)[1:] + day_stamps(day)[:n_obs]
    cube, _ = hsp_cube(stamps, win)
    obs = cube * C.RADAR_STEP_MIN / 60.0
    per_h = 60 // C.RADAR_STEP_MIN
    plan = blend_plan(day, asof, issue) if n_obs < n_day else []
    # 발령이 24시까지 닿지 않는 시간(리드 > 24, 03시 발령 도착 전 새벽)은 비워 둔다(0)
    missing = [str(C.ldaps_file(i, h)) for i, h in plan if h <= 24 and not C.ldaps_file(i, h).exists()]
    if missing:
        raise FileNotFoundError(f"LDAPS 파일 {len(missing)}개 없음 (예: {missing[0]})")
    empty = [f"{(issue.hour + h - 1) % 24:02d}-{(issue.hour + h - 1) % 24 + 1:02d}시" for i, h in plan if h > 24]
    shape = (win[3] - win[2], win[1] - win[0])
    fc = []
    for k, (i, h) in enumerate(plan):
        if h > 24:
            g = np.zeros(shape, "float32")
        else:
            a, lat0, lon0, jpos = _read_ldaps(C.ldaps_file(i, h))
            if jpos != 1:
                a = a[::-1]
            g = np.nan_to_num(_ldaps_on_radar(np.nan_to_num(a, nan=0.0), lat0, lon0, win), nan=0.0)
            g = np.clip(g, 0, None) / per_h
        skip = (n_obs % per_h) if k == 0 else 0               # as_of 가 정시가 아니면 첫 시간은 남은 스텝만
        fc += [g] * (per_h - skip)
    depth = np.concatenate([obs] + ([np.stack(fc)] if fc else []), 0)
    feats = features_from_series(depth, C.RADAR_STEP_MIN, n_day)
    n_tail = obs.shape[0] - n_obs
    parts = {"obs_mm": obs[n_tail:].sum(0), "fc_mm": depth[obs.shape[0]:].sum(0)}   # 관측분/예보분 합 (출처 요약용)
    miss_obs = sum(1 for s in day_stamps(day)[:n_obs] if not C.hsp_file(s).exists())
    prov = {"as_of_kst": f"{asof:%Y-%m-%d %H:%M}",
            "observed": {"source": "HSP 관측 10분", "window_kst": f"00:00-{asof:%H:%M}" if n_obs else "없음",
                         "steps": n_obs, "missing_10min_files": miss_obs},
            "forecast": ({"source": "LDAPS 예보 1시간 (NCPCP)", "issue_kst": f"{issue:%Y-%m-%d %H}시",
                          "window_kst": f"{asof:%H:%M}-24:00", "leads": f"h{plan[0][1]:03d}-h{min(plan[-1][1], 24):03d}",
                          "steps": len(fc), "empty_hours": empty} if plan else None)}
    return feats, prov, parts


def prelim_day_grids(day: pd.Timestamp, issue: pd.Timestamp, win):
    """사전 예보: 대상일(내일) 00~24시를 전날 발령 하나로 채운다. h024 를 넘는 시간은 비워 둔다(0)."""
    per_h = 60 // C.RADAR_STEP_MIN
    shape = (win[3] - win[2], win[1] - win[0])
    hours, used, empty = [], [], []
    for h in range(24):
        lead = int((day + pd.Timedelta(hours=h) - issue) / pd.Timedelta(hours=1)) + 1
        p = C.ldaps_file(issue, lead)
        if 1 <= lead <= 24 and p.exists():
            a, lat0, lon0, jpos = _read_ldaps(p)
            a = a[::-1] if jpos != 1 else a
            g = np.clip(np.nan_to_num(_ldaps_on_radar(np.nan_to_num(a, nan=0.0), lat0, lon0, win), nan=0.0), 0, None)
            used.append(lead)
        else:
            g = np.zeros(shape, "float32")
            empty.append(f"{h:02d}-{h + 1:02d}시")
        hours += [g / per_h] * per_h
    feats = features_from_series(np.stack(hours), C.RADAR_STEP_MIN, 24 * per_h)
    prov = {"source": "LDAPS 예보만 (사전 예보)", "issue_kst": f"{issue:%Y-%m-%d %H}시",
            "leads": f"h{min(used):03d}-h{max(used):03d}" if used else None, "empty_hours": empty}
    return feats, prov


def ldaps_index(px, py, origin):
    tf = pyproj.Transformer.from_crs(C.WORK_CRS, pyproj.CRS.from_dict(C.LDAPS_LCC), always_xy=True)
    x, y = tf.transform(np.asarray(px), np.asarray(py))
    return (np.asarray(x) - origin[0]) / C.LDAPS_DXY, (np.asarray(y) - origin[1]) / C.LDAPS_DXY


# --------------------------------------------------------------------------- 조립
def build_rain_table(day: pd.Timestamp, mode: str, points: dict[str, pd.DataFrame], now=None, issue=None):
    """points: {"polygon": df(px,py), "pixel": df(px,py)} -> {scale: DataFrame(10 특성)}, 출처.

    mode = "observed": 당일 6개 HSP 관측, 선행 4개 HSP 관측
    mode = "forecast": 당일 6개 LDAPS 예보, 선행 4개 HSP 관측 (대상일 이전 날짜만)
    mode = "blend":    당일 6개 = HSP 관측(00시~now) + 최신 LDAPS 발령(now~24시), 선행 4개 HSP 관측
    """
    allx = np.concatenate([p["px"].values for p in points.values()])
    ally = np.concatenate([p["py"].values for p in points.values()])
    fi, fj = radar_index(allx, ally)
    win = radar_window(fi, fj)

    api, api_prov = antecedent_grids(day, win, now=now if mode == "prelim" else None, issue=issue)
    prov = {"antecedent_source": "HSP 관측 (대상일 전 7일)", "antecedent_days": api_prov}
    if mode == "observed":
        today, p = observed_day_grids(day, win)
        prov.update(same_day_source="HSP 관측", same_day=p)
        ldaps_origin = None
    elif mode == "forecast":
        today, ldaps_origin, p = forecast_day_grids(day)
        prov.update(same_day_source="LDAPS 예보 (NCPCP)", same_day=p,
                    approximations=["rmax10_mmhr = 최대 1시간 강수(mm/h)", "wet10_n = 6 x (1 mm 이상 시간 수)"])
    elif mode == "prelim":
        today, p = prelim_day_grids(day, issue, win)
        prov.update(same_day_source="LDAPS 예보만 (사전 예보)", same_day=p,
                    approximations=["rmax10_mmhr = 1시간 평균 강도, wet10_n = 6 x 습윤시간", "발령이 닿지 않는 시간은 0 으로 둠"])
        ldaps_origin = None
    elif mode == "blend":
        today, p, parts = blended_day_grids(day, now, issue, win)
        prov.update(same_day_source="HSP 관측 + LDAPS 예보 결합", same_day=p,
                    approximations=["예보 구간만: rmax10_mmhr = 1시간 평균 강도, wet10_n = 6 x 습윤시간"])
        ldaps_origin = None
    else:
        raise ValueError(mode)

    tables = {}
    for scale, pts in points.items():
        ri, rj = radar_index(pts["px"].values, pts["py"].values)
        ri, rj = ri - win[0], rj - win[2]
        df = pd.DataFrame(index=pts.index)
        if mode in ("observed", "blend", "prelim"):
            for k, g in today.items():
                df[k] = bilinear(g, ri, rj)
        else:
            li, lj = ldaps_index(pts["px"].values, pts["py"].values, ldaps_origin)
            for k, g in today.items():
                df[k] = bilinear(g, li, lj)
        for k, g in api.items():
            df[k] = bilinear(g, ri, rj)
        tables[scale] = df[C.RAIN_FEATURES + C.API_FEATURES]
        if mode == "blend" and scale == next(iter(points)):     # 유역 평균 관측분·예보분 (첫 번째 scale 지점 기준)
            for k, g in parts.items():
                v = bilinear(g, ri, rj)
                prov["same_day"][k] = {"mean": round(float(v.mean()), 2), "max": round(float(v.max()), 2)}
    return tables, prov
