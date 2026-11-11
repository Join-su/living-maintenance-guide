// [dev-2] 패턴 근거 → 후보 — 설비마다 한 줄: 그 설비가 흡기 필터 교체(앱 4.3.2 = 그래프 3.3.4) → 7일 안 '같은 설비' 다음 기록에 필터 덮개 문제가 있었는지. 읽기만.
// 근거 = 사전 점검 예시 기록 + 시연 M-01 교체 기록(승인 · 반영 뒤 그래프에 들어옴). 후보 = 교체 뒤 7일 창이 지금 아직 열린 설비(M-04). 앱처럼 지금보다 앞선 기록만 셈.
// 후보 판정 = 앱과 같은 문턱(이어짐 3건 이상 · 이어짐 ≥ 문제 없음). 다른 패턴(공구 교체 · 엔코더)은 시연에서 뺌.
// :param v => '<층>'
MATCH (e:Equipment {layer: $v})-[h:HAD]->(a:Record {layer: $v})-[d:DID]->(s:Section {no: '3.3.4'})
WHERE (a.id STARTS WITH 'H-P' OR a.id = 'H-0301') AND datetime(a.ts) < datetime()
OPTIONAL MATCH (a)-[n:NEXT]->(b:Record)-[hi:HAD_ISSUE]->(i:Issue)
WHERE n.days <= 7 AND datetime(b.ts) <= datetime()
WITH e, h, a, d, s, n, b, hi, i, b IS NULL AND datetime(a.ts) + duration({days: 7}) > datetime() AS open
WITH collect({e: e, h: h, a: a, d: d, s: s, n: n, b: b, hi: hi, i: i, open: open}) AS rows
WITH rows, size([r IN rows WHERE NOT r.open AND r.b IS NOT NULL]) AS hits, size([r IN rows WHERE NOT r.open AND r.b IS NULL]) AS clean
WITH rows, hits, clean, hits >= 3 AND hits >= clean AS met
UNWIND rows AS r
WITH r, hits, clean, met,
     replace(left(toString(datetime({datetime: datetime(r.a.ts), timezone: 'Asia/Seoul'})), 10), 'T', ' ') AS day
RETURN r.e.id AS 설비,
       '흡기 필터 교체 ' + day AS 교체,
       CASE WHEN r.b IS NOT NULL THEN '있음 · ' + toString(round(r.n.days, 1)) + '일 뒤 같은 설비 ' + r.i.name + ' 문제'
            WHEN r.open THEN '아직 7일 안(창 열림)'
            ELSE '없음' END AS 덮개_문제_7일_안,
       CASE WHEN r.open THEN CASE WHEN met THEN '사전 점검 후보 · 근거 ' + (hits + clean) + '건 중 ' + hits + '건' ELSE '아직 후보 아님 · 근거 ' + (hits + clean) + '건 중 ' + hits + '건' END
            ELSE '근거' END AS 판정,
       CASE WHEN r.b IS NOT NULL THEN [r.e, r.h, r.a, r.d, r.s, r.n, r.b, r.hi, r.i]
            ELSE [r.e, r.h, r.a, r.d, r.s] END AS 그래프
ORDER BY CASE WHEN r.open THEN 2 WHEN r.b IS NULL THEN 1 ELSE 0 END, 설비;
