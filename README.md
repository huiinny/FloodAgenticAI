# FloodAgenticAI — 도림천 침수 예보관 에이전트

서울 도림천 유역(57.5 km²)의 하루 침수 위험을 상황실 예보관의 시점에서 판단하는 에이전트다. 레이더·AWS·하수관 수위계 관측과 수치예보(LDAPS)를 합쳐 위험 분구를 고르고, 침수예보·침수경보 단계와 현장 확인 지점을 **제안**한다. 발령 결정은 예보관이 승인한다.

- 총괄 에이전트가 **관측 에이전트**(실제로 내린 비, 서울시 침수 예·경보 기준, 하수관 만관)와 **예측 에이전트**(관측 + 최신 예보로 계산한 오늘 위험, 건물 정적 위험지도)에게 일을 나누고, 두 보고를 합쳐 브리핑과 제안 행동을 쓴다.
- 새 예보 도착, 기준 첫 도달, 하수관 첫 만관 같은 사건에서 스스로 깨어나고, 예보관은 언제든 자연어로 질문할 수 있다.
- 실행은 Claude Code 헤드리스(`claude -p`, 기본 모델 sonnet)라 API 키가 필요 없다.

## 폴더

| 경로 | 내용 |
|---|---|
| `flood_headless/` | 에이전트 본체: 도구(`flood_tool.py`, `evidence.py`, `rainfall.py`, `riskmodel.py`), 프롬프트(`prompts/`), 실행(`ask.sh`) |
| `flood_headless/web/` | 실시간 상황판 서버(가상 시계, 사건 트리거, 실행 카드, 승인), 자동 시연, 녹화 도구 |
| `flood_headless/dashboard/` | 상황판 템플릿과 2022-08-08 재현 자료, 정적 상황판(`dorim_console.html`) |
| `features/building_features.parquet` | 건물 특성 (건물 민감도 조회에 사용) |
| `results/building_risk.gpkg` | 건물 정적 위험지도 (비와 무관하게 잘 잠기는 건물, 2010·2011 사건 검증) |

레이더(HSP), LDAPS, AWS, 하수관 수위계, sjbae 위험 모델은 m14의 `/share` 경로를 직접 읽는다. 그래서 m14에서 실행한다.

## 실행 (m14)

```bash
cd flood_headless
./ask.sh --now "2022-08-08 20:40" "신대방 쪽 지금 어때? 앞으로는?"   # 질문 한 번
bin/flood_tool blend --date 2022-08-08                              # LLM 없이 도구만

web/run_server.sh start      # 상황판: http://localhost:8765 (VS Code 포트 포워딩)
web/run_server.sh prep       # 시연 처음 상태 (03:55, 일지 비움)
```

학내망의 다른 사람은 `http://<m14 주소>:8765`로 볼 수 있다(보기 전용, 질문은 횟수 제한).

## 문서

- `flood_headless/DESIGN_NOTES.md`: 설계 노트 — 두 에이전트의 역할, 오케스트레이션, 예보관 시점, 트리거, 강우 창, 검증 탭의 뜻
- `flood_headless/README.md`: 도구·모드·에이전트 구성 상세
- `flood_headless/web/RECORDING.md`: 시연 영상 순서와 대본
