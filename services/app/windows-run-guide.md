# 팀원 Windows 확인 안내(수동 검토 — 이 run에서는 '미확인', 사용자 결정 020 ⑨)

Matrix 수동 검토 '팀원 Windows 환경에서 compose up · 시연 1건 실행 확인'을 할 때 쓰는 안내다. 이 run에서는 팀원 확인을 받지 않았다.

1. Docker Desktop(WSL2 backend)과 Git을 준비한다. 저장소를 받고, 줄바꿈은 그대로 둔다(`git config core.autocrlf false`).
2. 저장소 폴더에서 `.env.example`을 `.env`로 복사한다. 비밀번호 · 토큰 네 값은 각자 무작위 값으로 채운다. 이 값은 저장소에 올리지 않는다.
3. 앱을 만든다: `npm ci` → `npm run build`(Node 20 이상).
4. 스택을 띄운다: `docker compose up -d --build --wait web`. `http://127.0.0.1:8081`(정비자) · `:8082`(파트장)가 열려야 한다.
5. 시연 1건: `docker compose exec api python -m c1a15.alarms DEMO-01`.
   - 정비자 앱(tech.kim)에서 알람 → 진단 → 잠금 → 사전 확인을 한다.
   - 4.3에서 '이상'을 고른다 → 교체 완료 → 재가동 체크 → 경계 이내 → 보고서 확정까지 간다.
   - 파트장 앱(lead.park) 승인 대기 목록에 1행이 보여야 한다.
6. 끝나면 `docker compose down -v`.

확인할 것: 화면이 맥과 같은지, 줄바꿈 · 경로 때문에 init(migration · 적재)이 실패하지 않는지. 결과(통과 · 실패와 화면 캡처)는 사용자에게 전한다.
