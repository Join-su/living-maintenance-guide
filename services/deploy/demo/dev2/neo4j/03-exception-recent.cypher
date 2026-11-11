// [dev-2] 예외 근거 — M-01 · M-03 · M-02의 흡기 필터 교체(앱 4.3.2 = 그래프 3.3.4) 기록이 예외 관계(ExRel -WHEN_RECENT-> 3.3.4)에 이어지는지. 읽기만.
// 규칙 기간 = WHEN_RECENT.days(그래프 관계 값). 기준 시각 = 마지막 교체 뒤 그 설비의 가장 최근(이번) '온도 차 저하' 알람 기록, 없으면 지금(아직 알람 전).
// 기간 안이면 경로(예외 관계 → 3.3.4 ← 교체 기록 ← 설비) · 밖이면 예외 관계 → 3.3.4와 설비 노드가 따로(설비 쪽 선 없음). Table 칸 넷 + 그래프.
// :param v => '<층>'
MATCH (x:ExRel {layer: $v})-[w:WHEN_RECENT]->(s:Section {layer: $v})
WITH x, w, s, datetime() AS now
UNWIND ['M-01', 'M-03', 'M-02'] AS mid
MATCH (e:Equipment {layer: $v, id: mid})
OPTIONAL MATCH (e)-[:HAD]->(r:Record {layer: $v})-[:DID]->(s)
WHERE datetime(r.ts) <= now
WITH x, w, s, now, mid, e, r ORDER BY r.ts DESC
WITH x, w, s, now, mid, e, collect(r)[0] AS r
OPTIONAL MATCH (e)-[:HAD]->(al:Record {layer: $v})-[:ALARM_IN]->(:Group {name: '온도 차 저하'})
WHERE r IS NOT NULL AND datetime(al.ts) > datetime(r.ts) AND datetime(al.ts) <= now
WITH x, w, s, now, mid, e, r, max(datetime(al.ts)) AS alarm
WITH x, w, s, mid, e, r, alarm, coalesce(alarm, now) AS at
WITH x, w, s, mid, e, r, alarm,
     CASE WHEN r IS NULL THEN null ELSE round(duration.inSeconds(datetime(r.ts), at).seconds / 86400.0, 1) END AS days
WITH x, w, s, mid, e, r, alarm, days, days IS NOT NULL AND days <= w.days AS inside
OPTIONAL MATCH p = (x)-[w]->(s)<-[:DID]-(r)<-[:HAD]-(e)
WHERE inside
RETURN mid AS 기계,
       CASE WHEN r IS NULL THEN '없음' ELSE '흡기 필터 교체 ' + replace(left(toString(datetime({datetime: datetime(r.ts), timezone: 'Asia/Seoul'})), 16), 'T', ' ') END AS 마지막_교체,
       CASE WHEN r IS NULL THEN '-' ELSE toString(days) + '일 전' + CASE WHEN alarm IS NULL THEN '(지금 기준 · 알람 전)' ELSE '(가장 최근 알람 ' + replace(left(toString(datetime({datetime: alarm, timezone: 'Asia/Seoul'})), 16), 'T', ' ') + ' 기준)' END END AS 알람_때,
       CASE WHEN inside THEN '예 · 최근 ' + w.days + '일 안' ELSE '아니오 · 최근 ' + w.days + '일 밖' END AS 규칙_기간,
       CASE WHEN p IS NULL THEN [x, w, s, e] ELSE [p] END AS 그래프
ORDER BY CASE mid WHEN 'M-01' THEN 1 WHEN 'M-03' THEN 2 ELSE 3 END;
