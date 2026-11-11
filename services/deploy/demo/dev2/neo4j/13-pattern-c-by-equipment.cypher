// [dev-2] 패턴 C 근거 → 후보 — 설비마다 한 줄: 그 설비가 엔코더 커넥터 재체결(앱 3.4.2 = 그래프 3.2.2) → 7일 안 '같은 설비' 기록에서 회전 조건 설정 확인(앱 3.3 = 그래프 2.2.3)이 이상(회전 조건 설정 오류)이었는지. 읽기만.
// 근거 = 사전 점검 예시 기록. 후보 = 재체결 뒤 7일 창이 지금 아직 열린 설비(M-04). 앱처럼 지금보다 앞선 기록만 셈 · 후보 판정 = 앱과 같은 문턱(이어짐 3건 이상 · 이어짐 ≥ 문제 없음).
// :param v => '<층>'
MATCH (e:Equipment {layer: $v})-[h:HAD]->(a:Record {layer: $v})-[d:DID]->(s:Section {no: '3.2.2'})
WHERE a.id STARTS WITH 'H-P' AND datetime(a.ts) < datetime()
OPTIONAL MATCH (e)-[h2:HAD]->(b:Record {layer: $v})-[c:CHECKED {result: '이상'}]->(t:Section {no: '2.2.3'})
WHERE b.id STARTS WITH 'H-P' AND datetime(a.ts) < datetime(b.ts) <= datetime(a.ts) + duration({days: 7}) AND datetime(b.ts) <= datetime()
WITH e, h, a, d, s, h2, b, c, t ORDER BY b.ts
WITH e, h, a, d, s, collect({h2: h2, b: b, c: c, t: t})[0] AS f
WITH e, h, a, d, s, f.h2 AS h2, f.b AS b, f.c AS c, f.t AS t
WITH e, h, a, d, s, h2, b, c, t, b IS NULL AND datetime(a.ts) + duration({days: 7}) > datetime() AS open
WITH collect({e: e, h: h, a: a, d: d, s: s, h2: h2, b: b, c: c, t: t, open: open}) AS rows
WITH rows, size([r IN rows WHERE NOT r.open AND r.b IS NOT NULL]) AS hits, size([r IN rows WHERE NOT r.open AND r.b IS NULL]) AS clean
WITH rows, hits, clean, hits >= 3 AND hits >= clean AS met
UNWIND rows AS r
WITH r, hits, clean, met, replace(left(toString(datetime({datetime: datetime(r.a.ts), timezone: 'Asia/Seoul'})), 10), 'T', ' ') AS day
RETURN r.e.id AS 설비,
       '엔코더 커넥터 재체결 ' + day AS 재체결,
       CASE WHEN r.b IS NOT NULL THEN '있음 · ' + toString(round(duration.inSeconds(datetime(r.a.ts), datetime(r.b.ts)).seconds / 86400.0, 1)) + '일 뒤 같은 설비 회전 조건 설정 오류(' + r.t.name + ' 이상)'
            WHEN r.open THEN '아직 7일 안(창 열림)'
            ELSE '없음' END AS 회전_조건_7일_안,
       CASE WHEN r.open THEN CASE WHEN met THEN '사전 점검 후보 · 근거 ' + (hits + clean) + '건 중 ' + hits + '건' ELSE '아직 후보 아님 · 근거 ' + (hits + clean) + '건 중 ' + hits + '건' END
            ELSE '근거' END AS 판정,
       CASE WHEN r.b IS NOT NULL THEN [r.e, r.h, r.a, r.d, r.s, r.h2, r.b, r.c, r.t] ELSE [r.e, r.h, r.a, r.d, r.s] END AS 그래프
ORDER BY CASE WHEN r.open THEN 2 WHEN r.b IS NULL THEN 1 ELSE 0 END, 설비;
