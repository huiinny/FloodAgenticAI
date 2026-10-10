"""시안 HTML 조립: console_tpl.html 에 data/ 의 자료를 넣어 dorim_console.html 을 만든다.

  python build.py
"""
import base64
import json
from pathlib import Path

H = Path(__file__).resolve().parent
t = (H / "console_tpl.html").read_text(encoding="utf-8")
d = json.loads((H / "data/dash_0808.json").read_text(encoding="utf-8"))
img = "data:image/jpeg;base64," + base64.b64encode((H / "data/terrain.jpg").read_bytes()).decode()
out = (t.replace("/*DATA*/null", json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        .replace("/*GEO*/null", (H / "data/geo.json").read_text(encoding="utf-8"))
        .replace("/*TABS*/null", (H / "data/tabs_0808.json").read_text(encoding="utf-8"))
        .replace("/*TERRAIN*/", img))
(H / "dorim_console.html").write_text(out, encoding="utf-8")
print("wrote", H / "dorim_console.html", len(out) // 1024, "KB")
