# [dev-2] 실시간 시연(D-570 · D-575 ~ D-583) 배포 대본

시연 8796은 `repo/worktrees/<시연 worktree>`(이 저장소 dev-2-gate · detached)에서 `config/c1a15-dev2-demo.yaml`로 돌고, 시연 자료(Pack 시연 복제 · 그래프 DB 비밀 · 작업 폴더 · 신호)는 저장소 밖 `llmopsagent-deploy/var/c1a15-dev2-demo/`에 둡니다. 경로는 모두 `common.sh`가 이 파일 자리에서 셈(값 · 키 0).

| 대본 | 하는 일 |
|---|---|
| `env.sh` | 시연 감시 env — 로그(KG 반영 대기 폴더) · 버전 전환 0 · 실제 추출 칸 · AI 키 경로(경로만 · 감시가 다시 띄울 때도 따라감) |
| `restart.sh` | r12 check → 8796 그 pid만 다시 띄움(키 경로 · 주소 환경) → /info |
| `set-tag.sh <태그>` | 시연 그래프 첫 상태 · 되돌림: 감시 끔 → 처리 표시 · 신호 옆으로(지우기 0 · 대기 폴더 파일은 처리 표시만 되살림) → ref · expect → 다시 띄움 → 감시 켬 |
| `provider.sh fake\|http` | AI 나눔 · 보고서 문장 가짜 / 실제(http면 embedding 칸 뺌 — 설정 규칙) |
| `import-pack.sh <커밋> <머리>` | 새 Pack 커밋을 시연 복제에 `<머리>-g0` · 가이드 문장 가짜 답 한 줄을 얹어 `<머리>-g1` |
| `history.sh` | 앱 시연 이력 줄 둘(approval 확인) → 그래프 이력 |
| `llm_count.py <시각>` | 8796 실제 AI 사용 셈(상한 $0.05) |

차례 예(D-583): `import-pack.sh ef9ec74 d580` → `set-tag.sh d580-g1` · 감시 켜기 `. demo/dev2/env.sh && scripts/py scripts/autodeploy.py start`. 운영 v22로 되돌림은 `orchestration/agents/deploy/handoff/d575/move-rule07.md` ③.
