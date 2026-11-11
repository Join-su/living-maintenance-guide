# kg-test/graph — 관계표 씀(v0) · 관계 층을 그래프 DB(Neo4j Community 사본)로(D-534 · dev-2 전용)

- 원본 층은 그대로 `../data/`(앱에서는 PostgreSQL 승인된 보고서 칸)입니다. 그래프는 **관계 층**만이고, 원본에서 언제든 다시 만듭니다.
- 규칙 표(`../exceptions.csv`)는 그래프에 넣지 않습니다. 찾을 때 질의 틀(Cypher)에 값을 넣어 씁니다.
- 관계 질의 노드는 하나입니다(옵션 셋 · `../node.yaml`). 파일판(`../kg_node.py`)과 판단 함수(`decide`)가 같고, 근거 찾기만 Cypher입니다.

## 파일
| 파일 | 무엇 |
|---|---|
| `compose.yaml` | 사본 컨테이너 — `neo4j:2026.09.0-community`(digest 고정) · 프로젝트 `c1a15-kgdb` · 볼륨 `c1a15-kgdb-data` · 127.0.0.1:27474(그래프 화면) · 27687(bolt) · 사용 보고 끔 |
| `conn.py` | 연결 — 비밀번호는 `~/.c1a15-kgdb/neo4j.env`(저장소 밖 · 모드 600)에서 메모리로만 |
| `load_graph.py` | 적재기 — 원본 + 관계 정의 + 매뉴얼 지식 → 그래프(꼬리표 = 정의 version · 그 꼬리표만 지우고 다시 넣음 · `--dump`로 sha256) |
| `graph_node.py` | 그래프 질의 노드(Cypher 틀 · 칸 이름 허용 목록 · 값은 파라미터) |
| `compare.py` | 같은 100건 파일판 ↔ 그래프판 → `../results/graph_compare.md` · `.json` |
| `capture.cjs` | 그래프 화면 캡처(127.0.0.1 밖 요청 막음) → `captures/` |
| `test_graph.py` | 시험 11(사본에 시험 꼬리표로 넣고 지움 · 파일은 임시 폴더) |

## 그래프 꼴
```
(:Line) ←ON_LINE− (:Machine) ←AT− (:Event 알람 · 센서 칸 · 계절)
(:Event) −CONFIRMED_CAUSE→ (:Cause 매뉴얼 안/밖)        확정 원인
(:Event) −PRIOR_WORK {kind, age_days}→ (:Event)         같은 설비 앞 정비(부품 교체 · 미룸)
(:Event) −SAME_LINE_BEFORE {age_min}→ (:Event)          같은 라인 · 다른 설비 앞 알람(창 안)
(:Event) −CHECKED {result}→ (:Step) · −DID {note}→ (:Step)   그 알람에서 한 점검 · 조치
(:Step 점검) −CHECKS→ (:Cause) · (:Cause) −REMEDIED_BY→ (:Step 조치 · 호출) · (:Step) −REQUIRES→ (:Grade 권한)
(:Layer {version, defs, raw_sha256})
```

## 돌리기(저장소 뿌리에서)
```
docker compose -p c1a15-kgdb -f kg-test/graph/compose.yaml up -d
kg-test/.venv-kg/bin/python kg-test/graph/load_graph.py --dump
kg-test/.venv-kg/bin/python kg-test/graph/compare.py
kg-test/.venv-kg/bin/python -m unittest discover -s kg-test/graph -p 'test_*.py'
PLAYWRIGHT_DIR=<playwright 폴더> node kg-test/graph/capture.cjs KG-0589 kg-test/graph/captures
docker compose -p c1a15-kgdb -f kg-test/graph/compose.yaml down -v      # 내리기(볼륨까지)
```
- `kg-test/.venv-kg`: 드라이버 `neo4j==6.4.1`(+ `pytz`)만 설치. packrt · yaml은 Pack `.venv` 패키지 폴더를 읽기 경로(.pth)로 붙임.
