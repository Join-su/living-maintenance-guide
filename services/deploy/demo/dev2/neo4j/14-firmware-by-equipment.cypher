// [dev-2] 펌웨어 패턴 근거 → 후보(가이드 ⑥ · 승인 뒤 상태) — 설비마다 한 줄: 컨트롤러 펌웨어 업데이트(매뉴얼 7.1 = 그래프 절 $fw '3.5.1' · 예시 줄) → 7일 안 '같은 설비' 기록에서 냉각 설정값 확인(앱 4.6 = 그래프 $chk '2.3.6')이 이상(냉각 설정 오류)이었는지. 읽기만.
// 짝짓기는 이력끼리(Record-CHECKED 이상-Section · 정비자 글 0 · 예시 줄은 원인 칸 없음 → 확정 원인 말고 점검 결과로 셈). 이어짐 쪽은 이력 번호로 거르지 않음 → 방금 승인한 M-02 보고(kg-inbox로 실린 새 이력)도 셈. 지금보다 앞선 기록만 · 문턱 = 앱과 같음(이어짐 3건 이상 · 이어짐 ≥ 이상 없음).
// :param v => '<층>' · :param fw => '3.5.1' · :param chk => '2.3.6'
MATCH (e:Equipment {layer: $v})-[h:HAD]->(a:Record {layer: $v})-[d:DID]->(s:Section {layer: $v, no: $fw})
WHERE a.id STARTS WITH 'H-P' AND datetime(a.ts) < datetime()   // 7.1(그래프 3.5.1 전기 계통 작업)은 다른 전기 작업 이력도 씀 → 업데이트 쪽은 사전 점검 예시 줄(H-P)만 · 이어짐 쪽은 번호로 거르지 않음(방금 승인한 M-02 보고 셈)
OPTIONAL MATCH (e)-[h2:HAD]->(b:Record {layer: $v})-[c:CHECKED {result: '이상'}]->(k:Section {layer: $v, no: $chk})
WHERE datetime(a.ts) < datetime(b.ts) <= datetime(a.ts) + duration({days: 7}) AND datetime(b.ts) <= datetime()
WITH e, h, a, d, s, h2, b, c, k ORDER BY b.ts
WITH e, h, a, d, s, collect({h2: h2, b: b, c: c, k: k})[0] AS f
WITH e, h, a, d, s, f.h2 AS h2, f.b AS b, f.c AS c, f.k AS k
WITH e, h, a, d, s, h2, b, c, k, b IS NULL AND datetime(a.ts) + duration({days: 7}) > datetime() AS open
WITH collect({e: e, h: h, a: a, d: d, s: s, h2: h2, b: b, c: c, k: k, open: open}) AS rows
WITH rows, size([r IN rows WHERE NOT r.open AND r.b IS NOT NULL]) AS hits, size([r IN rows WHERE NOT r.open AND r.b IS NULL]) AS clean
WITH rows, hits, clean, hits >= 3 AND hits >= clean AS met
UNWIND rows AS r
WITH r, hits, clean, met, replace(left(toString(datetime({datetime: datetime(r.a.ts), timezone: 'Asia/Seoul'})), 10), 'T', ' ') AS day
RETURN r.e.id AS 설비,
       '컨트롤러 펌웨어 업데이트 ' + day AS 업데이트,
       CASE WHEN r.b IS NOT NULL THEN '있음 · ' + toString(round(duration.inSeconds(datetime(r.a.ts), datetime(r.b.ts)).seconds / 86400.0, 1)) + '일 뒤 같은 설비 냉각 설정 오류(' + r.k.name + ' 이상)'
            WHEN r.open THEN '아직 7일 안(창 열림 · ' + toString(round(duration.inSeconds(datetime(r.a.ts), datetime()).seconds / 86400.0, 1)) + '일 지남)'
            ELSE '없음' END AS 설정_오류_7일_안,
       CASE WHEN r.open THEN CASE WHEN met THEN '사전 점검 후보 · 같은 조건 ' + (hits + clean) + '건 중 ' + hits + '건' ELSE '아직 후보 아님 · 같은 조건 ' + (hits + clean) + '건 중 ' + hits + '건' END
            ELSE '근거' END AS 판정,
       CASE WHEN r.b IS NOT NULL THEN [r.e, r.h, r.a, r.d, r.s, r.h2, r.b, r.c, r.k] ELSE [r.e, r.h, r.a, r.d, r.s] END AS 그래프
ORDER BY CASE WHEN r.open THEN 2 WHEN r.b IS NULL THEN 1 ELSE 0 END, 설비;
