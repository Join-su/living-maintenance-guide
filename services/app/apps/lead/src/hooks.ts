import { useEffect, useRef } from "react";

/** 주기 조회(EP-C1-SYNC-001, 파트장 앱 10초). 화면이 바뀌면 다시 시작한다. */
export function usePoll(fn: () => Promise<void> | void, ms: number, deps: unknown[]): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    let alive = true;
    const tick = () => alive && void ref.current();
    tick();
    const id = setInterval(tick, ms);
    return () => {
      alive = false;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
