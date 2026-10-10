# flood_headless — 도림천 하루 침수 위험 (Claude Code 헤드리스, API 키 불필요)

사용자는 자연어로 질문만 한다. 에이전트(`claude -p`, 로그인 사용)가 다음을 스스로 한다.
1. 질문에서 날짜와 관측/예보 중 무엇을 원하는지 판단한다.
2. `status` 로 지금 계산이 가능한지 확인한다.
3. 가능하면 도구를 부르고, 결과를 해석해 답한다.

```bash
./ask.sh "오늘 도림천 침수 위험 어때?"
./ask.sh --now "2022-08-08 05:00" "오늘 침수 위험 예보해줘"           # 과거 시점 재현
./ask.sh --now "2022-08-09 09:00" "어제 예보가 실제랑 얼마나 맞았어?"   # 예보·관측 둘 다 돌려 비교
bin/flood_tool status   --date 2022-08-08    # LLM 없이 도구만
bin/flood_tool blend    --date 2022-08-08    # FLOOD_NOW 기준 관측·예보 결합
bin/flood_tool prelim   --date 2022-08-09    # 밤에 내일: 21시 발령만으로 사전 예보 (21~24시 비어 있음)
bin/flood_tool forecast --date 2022-08-08
```

- 답변은 `outputs/queries/<시각>.md` 에, 도구 결과는 `outputs/<YYYYMMDD>/<mode>/` 에 저장된다. blend 는 시점마다 `blend/<HHMM>_i<MMDDHH>/` (기준 시각, 사용 발령) 에 따로 쌓인다.
- LLM 모델은 기본 `sonnet` 이다. `FLOOD_MODEL`(별칭 또는 전체 모델 ID)로 바꾼다.
- 기준 시각은 `--now`(환경변수 `FLOOD_NOW`)로 고정한다. 에이전트는 이 시각을 바꿀 수 없다.
- 계산 가능 조건은 도구가 직접 막는다.
  - 결합(blend): 하루 내내 가능. 단 새벽(03시 발령 도착 전)에는 전날 21시 발령이 21시까지만 닿아 21~24시를 비워 계산하고, 03시 발령이 오면 다시 계산한다.
  - 사전 예보(prelim): 전날 21시 발령 도착 후 자정 전까지, 내일 하루를 예보만으로.
  - 예보: 대상일 03시 발령 도착 이후 (`FLOOD_LDAPS_LAG_H` 로 도착 지연 시간 설정, 기본 0)
  - 관측: 대상일이 끝난 뒤
- 에이전트 권한은 `bin/flood_tool` 실행뿐이다. 파일 읽기·쓰기·웹은 막혀 있다. 과거 재현(--now) 때 이미 계산된 미래 결과를 읽지 못하게 하려는 것이다.

## 에이전트 구성 (claude -p, 하위 에이전트 2개)
`ask.sh` 는 총괄 에이전트(`prompts/lead.md`)를 띄우고, `--agents` 로 하위 에이전트 2개를 정의한다. 총괄은 질문을 나눠 맡기고 두 보고를 합쳐 예보관 브리핑을 쓴다.

| 에이전트 | 프롬프트 | 도구 (`bin/flood_tool`) | 하는 일 |
|---|---|---|---|
| 총괄 | `prompts/lead.md` | 하위 에이전트 호출 | 질문 해석, 관측·예측 결합, 일치·불일치 정리 |
| 관측 `obs-agent` | `prompts/obs_agent.md` | `rain`, `sewer` | 레이더·AWS 강우로 서울시 침수 예·경보와 호우특보 기준 도달, 하수관 만관·수신 끊김 |
| 예측 `forecast-agent` | `prompts/forecast_agent.md` | `status`, `blend`, `observed`, `forecast`, `sensitivity` | 블렌딩 위험 분구, 건물 정적 위험지도로 우선 확인 건물 |

- `rain`·`sewer` 는 기준 시각(FLOOD_NOW)까지 자료만 읽는다 (`evidence.py`).
- `sensitivity` 는 `results/building_risk.gpkg`(건물 정적 위험지도, 2010·2011 독립 검증)에서 분구별 위험 상위 10% 건물과 원인·반지하를 준다.
- 2022-08-08 20:40 기준 질문 1회: 약 1분, 소넷.

## 모델
sjbae `PROJECT_FLOOD` 의 4개 학습기(LightGBM, XGBoost, CatBoost, RandomForest)를 그대로 읽어 쓴다(읽기 전용).
- polygon: 29,816 필지, 55 입력 → 배수분구 집계
- pixel: 10 m 574,540 격자, 22 입력 → 500 m 섹터·배수분구 집계
- risk = 4개 평균, confidence = exp(−std(log-odds)/2)

