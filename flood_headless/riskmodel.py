"""sjbae 위험 모델 4개(LightGBM, XGBoost, CatBoost, RandomForest)로 하루 위험도를 낸다.

모델·정적 특성은 sjbae 폴더에서 읽기만 한다. sjbae 의 common.out_path 는 폴더를
만들기 때문에 import 하지 않고, 필요한 부분(s06 ensemble_predict, s05b numeric_frame,
confidence_from, p02 aggregate)을 여기 옮겨 적었다.

  risk       = 4개 확률 평균
  spread     = 4개 확률 표준편차
  confidence = exp(-std(log-odds) / 2)
"""
from __future__ import annotations

import json
import pickle

import numpy as np
import pandas as pd

import config as C


def load_bundles(scale: str) -> dict:
    d = C.POLY_MODELS if scale == "polygon" else C.PIXEL_MODELS
    out = {}
    for name in C.LEARNERS:
        with open(d / f"{name}.pkl", "rb") as fh:
            out[name] = pickle.load(fh)
    return out


def load_static(scale: str) -> pd.DataFrame:
    if scale == "polygon":
        return pd.read_parquet(C.POLY_STATIC)
    cols = ["pixel_id", "px", "py", "dem", "slope_deg", "sink_depth", "hand", "logupa", "twi",
            "tpi100", "tpi500", "dist_stream", "dsm_minus_dem", "impervious", "bld_cover_frac",
            "drain_idx", "drain_name", "gu", "sector_id", "sector_i", "sector_j"]
    return pd.read_parquet(C.PIXEL_STATIC, columns=cols)


def numeric_frame(X, cat_cols):
    present = [c for c in (cat_cols or []) if c in X.columns]
    out = X.drop(columns=present).astype("float32")
    for c in present:
        out = pd.concat([out, pd.get_dummies(X[c].astype(str), prefix=c, dtype="float32")], axis=1)
    return out


def ensemble_predict(bundle, X, medians):
    m, cols = bundle["model"], bundle["features"]
    kind = type(m).__name__
    if kind == "RandomForestClassifier":
        A = numeric_frame(X[cols], bundle["categorical"])
        A = A.reindex(columns=getattr(m, "feature_names_in_", A.columns), fill_value=0.0)
        A = A.fillna(medians.reindex(A.columns))
        return m.predict_proba(A)[:, 1].astype("float32")
    if kind == "CatBoostClassifier":
        A = X[cols].copy()
        for c in bundle["categorical"]:
            if c in A.columns:
                A[c] = A[c].astype(str).fillna("missing")
        return m.predict_proba(A)[:, 1].astype("float32")
    return m.predict_proba(X[cols])[:, 1].astype("float32")


def polygon_training_medians(rf_bundle) -> pd.Series:
    """RF 결측 대치값 = 학습 패널 중앙값 (sjbae s06 training_medians 재현). 한 번 계산해 캐시."""
    f = C.CACHE_DIR / "rf_medians_polygon.json"
    if f.exists():
        return pd.Series(json.loads(f.read_text()), dtype="float32")
    static = pd.read_parquet(C.POLY_STATIC)
    rain = pd.read_parquet(C.SJ_PROCESSED / "rain_daily_units.parquet")
    lab = pd.read_parquet(C.SJ_PROCESSED / "labels_daily.parquet")
    df = lab.merge(rain, on=["unit_id", "date"], how="left").merge(static, on="unit_id", how="left")
    rng = np.random.default_rng(42)                       # s05_train.build_panel 과 같은 표본
    keep = (df.basin_rain_mm >= 1.0) | (df.y == 1) | (rng.random(len(df)) < 0.20)
    X = df.loc[keep, rf_bundle["features"]].copy()
    for c in rf_bundle["categorical"]:
        X[c] = X[c].astype("category")
    med = numeric_frame(X, rf_bundle["categorical"]).median()
    C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({k: float(v) for k, v in med.items()}))
    return med


def confidence_from(P):
    q = np.clip(P, 1e-6, 1 - 1e-6)
    s = np.log(q / (1 - q)).std(0)
    return np.exp(-s / 2.0).astype("float32")


def classify(p):
    return pd.cut(p, bins=C.RISK_BINS, labels=C.RISK_LABELS, right=False,
                  include_lowest=True).astype(str)


