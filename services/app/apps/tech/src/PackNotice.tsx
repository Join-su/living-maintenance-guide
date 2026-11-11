// 판정 지연 · 실패 안내(EV-001 CH-17): 배포 서버 길(C1A15_JUDGE=deploy)에서 판정 요청이 늦거나 끝내 실패할 때만.
// 지금 화면의 안내 줄(.banner) 모양 그대로 — 문구 둘과 '다시 보내기'(같은 seq로 다시)만 더한다. 기본 길에서는 생기지 않는다.

import { errorDetail, Icon } from "@c1a15/ui";

export type PackState = "slow" | "failed" | null;
export const PACK_SLOW_MS = 3000; // 이보다 늦으면 '판정 지연'(배포 서버가 재시도 중)

/** 판정 요청 하나를 지켜본다: 늦으면 slow, 앱이 503 pack_unavailable을 돌려주면 failed, 그 밖에는 null. */
export async function trackPack<R extends { response: Response; error?: unknown }>(
  call: () => Promise<R>,
  setState: (s: PackState) => void,
): Promise<R> {
  const timer = setTimeout(() => setState("slow"), PACK_SLOW_MS);
  try {
    const r = await call();
    setState(r.response.status === 503 && errorDetail(r.error) === "pack_unavailable" ? "failed" : null);
    return r;
  } finally {
    clearTimeout(timer);
  }
}

export function PackNotice({ state, onRetry }: { state: PackState; onRetry: () => void }) {
  if (state === "slow") {
    return (
      <div className="banner warn" data-pack="slow">
        <Icon name="clock" />
        <div>
          <b>판정 지연</b>잠시 기다려 주세요.
        </div>
      </div>
    );
  }
  if (state === "failed") {
    return (
      <div className="banner bad" data-pack="failed">
        <Icon name="triangle-alert" />
        <div>
          <b>판정 서버 연결 실패</b>
          <span className="btn ghost" role="button" tabIndex={0} onClick={onRetry}>
            다시 보내기
          </span>
        </div>
      </div>
    );
  }
  return null;
}
