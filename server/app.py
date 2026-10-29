"""쉽(Ship)게 작업 — API 서버 시작 코드

설치 없이 파이썬 표준 라이브러리만으로 동작한다.
    python server/app.py            # http://localhost:8787
화면(index.html)의 '연결 설정'에서 '실제 AI 서버'를 고르고 주소에 http://localhost:8787 을 넣으면 연결된다.

동작 방식
- 검색(RAG의 R): server/data/docs/ 안의 문서를 문단 단위로 나눠, 질문과 겹치는 낱말이 많은 문단을 찾는다.
  실제 서비스에서는 이 부분을 임베딩 + 벡터 DB 검색으로 바꾼다 (retrieve 함수).
- 생성(RAG의 G): 환경 변수에 LLM 주소와 키가 있으면, 찾은 문단만 근거로 주고 답을 만들게 한다.
  키가 없으면 찾은 문단을 그대로 근거로 돌려준다. 근거를 못 찾으면 'grounded: false'(근거 없음)로 답한다.
- API 키는 이 서버에만 둔다. 화면 코드에는 절대 넣지 않는다.

환경 변수 (모두 선택)
    LLM_API_URL   OpenAI 호환 Chat Completions 주소 (예: https://api.openai.com/v1/chat/completions)
    LLM_API_KEY   해당 서비스의 API 키
    LLM_MODEL     모델 이름
    ALLOW_ORIGIN  화면 주소 (기본 *). 예: https://d-dong-2.github.io
    PORT          기본 8787
"""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS_DIR = HERE / "data" / "docs"
DEMO = json.loads((HERE / "data" / "demo.json").read_text(encoding="utf-8"))

STATE = {"reports": [], "published": set(), "registered": False}


# ---------------- 검색 (RAG의 R) ----------------
def load_passages():
    """문서마다 '## p.쪽수' 제목으로 나뉜 문단을 읽는다."""
    out = []
    for f in sorted(DOCS_DIR.glob("*.md")):
        doc = f.stem
        page = None
        for block in f.read_text(encoding="utf-8").split("\n\n"):
            m = re.match(r"##\s*p\.(\d+)", block.strip())
            if m:
                page = int(m.group(1))
                block = block.strip()[m.end():].strip()
            if block.strip():
                out.append({"doc": doc, "page": page, "text": block.strip()})
    return out


PASSAGES = load_passages()


def tokens(s):
    # 한글은 2글자 조각, 영문·숫자는 낱말 단위로 나눈다 (아주 단순한 검색용)
    s = s.lower()
    words = re.findall(r"[a-z0-9]+", s)
    han = re.sub(r"[^가-힣]", "", s)
    return set(words) | {han[i:i + 2] for i in range(len(han) - 1)}


def retrieve(question, k=2, min_overlap=2):
    q = tokens(question)
    scored = []
    for p in PASSAGES:
        ov = len(q & tokens(p["text"]))
        if ov >= min_overlap:
            scored.append((ov, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:k]]


