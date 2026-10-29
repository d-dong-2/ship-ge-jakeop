# API 규격

화면(`index.html`)은 아래 주소만 부릅니다. 같은 형식으로 응답하는 서버라면 어떤 언어·어떤 AI 서비스로 만들어도 화면을 고치지 않고 연결할 수 있습니다.

- 모든 요청과 응답은 JSON(UTF-8)입니다.
- 서버는 CORS 헤더(`Access-Control-Allow-Origin`)를 보내야 합니다.
- **AI 서비스의 API 키는 서버에만 둡니다.** 화면에는 키를 넣지 않습니다.

## 공통 값

| 이름 | 예시 | 뜻 |
| --- | --- | --- |
| `vessel`, `prev` | `blueocean`, `coralstar` | 지금 탄 배, 전에 탄 배 |
| `equip` | `FU-01` | 설비 코드 (실제 서비스에서는 SFI 코드) |
| `task` | `overhaul`, `inspect` | 작업 종류 |
| `lang` | `ko`, `en` | 안내 언어 |
| `sources[]` | `{"doc":"B사 밸브 매뉴얼","page":37,"quote":"…"}` | 근거 문서, 쪽수, 인용문(선택) |

## 1단계: 현장 안내

### `GET /api/health`
연결 확인용입니다. `{"ok": true}`를 돌려줍니다.

### `POST /api/guide`
이전 배와 달라진 점과 과거 사례를 돌려줍니다.

```json
요청 {"vessel":"blueocean","prev":"coralstar","equip":"FU-01","task":"overhaul","lang":"ko"}
응답 {"diffs":[{"t":"작업 전 불활성화(퍼징) 먼저","b":"…","src":[{"doc":"연료공급장치 매뉴얼","page":142,"quote":"…"}],"v2":false}],
      "cases":[{"ship":"MV 그린웨이브","date":"2026년 3월","t":"…","cause":"…","fix":"…"}]}
```
`v2: true`는 공무감독이 고친 정비 지침이 반영된 안내라는 뜻입니다.

### `POST /api/ask`
매뉴얼에 질문합니다. **근거 문서를 찾지 못하면 반드시 `grounded: false`로 답합니다.**

```json
요청 {"vessel":"blueocean","equip":"FU-01","question":"볼트 조임 토크는?","lang":"ko"}
응답 {"grounded":true,"answer":"…","sources":[{"doc":"B사 밸브 매뉴얼","page":37}]}
응답 {"grounded":false,"answer":null,"sources":[]}
```

## 2단계: 현장 피드백

### `POST /api/reports/structure`
말이나 글로 남긴 보고를 항목별로 정리합니다.

```json
요청 {"text":"배관 간격이 좁아서 토크렌치가 안 들어갑니다…","equip":"FU-01","vessel":"blueocean"}
응답 {"target":"연료 차단 밸브 V-434 (설비 코드 FU-01)","symptom":"…","cause":"…","action":"…","similar_count":2}
```

### `POST /api/reports`
기관장이 확인한 보고를 육상으로 보냅니다. 응답: `{"id":"R-101","status":"received"}`

## 3단계: 정비 지침 수정

| 주소 | 요청 | 응답 |
| --- | --- | --- |
| `GET /api/dashboard` | - | `{"alerts":[{"id":"A-1","type":"…","code":"FU-01","ships":["…"],"status":"반복"}]}` |
| `GET /api/alerts/{id}/similar` | - | `[{"ship":"…","date":"…","t":"…"}]` |
| `POST /api/instructions/draft` | `{"alert_id":"A-1"}` | `{"v1":"(HTML)","v2":"(HTML, <ins>/<del> 사용)","evidence":"…"}` |
| `POST /api/instructions/publish` | `{"alert_id":"A-1"}` | `{"status":"published","revision":2}` |

## 4단계: 새 배 사양 반영

| 주소 | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/specs/check` | `{"newbuild":"H-2031","registered":true}` | `{"items":[{"t":"…","note":"…","ok":false}],"letter":"(HTML) 또는 null"}` |
| `POST /api/specs/send` | `{"newbuild":"H-2031"}` | `{"status":"sent"}` |

## 실제 AI로 바꿀 곳 (server/app.py)

| 기능 | 지금 (시작 코드) | 실제 서비스 |
| --- | --- | --- |
| 문서 검색 `retrieve()` | 낱말이 겹치는 문단 찾기 | 임베딩 + 벡터 DB 검색, 설비 코드 필터 |
| 답변 생성 `call_llm()` | 환경 변수가 있으면 OpenAI 호환 API 호출, 없으면 찾은 문단을 그대로 제시 | 원하는 LLM 서비스 또는 회사 내부에 설치한 공개 모델 |
| 보고 정리 `structure_report()` | LLM이 있으면 JSON으로 정리, 없으면 규칙 | LLM + 사진 인식 |
| 달라진 점 `guide()` | 시연용 고정 데이터 | 두 배의 절차서를 설비 코드로 짝짓고 차이를 추출 (기획서 2.2 참조) |
