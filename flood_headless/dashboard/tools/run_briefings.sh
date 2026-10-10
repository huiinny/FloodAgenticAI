#!/usr/bin/env bash
# 시연 장면마다 감시 장치가 에이전트를 자동 실행한 것처럼 ask.sh 를 돌려 dashboard/briefings/ 에 저장한다.
# 직전 장면의 제안(actions)을 다음 장면에 [직전 제안] 으로 넘겨 에이전트가 이어서 판단하게 한다.
set -u
HERE="$(cd "$(dirname "$0")/../.." && pwd)"; OUT="$HERE/dashboard/briefings"; mkdir -p "$OUT"
export FLOOD_LDAPS_LAG_H=1
PREV=""
run() { local now="$1" key="$2" q="$3"
  [ -n "$PREV" ] && q="$q
[직전 제안] $PREV (예보관 미승인)"
  "$HERE/ask.sh" --now "$now" "$q" > "$OUT/$key.log" 2>&1
  f=$(ls -t "$HERE"/outputs/queries/*.md | head -1); b="${f%.md}"
  cp "$f" "$OUT/$key.md"; cp "$b.json" "$OUT/$key.json"; cp "$b.jsonl" "$OUT/$key.jsonl"
  a=$(awk '/^```actions/{f=1;next}/^```/{f=0}f' "$OUT/$key.md" | tr -d '\n')
  [ -n "$a" ] && PREV="$(echo "$a" | jq -r '"\(.level) (\(.change)): \(.level_reason)"' 2>/dev/null || echo "")"
  echo "$key done $(date +%T) prev=[$PREV]"; }
run "2022-08-08 04:00" 0400 "[자동 실행] 03시 발표 수치예보가 도착했다. 오늘 상황을 판단하라."
run "2022-08-08 13:10" 1310 "[자동 실행] 침수예보 기준 도달 감지: 레이더 유역평균 20분 20 mm 이상. 상황을 판단하고 조치를 제안하라."
run "2022-08-08 20:40" 2040 "[자동 실행] 침수경보 기준 도달 감지: 1시간 50 mm 이상 그리고 3시간 90 mm 이상. 상황을 판단하고 조치를 제안하라."
run "2022-08-08 22:00" 2200 "[자동 실행] 21시 발표 수치예보가 도착했다. 오늘 남은 시간과 내일(8/9) 전망을 판단하라."
run "2022-08-09 00:30" final "[자동 실행] 8/8 하루가 끝났다. 어제 상황과 너의 예측·제안이 맞았는지 검증하라."
