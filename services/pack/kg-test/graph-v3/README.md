# graph-v3 — 관계표 없는 매뉴얼 + 정비 이력 → 지식그래프(D-536 ② · ③)

| 파일 | 무엇 |
|---|---|
| `build_v3.py` | ② 그래프 만들기 — MG-MM 3.0(`manual-v3/`, 절 = 노드) + 앱 정비 이력(읽기만) → 그래프 DB 사본 꼬리표 `mm3-v2`(D-543: 절 노드에 시작 전 체크리스트 칸 · Pack 판정 지식을 그래프 하나에서). `mm3-v1`(칸 없음)은 나란히 남김. 점검 ↔ 원인 ↔ 조치 연결은 이력에서만 셈. `번호-대응.csv`는 이력의 옛 절 번호를 바꾸는 데만 씀(그래프에 0) |
| `check_v3.py` | ③ 알람별 확인 — 숨긴 정답(MG-MM 2.0 부록 A)과 대조. 정답은 이 파일만 읽음. 화면 · `대조.json`은 개수만, 짝 글은 사용자용 문서에만 |
| `capture_v3.cjs` | 대표 알람 둘레 그래프 그림 — 그래프 칸만 크게(그래프 DB 화면 · 127.0.0.1 밖 요청 막음) |
| `noise.py` | ③ 보강 1 — 잡음 섞인 이력 사본(씨앗 고정 · 0 · 5 · 10 · 20 · 30%)으로 같은 대조 → `results/잡음.json/md` |
| `export_release.py` | ④ 그래프 꼬리표 → Pack이 읽는 그래프 release 파일(`pack/templates/graph/current.json` 하나 · D-545) |
| `loader.py` | D-543 ② 기본 반영 길 — 적재기: 처음 이력 + KG 반영 대기 폴더(앱 승인 한 건 = 파일 하나 · `kind kg_record`) → 원본 그대로 합침 → 새 꼬리표 `<꼴 버전>-<원본 sha 8자리>`(같은 원본 = 같은 버전) → release 파일. 꼴이 틀린 승인 기록 파일는 빼고 목록 · 새 버전을 current.json에 씀(들이는 것은 받아 넣기 길) · D-545 `--nodes <llmops-2 추출기 출력>` = 예외 절 · 예외 관계 줄(꼴 검사 · 분 채움 · 그래프에 예외 절 · ExRel 노드 → release `exception_relations`) |
| `nodes-example.json` | `--nodes` 꼴 예(꼴 정본 handoff/d545/graph-shape.md) — 맞는 줄 하나 · 틀린 줄 둘(시험용) |
| `doc_pdf.cjs` | 사용자용 문서 HTML → PDF |
| `test_loader.py` | 시험 5 — 앱 승인 기록 파일 → 새 버전 · 같은 원본 같은 버전(처음 이력만 = mm3-v2 관계) · 틀린 승인 기록 파일 빼고 목록 · Pack이 새 release로 판정 지식을 만듦 · --nodes 예외 줄 |
| `test_v3.py` | 시험 8 — 다시 만들기 같음 · 만드는 길은 2.0 매뉴얼을 열지 않음 · 대응 표 그래프에 0 · 근거 건수 = 원본 · 모르는 절 · 증상군 거절 · 대조 셈 빠짐/틀림 |
| `results/` | `대조.json` · `대표-알람.json` · `알람별-확인.html/pdf`(사용자용) · `alarm-*.png` |

```
H=<앱 저장소>/data/history/maintenance_history.jsonl
kg-test/.venv-kg/bin/python -B kg-test/graph-v3/build_v3.py --history $H --dump
kg-test/.venv-kg/bin/python -B kg-test/graph-v3/check_v3.py --gold <앱 저장소>/data/manuals/MG-MM.md
KG3_HISTORY=$H kg-test/.venv-kg/bin/python -B -m unittest discover -s kg-test/graph-v3 -p 'test_*.py'
```

## 예외 → 조건 절 선(D-592)

최근 정비 조건이 있는 예외 관계는 그래프 DB에 선이 하나 더 있습니다.

- `(ExRel)-[:WHEN_RECENT {days, app_no}]->(Section)` — `days` = 며칠 안, `app_no` = 앱 번호(예 4.3.2).
- 절 노드 `no`는 그래프 번호(MG-MM 3.0)입니다. 앱 4.3.2는 그래프 3.3.4입니다.
- 선은 그래프 DB에만 있습니다. release 파일과 판정은 그대로 `when.recent`를 읽습니다.
- 이 선이 생기기 전에 적재한 꼬리표는 같은 내용을 다시 받으면 선만 채웁니다.

Neo4j 화면에서 보기(꼬리표 = release 파일 `layer`):

```
MATCH p = (e:ExRel)-[:WHEN_RECENT|GUIDES]->(:Section) WHERE e.layer = '<꼬리표>' RETURN p
```

## 사전 점검 패턴 그림(D-619)

건수는 앱 서버가 셉니다. 그래프는 그림만 맡습니다. 판정과 release 파일은 이 칸을 읽지 않습니다.

- `precheck_examples.py`: 시연 예시 이력 25줄을 만듭니다. 후보 설비는 M-04이고 시연 네 기기에는 넣지 않습니다.
- `Record.demo`: 이력 줄의 표시 칸. 예시 줄은 `D-619`, 시연 줄 H-0301 ~ H-0303은 `D-570`입니다.
- 덮개 선: `(Record)-[:HAD_ISSUE]->(:Issue {name: '필터 덮개 고정'})`. 이력 줄 `exception_record.text`에 '덮개'가 있으면 이어집니다. 승인 보고서도 같습니다.

패턴 A 근거 보기(`$v` = 꼬리표):

```
MATCH p = (e:Equipment {layer: $v})-[:HAD]->(a:Record {layer: $v})-[:DID]->(:Section {no: '3.3.4'})
WHERE a.demo = 'D-619' OR a.id = 'H-0301'
OPTIONAL MATCH q = (a)-[n:NEXT]->(b:Record)-[:HAD_ISSUE]->(i)
WHERE n.days <= 7
RETURN p, q
```