def predict(scale: str, static: pd.DataFrame, rain: pd.DataFrame) -> pd.DataFrame:
    """static 과 rain(같은 index) -> 지점별 p_*, risk, spread, confidence, risk_class."""
    bundles = load_bundles(scale)
    X = pd.concat([static, rain], axis=1)
    if scale == "polygon":
        meta = json.loads(C.POLY_META.read_text())
        for c, cats in meta["categories"].items():
            X[c] = pd.Categorical(X[c], categories=cats)
    rf = bundles["randomforest"]
    # RF 결측 대치값: polygon 은 학습 패널 중앙값(sjbae s06), pixel 은 그날 프레임 중앙값(sjbae p02)
    medians = (polygon_training_medians(rf) if scale == "polygon"
               else numeric_frame(X[rf["features"]], rf["categorical"]).median())

    cols = {f"p_{n}": ensemble_predict(b, X, medians) for n, b in bundles.items()}
    P = np.vstack(list(cols.values()))
    keep = ["unit_id", "drain_code", "drain_name", "gu", "area_m2", "lc_group"] if scale == "polygon" \
        else ["pixel_id", "drain_idx", "drain_name", "gu", "sector_id", "sector_i", "sector_j"]
    out = static[[c for c in keep if c in static.columns] + ["px", "py"]].copy()
    for k, v in cols.items():
        out[k] = v
    out["risk"] = P.mean(0).astype("float32")
    out["spread"] = P.std(0).astype("float32")
    out["confidence"] = confidence_from(P)
    out["risk_class"] = classify(out["risk"])
    return out


def rollup_polygon(r: pd.DataFrame) -> pd.DataFrame:
    """배수분구별 면적가중 평균 (sjbae s06 risk_by_drainage 와 같은 정의)."""
    r = r.assign(_w=r.risk * r.area_m2, _aa=np.where(r.risk >= C.ALERT_THR, r.area_m2, 0.0))
    g = r.groupby(["drain_code", "drain_name", "gu"], dropna=False)
    out = g.agg(_w=("_w", "sum"), _a=("area_m2", "sum"),
                p95_risk=("risk", lambda s: float(np.percentile(s, 95))),
                n_units=("risk", "size"), n_alert=("risk", lambda s: int((s >= C.ALERT_THR).sum())),
                area_alert_m2=("_aa", "sum"), confidence=("confidence", "mean")).reset_index()
    out["mean_risk"] = out._w / out._a.replace(0, np.nan)
    return out.drop(columns=["_w", "_a"]).sort_values("mean_risk", ascending=False)


def rollup_pixel(r: pd.DataFrame, keys) -> pd.DataFrame:
    """500 m 섹터 / 배수분구 집계 (sjbae p02 aggregate 와 같은 정의)."""
    g = r.groupby(keys, dropna=False)
    out = g.agg(risk_mean=("risk", "mean"), risk_p90=("risk", lambda s: s.quantile(.9)),
                risk_max=("risk", "max"), alert_frac=("risk", lambda s: float((s >= C.ALERT_THR).mean())),
                confidence=("confidence", "mean"), n_pixel=("risk", "size"),
                px=("px", "mean"), py=("py", "mean")).reset_index()
    out["area_alert_km2"] = out.alert_frac * out.n_pixel * 1e-4
    return out.sort_values("risk_mean", ascending=False)


def rain_stage(r3h_basin: float):
    """3시간 최대 강우(유역평균 mm) -> (단계 이름, 표시할 상위 %). 30 mm 미만이면 표시하지 않는다."""
    for lo, name, pct in C.RAIN_STAGES:
        if r3h_basin >= lo:
            return name, pct
    return "약함", 0.0


def ranked_regions(r: pd.DataFrame, pct: float) -> tuple[pd.DataFrame, float]:
    """위험 상위 pct % 필지를 표시 대상으로 보고, 배수분구별 대상 필지 수·비중."""
    if pct <= 0:
        return r.groupby(["drain_name", "gu"], dropna=False).size().rename("n_units").reset_index().assign(
            n_top=0, top_share=0.0, flagged=False), float("nan")
    thr = float(np.percentile(r.risk, 100 - pct))
    g = r.assign(_t=r.risk >= thr).groupby(["drain_name", "gu"], dropna=False)
    out = g.agg(n_units=("risk", "size"), n_top=("_t", "sum")).reset_index()
    out["top_share"] = out.n_top / out.n_units
    out["flagged"] = out.top_share >= C.DRAIN_FLAG_SHARE
    return out.sort_values(["flagged", "n_top"], ascending=False), thr