관측과 예보는 같은 모델이고, **당일 강우 6개만** 바꿔 넣는다.

| 입력 | blend (기본) | observed | forecast |
|---|---|---|---|
| 정적 특성 (지형, 토지피복, 건물, 배수분구) | sjbae | sjbae | sjbae |
| 당일 강우 6개 (rain_mm, rmax10_mmhr, r1h/r3h/r6h_max_mm, wet10_n) | 00시~지금 HSP 관측 + 지금~24시 최신 LDAPS | HSP 관측 | LDAPS 예보 |
| 선행강우 4개 (api1/3/7_mm, api_decay_mm: 대상일 전 1~7일) | HSP 관측 | HSP 관측 | HSP 관측 |

### 위험 표시: 순위 + 강우 단계
- 고정 점수(0.5 이상 = 경계)는 다른 사건에서 크게 빗나갔다 (2023-07-30 침수 49필지 중 3곳만, 2023-07-11·2024-07-18 침수 0~3곳에 5~6천 필지 경보).
- 그래서 위치는 모델 위험 순위, 표시 범위는 그날 3시간 최대 강우(유역평균)로 정한다: 30 mm 미만 0, 관심(30~60) 3%, 주의보급(60~90) 6%, 경보급(90+) 15% (놓치지 않는 쪽) (`config.RAIN_STAGES`).
- 값은 2022-08·2023-07·2024-07 사건의 실제 침수 필지 비율로 맞췄다. 60~90 mm 는 사례 1건뿐이라 보간값이다.
- 배수분구 판정: 분구 필지 중 표시 대상이 5 % 이상이면 위험 표시 분구 (`config.DRAIN_FLAG_SHARE`).
- 요약 JSON 의 `ranked`, 결과 폴더의 `region_ranked.parquet` 에 나온다.

### 관측·예보 결합 (blend)
- 하루(KST 00~24시)는 모델 학습 단위 그대로 둔다. 지나간 시간은 HSP 10분 관측, 남은 시간은 그 시각에 쓸 수 있는 최신 장기 발령(03·09·15·21시, 도착 = 발령 + `FLOOD_LDAPS_LAG_H`)으로 채운다.
- 두 구간을 한 10분 시계열로 이어 붙인 뒤 6개 특성을 계산한다. 그래서 3·6시간 최대처럼 경계를 걸치는 창도 바르게 잡힌다.
- 예보 1시간 값은 10분 6스텝에 고르게 나눈다. 예보 구간의 rmax10_mmhr 는 시간평균 강도, wet10_n 은 6 x 습윤시간이 되어 forecast 모드 근사와 같다.
- LDAPS 는 레이더 격자(500 m) 셀 중심으로 쌍선형 보간해 관측과 같은 격자에서 합친다.
- 확인: 하루가 끝난 뒤의 blend 결과는 observed 와 같다 (2022-08-08 polygon 등급 수 일치).

예보 트리거 (대상일 D, KST). LDAPS hNNN = 발령+N시에 끝나는 1시간 누적.
- D−1 21시 발령 h004~h006 → D 00~03시
- D 03시 발령 h001~h021 → D 03~24시
- ⇒ D 03시 자료 도착 후 계산

## sjbae 원본과 다른 점
- 관측 강우는 HSR(Z–R 변환 + AWS ×1.288 보정) 대신 HSP(직접 QPE)를 쓴다. sjbae README 기준 HSP/AWS 총량비는 1.006이다.
- LDAPS 는 1시간 자료라서 두 특성을 근사한다.
  - rmax10_mmhr ≈ 최대 1시간 강수
  - wet10_n ≈ 6 × (1 mm 이상 시간 수)
- 검증: sjbae 자체 강우표를 넣었을 때 polygon 결과가 sjbae `risk_daily` 와 같다.
  - LightGBM, XGBoost는 완전 일치한다.
  - 경계 이상(0.5) 필지 수도 같다 (08-08 12,782, 08-09 9,094, 08-11 533).
  - RandomForest 는 건물·배수분구 결측이 있는 487 필지에서만 차이가 난다 (결측 대치 차이).

## 주의
- LDAPS 는 이 서버에 2022-08 한 달만 있다. HSP 는 2019-12-31 ~ 2025-08-01 이다.
- LDAPS 는 대류성 호우를 과소예보할 수 있다. 2022-08-08 03시 발령은 유역 평균 1.9 mm였고, 관측(HSP)은 299 mm였다.
- 쓰는 곳은 이 폴더의 `outputs/`, `cache/` 뿐이다. LDAPS 는 cfgrib `indexpath=''` 로 읽어 원본 폴더에 .idx 를 만들지 않는다.
- cron 에서 쓸 때는 HOME 을 설정하고, 필요하면 `CLAUDE_BIN` 으로 claude 경로를 지정한다.
