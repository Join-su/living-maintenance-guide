// [dev-2] 층 찾기 — 앞 · 뒤 꼬리표. 바뀐 때가 늦은 줄이 위(맨 위 = 가장 새 층 · 그 '뒤' 칸이 지금 층). 첫 상태 층은 맨 아래 줄의 '앞'.
// 승인 뒤에는 이 쿼리를 다시 돌려야 새 층이 보입니다(앞서 돌린 결과 칸은 그대로 남음).
MATCH (a:Layer)-[:PREVIOUS]->(b:Layer)
RETURN a.layer AS 뒤, b.layer AS 앞, a.swapped_at AS 바뀐_때
ORDER BY datetime(a.swapped_at) DESC;
