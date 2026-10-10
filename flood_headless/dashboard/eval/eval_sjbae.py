"""sjbae 모델 독립 사건 검증: flood_headless observed 결과(polygon) vs 그날 침수흔적도."""
import json, sys
import numpy as np, pandas as pd, geopandas as gpd
from sklearn.metrics import roc_auc_score, average_precision_score
D = "/share/geosat-11/Urban_flooding/data/04_침수흔적도(2010-2025, EPSG_5186)"
FH = "/share/geosat-11/Urban_flooding/work/hjkim/FloodAgentic/flood_headless/outputs"
units = gpd.read_file("/share/geosat-9/sjbae/PROJECT_FLOOD/data/processed/units.gpkg").to_crs(5186)
uid = "unit_id" if "unit_id" in units.columns else units.columns[0]
units["a"] = units.area
rows = []
for day in sys.argv[1:]:
    y = int(day[:4]); d8 = day.replace("-", "")
    tr = gpd.read_file(f"{D}/SEL_FLOODMAP_{y}.shp")
    tr = (tr.set_crs(5186) if tr.crs is None else tr).to_crs(5186)
    s, e = tr.F_SAT_YMD.astype(str), tr.F_END_YMD.astype(str).where(tr.F_END_YMD.notna(), tr.F_SAT_YMD.astype(str))
    tr = tr[(s <= d8) & (e >= d8)]
    tr = tr[["geometry"]].copy(); tr["geometry"] = tr.buffer(0)
    r = pd.read_parquet(f"{FH}/{d8}/observed/polygon_risk.parquet")
    if len(tr):
        ov = gpd.overlay(units[[uid, "a", "geometry"]], tr, how="intersection")
        cov = (ov.area.groupby(ov[uid]).sum() / units.set_index(uid).a).fillna(0)
    else:
        cov = pd.Series(0.0, index=units[uid])
    lab = (cov.reindex(r[uid]).fillna(0).values >= 0.10).astype(int)
    p = r.risk.values
    n1 = lab.sum()
    row = {"date": day, "traces": len(tr), "n_units": len(r), "n_flooded": int(n1), "base_rate": round(n1 / len(r), 5),
           "n_alert(>=0.5)": int((p >= 0.5).sum()), "mean_risk": round(float(p.mean()), 4)}
    if n1 > 0:
        row["roc_auc"] = round(roc_auc_score(lab, p), 3)
        row["pr_auc"] = round(average_precision_score(lab, p), 3)
        row["pr_lift"] = round(row["pr_auc"] / row["base_rate"], 1)
        order = np.argsort(-p)
        for k in (0.03, 0.06, 0.15):
            top = order[: int(len(p) * k)]
            row[f"recall@top{k*100:g}%"] = round(lab[top].sum() / n1, 3)
        row["hit(>=0.5)"] = int(((p >= 0.5) & (lab == 1)).sum())
    rows.append(row)
out = pd.DataFrame(rows)
print(out.to_string(index=False))
out.to_csv(sys.stdout.name if False else "/tmp/claude-1033/-share-geosat-11-Urban-flooding-work-hjkim-FloodAgentic/95db6c5a-c967-5a50-a88f-61d79f52f180/scratchpad/eval_sjbae.csv", index=False)
