"""claude -p stream-json 실행 기록을 '에이전트 활동' 줄로 바꾼다 (실행 중에도 한 줄씩 읽을 수 있게 증분 방식).

  t = Trace(); for line in f: t.feed(line)  ->  t.lines = [{who, what, result, mode}]
"""
from __future__ import annotations

import json

AG = {"obs-agent": "관측", "forecast-agent": "예측"}
LABEL = {"rain": "강우·기준 도달 확인", "sewer": "하수관 수위계 확인", "status": "계산 가능 여부 확인",
         "blend": "오늘 위험 계산 (관측+예보)", "prelim": "내일 사전 예보 계산", "observed": "하루 관측으로 위험 계산",
         "forecast": "예보만으로 위험 계산", "sensitivity": "건물 민감도 조회"}


def summarize(mode: str, j: dict) -> str:
    try:
        if "error" in j:
            return "오류: " + str(j["error"])[:80]
        if mode == "rain":
            r = j["radar_basin_mean"]
            got = [f"{k} {v}" for k, v in r["reached"].items() if v]
            return (f"최근 1시간 {r['last_1h_mm']} mm · 3시간 {r['last_3h_mm']} mm · "
                    + ("기준 도달: " + ", ".join(got) if got else "기준 도달 없음"))
        if mode == "sewer":
            c = j["status_counts"]
            return " · ".join(f"{k} {v}" for k, v in c.items() if k != "정상") or "모두 정상"
        if mode == "status":
            b, p = j["blend"], j.get("prelim", {})
            if b["ready"]:
                return f"결합 계산 가능 ({b.get('issue_kst')} 발령)"
            return "사전 예보 가능" if p.get("ready") else "계산 불가: " + b["reason"]
        if mode in ("blend", "prelim", "observed", "forecast"):
            k = j["ranked"]
            return (f"강우 단계 {k['stage']} → 위험 상위 {k['top_pct']:g}% {k['n_top_units']:,}필지, "
                    f"위험 분구 {k['n_flagged_regions']}곳")
        if mode == "sensitivity":
            d = [x for x in j["drains"] if x.get("n_buildings")][:3]
            return " · ".join(f"{x['drain_name']} 상위10% {x['n_top']:,}동(반지하 {x['basement_in_top']:,})" for x in d)
    except Exception:
        return ""
    return ""


class Trace:
    def __init__(self):
        self.sub, self.calls, self.lines, self.result = {}, {}, [], None

    def feed(self, line: str):
        try:
            e = json.loads(line)
        except Exception:
            return
        if e.get("type") == "result":
            self.result = e
            return
        par = e.get("parent_tool_use_id")
        who = "총괄" if not par else AG.get(self.sub.get(par, ""), "하위")
        m = e.get("message")
        cont = m.get("content") if isinstance(m, dict) else None
        for c in cont if isinstance(cont, list) else []:
            if not isinstance(c, dict):
                continue
            if e.get("type") == "assistant" and c.get("type") == "tool_use":
                inp = c.get("input", {})
                if c.get("name") in ("Agent", "Task"):
                    self.sub[c["id"]] = inp.get("subagent_type", "")
                    self.lines.append({"who": who, "what": f"{AG.get(inp.get('subagent_type'), '하위')} 에이전트에 맡김",
                                       "result": inp.get("description", "")})
                elif c.get("name") == "Bash":
                    cmd = inp.get("command", "").replace(";", " ").replace("&&", " ").split()
                    alias = {f"${x.split('=')[0]}" for x in cmd if "=" in x and x.endswith("flood_tool")}   # T=.../flood_tool; $T rain
                    alias |= {a.replace("$", "${") + "}" for a in alias} | {f'"{a}"' for a in alias}
                    k = next((i for i, x in enumerate(cmd) if (x.endswith("flood_tool") and "=" not in x) or x in alias), None)
                    if k is None or k + 1 >= len(cmd):
                        continue                                  # 도구가 아닌 명령은 활동에 넣지 않음
                    mode = cmd[k + 1]
                    lbl = LABEL.get(mode, mode)
                    if mode == "sensitivity" and "--drains" in cmd:
                        lbl += " (" + cmd[cmd.index("--drains") + 1].replace(",", "·") + ")"
                    self.calls[c["id"]] = len(self.lines)
                    self.lines.append({"who": who, "what": lbl, "result": "실행 중…", "mode": mode})
            elif e.get("type") == "user" and c.get("type") == "tool_result" and c.get("tool_use_id") in self.calls:
                i = self.calls[c["tool_use_id"]]
                txt = c.get("content")
                txt = txt if isinstance(txt, str) else "".join(x.get("text", "") for x in txt or [] if isinstance(x, dict))
                try:
                    self.lines[i]["result"] = summarize(self.lines[i]["mode"], json.JSONDecoder().raw_decode(txt.lstrip())[0])   # 첫 JSON만
                except Exception:
                    self.lines[i]["result"] = "오류" if "rror" in txt else ""
            elif e.get("type") == "assistant" and c.get("type") == "text" and par:
                first = next((l.strip("*# ") for l in c["text"].splitlines() if l.strip()), "")
                self.lines.append({"who": who, "what": "보고 완료", "result": first[:60]})


def split_answer(md: str):
    """최종 답변 -> (브리핑 본문, 제안 행동 dict). 파일 경로 줄은 뺀다."""
    act = None
    if "```actions" in md:
        blk = md.split("```actions", 1)[1].split("```", 1)[0]
        try:
            act = json.loads(blk)
        except Exception:
            act = None
        md = md.split("```actions", 1)[0] + md.split("```actions", 1)[1].split("```", 1)[1]
    body = "\n".join(l for l in md.splitlines() if "/share/" not in l and "outputs/" not in l)
    return body, act
