"""경로와 상수. 외부 자료(sjbae 모델, HSP, LDAPS)는 모두 읽기 전용.

이 패키지가 쓰는 곳은 flood_headless/ 아래(cache/, outputs/)뿐이다.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "outputs"            # 날짜별 결과
CACHE_DIR = HERE / "cache"            # HSP 일강우 격자 캐시

# ---- sjbae 위험 모델 (읽기 전용) --------------------------------------------
SJBAE = Path("/share/geosat-9/sjbae/PROJECT_FLOOD")
SJ_PROCESSED = SJBAE / "data" / "processed"
POLY_MODELS = SJ_PROCESSED / "models"            # 55 입력, 토지피복 폴리곤 29,816
PIXEL_MODELS = SJ_PROCESSED / "models_pixel"     # 22 입력, 10 m 픽셀 574,540
POLY_META = SJ_PROCESSED / "model_meta.json"     # 범주형 카테고리 목록
POLY_STATIC = SJ_PROCESSED / "features_static.parquet"
PIXEL_STATIC = SJ_PROCESSED / "pixel_static.parquet"
LEARNERS = ["lightgbm", "xgboost", "catboost", "randomforest"]

# 위험 등급 (sjbae s06_predict 와 동일)
RISK_BINS = [0.0, 0.20, 0.50, 0.80, 1.01]
RISK_LABELS = ["낮음 (Low)", "주의 (Watch)", "경계 (Alert)", "심각 (Severe)"]
ALERT_THR = 0.5

# 순위 + 강우 단계: 위치는 모델 위험 순위, 표시 범위(상위 %)는 그날 3시간 최대 강우(유역평균)로 정한다.
# 2022-08·2023-07·2024-07 사건의 실제 침수 필지 비율(30~60 mm 0.2~0.7 %, 160 mm 13.6 %)보다 넓게 잡아 놓치지 않는 쪽으로 정했다.
# 2023-07-30(37 mm): 1 % 면 침수 49필지 중 18, 3 % 면 35 를 잡는다.
# 60~90 mm 구간은 사례가 1건(2023-07-11, 침수 거의 없음)뿐이라 보간값이다.
DRAIN_FLAG_SHARE = 0.05   # 배수분구 판정: 분구 필지 중 표시 대상 비중이 5 % 이상이면 "위험 표시 분구"
RAIN_STAGES = [(90.0, "경보급", 15.0), (60.0, "주의보급", 6.0), (30.0, "관심", 3.0)]   # 놓치지 않는 쪽 (2026-10-09 결정)   # (r3h 하한 mm, 이름, 상위 %)

RAIN_FEATURES = ["rain_mm", "rmax10_mmhr", "r1h_max_mm", "r3h_max_mm", "r6h_max_mm", "wet10_n"]
API_FEATURES = ["api1_mm", "api3_mm", "api7_mm", "api_decay_mm"]
ANTECEDENT_DAYS = 7
API_DECAY_K = 0.9

# ---- 관측: KMA HSP 레이더 합성 (mm/h x 100, 10분, KST) ---------------------
HSP_DIR = Path("/share/geosat-5/atmos_project/HSP_kma_api")
RADAR_NX, RADAR_NY, RADAR_HEADER = 2305, 2881, 1024
RADAR_SCALE, RADAR_NO_ECHO, RADAR_NO_DATA = 100.0, -25000, -30000
RADAR_STEP_MIN = 10
RADAR_LCC = dict(proj="lcc", lat_1=30, lat_2=60, lat_0=38, lon_0=126,
                 a=6371008.77, b=6371008.77)
RADAR_I0, RADAR_J0, RADAR_DXY = 1120.0, 1680.0, 500.0

# ---- 예보: LDAPS NCPCP (1시간 누적, hNNN = 발령+N시에 끝나는 1시간) ----------
LDAPS_DIR = Path("/share/geosat-11/Urban_flooding/data/LDAPS/Source/ncpc")
LDAPS_LCC = dict(proj="lcc", lat_1=30, lat_2=60, lat_0=38, lon_0=126,
                 a=6371229.0, b=6371229.0)
LDAPS_DXY = 1500.0
LDAPS_MISSING = 9999.0
LONG_ISSUE_HOURS = (3, 9, 15, 21)   # h024 까지 있는 장기 발령 (나머지 00/06/12/18 은 h003 까지)

WORK_CRS = "EPSG:5186"


def ldaps_file(issue_kst, lead: int) -> Path:
    """issue_kst: pandas Timestamp (KST 발령 시각)."""
    return (LDAPS_DIR / f"{issue_kst:%Y}" / f"{issue_kst:%m}"
            / f"l015_ncpc_unis_{issue_kst:%Y%m%d%H}KST_h{lead:03d}.grib")


def hsp_file(stamp: str) -> Path:
    """stamp: 'YYYYMMDDHHMM' (KST)."""
    return HSP_DIR / stamp[:8] / f"RDR_CMP_HSP_PUB_{stamp}.bin.gz"
