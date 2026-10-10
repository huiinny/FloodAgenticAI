#!/usr/bin/env bash
# 상황판 자동 시연(D)을 m14 안의 화면 없는 크롬으로 돌리며 mp4 로 녹화한다.
#   web/record/record.sh web/demo_take5.mp4 [작업폴더]
# 필요: 서버 실행 중(run_server.sh start), node + playwright(PW_DIR/node_modules), 크롬(PLAYWRIGHT_BROWSERS_PATH), ffmpeg(FFMPEG)
# 비용: 에이전트 실제 실행 5회 (약 1.5달러), 약 12분
set -euo pipefail
H="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?출력 mp4 경로}"; WD="${2:-$(mktemp -d)}"
: "${PW_DIR:?playwright 가 설치된 폴더}"; FFMPEG="${FFMPEG:-ffmpeg}"
"$H/web/run_server.sh" prep
rm -rf "$WD/f"; mkdir -p "$WD"
NODE_PATH="$PW_DIR/node_modules" node "$H/web/record/rec.js" "$WD" | tee "$WD/log.txt"   # 프레임 + idx.json
python3 "$H/web/record/frames_to_list.py" "$WD"                                           # 프레임 시각 → ffmpeg 목록
"$FFMPEG" -y -loglevel error -f concat -safe 0 -i "$WD/list.txt" \
  -vf "fps=30,scale=in_range=pc:in_color_matrix=bt601:out_range=tv:out_color_matrix=bt709,format=yuv420p" \
  -c:v libx264 -preset medium -crf 18 -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv \
  -movflags +faststart "$OUT"
echo "완료: $OUT  (프레임 폴더 $WD 는 지워도 됨)"