# ---------------- 생성 (RAG의 G) ----------------
def call_llm(system, user):
    url, key, model = os.environ.get("LLM_API_URL"), os.environ.get("LLM_API_KEY"), os.environ.get("LLM_MODEL")
    if not (url and key and model):
        return None
    body = json.dumps({"model": model, "temperature": 0,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"].strip()


SYSTEM_ASK = ("너는 선박 기관실 정비를 돕는 안내원이다. 반드시 아래 '근거' 문단에 쓰인 내용만으로 짧게 답한다. "
              "근거에 답이 없으면 정확히 'NO_EVIDENCE' 한 단어만 출력한다. 추측하지 않는다.")


def answer_question(question, lang="ko"):
    hits = retrieve(question)
    if not hits:
        return {"grounded": False, "answer": None, "sources": []}
    context = "\n\n".join(f"[{h['doc']} p.{h['page']}] {h['text']}" for h in hits)
    out = call_llm(SYSTEM_ASK, f"근거:\n{context}\n\n질문: {question}\n답변 언어: {lang}")
    if out is None:  # LLM 미설정: 가장 잘 맞는 문단을 그대로 근거로 제시
        out = hits[0]["text"]
    if out.strip() == "NO_EVIDENCE":
        return {"grounded": False, "answer": None, "sources": []}
    return {"grounded": True, "answer": out, "sources": [{"doc": h["doc"], "page": h["page"]} for h in hits]}


SYSTEM_STRUCT = ("선원이 말로 남긴 결함 보고를 JSON으로 정리한다. 키: symptom(현상), cause(추정 원인), action(임시 조치). "
                 "말하지 않은 내용은 '확인 필요'라고 쓴다. JSON만 출력한다.")


def structure_report(text, equip):
    e = DEMO["equip"].get(equip, {"name": equip})
    base = {"target": f"{e['name']} (설비 코드 {equip})", "similar_count": len(DEMO["similar"].get("A-1", [])) if equip == "FU-01" else 0}
    out = call_llm(SYSTEM_STRUCT, text)
    if out:
        try:
            base.update(json.loads(re.search(r"\{.*\}", out, re.S).group(0)))
            return base
        except Exception:
            pass
    narrow = re.search(r"좁|공간|안 들어|들어가지", text)
    base.update({
        "symptom": "플랜지 볼트를 조일 때 토크렌치가 들어가지 않음" if narrow else text[:60],
        "cause": "인접 배관과의 간격 부족 (공구 공간)" if narrow else "확인 필요",
        "action": "오프셋 토크렌치 어댑터로 조임" if "어댑터" in text else "임시 조치 없음",
    })
    return base


# ---------------- 라우팅 ----------------
def guide(q):
    key = f"{q.get('equip')}|{q.get('task')}"
    lang = q.get("lang", "ko")
    pack = DEMO["guide"].get(key, {})
    diffs = list(pack.get(lang) or pack.get("ko") or [])
    if q.get("equip") == "FU-01" and q.get("task") == "overhaul" and "A-1" in STATE["published"]:
        v2 = dict(DEMO["v2diff"].get(lang) or DEMO["v2diff"]["ko"]); v2["v2"] = True
        diffs.append(v2)
    return {"diffs": diffs, "cases": DEMO["cases"].get(q.get("equip"), [])}


def dashboard():
    alerts = json.loads(json.dumps(DEMO["alerts"]))
    for r in STATE["reports"]:
        for a in alerts:
            if a["code"] == r.get("equip") and r.get("vessel_name") and r["vessel_name"] not in a["ships"]:
                a["ships"].append(r["vessel_name"])
    return {"alerts": alerts}


ROUTES_GET = {
    "/api/health": lambda: {"ok": True, "mode": "llm" if os.environ.get("LLM_API_KEY") else "retrieval-only", "passages": len(PASSAGES)},
    "/api/dashboard": dashboard,
}


def route_post(path, body):
    if path == "/api/guide":
        return guide(body)
    if path == "/api/ask":
        return answer_question(body.get("question", ""), body.get("lang", "ko"))
    if path == "/api/reports/structure":
        return structure_report(body.get("text", ""), body.get("equip", ""))
    if path == "/api/reports":
        body["vessel_name"] = DEMO["vessels"].get(body.get("vessel"), {}).get("name")
        STATE["reports"].append(body)
        return {"id": f"R-{100 + len(STATE['reports'])}", "status": "received"}
    if path == "/api/instructions/draft":
        return DEMO["drafts"][body["alert_id"]]
    if path == "/api/instructions/publish":
        STATE["published"].add(body["alert_id"])
        return {"status": "published", "revision": 2}
    if path == "/api/specs/check":
        reg = bool(body.get("registered"))
        STATE["registered"] = STATE["registered"] or reg
        items = DEMO["checks"] + ([DEMO["newCheck"]] if STATE["registered"] else [])
        return {"items": items, "letter": DEMO["letter"] if STATE["registered"] else None}
    if path == "/api/specs/send":
        return {"status": "sent"}
    return None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", os.environ.get("ALLOW_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = self.path.split("?")[0]
        m = re.match(r"^/api/alerts/([^/]+)/similar$", path)
        if m:
            return self._send(200, DEMO["similar"].get(urllib.request.unquote(m.group(1)), []))
        fn = ROUTES_GET.get(path)
        return self._send(200, fn()) if fn else self._send(404, {"error": "없는 주소입니다", "path": path})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            out = route_post(self.path.split("?")[0], body)
            return self._send(200, out) if out is not None else self._send(404, {"error": "없는 주소입니다"})
        except KeyError as e:
            return self._send(400, {"error": f"필요한 값이 없습니다: {e}"})
        except Exception as e:  # 시연용 서버: 오류 내용을 그대로 알려 준다
            return self._send(500, {"error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    print(f"쉽(Ship)게 작업 API 서버: http://localhost:{port}  (문단 {len(PASSAGES)}개 색인)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
