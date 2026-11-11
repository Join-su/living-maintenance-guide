import { useEffect, useRef, useState } from "react";
import { hhmm } from "@c1a15/ui";

/** 주기 조회(EP-C1-SYNC-001): 홈 5초 · 세션 3초. 실패하면 다음 주기에 다시 조회한다. */
export function usePoll(fn: () => Promise<void> | void, ms: number, deps: unknown[]): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        await ref.current();
      } catch {
        /* 다음 주기에 다시 */
      }
    };
    void tick();
    const id = setInterval(() => alive && void tick(), ms);
    return () => {
      alive = false;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function useClock(): string {
  const [now, setNow] = useState(() => hhmm(new Date()));
  useEffect(() => {
    const id = setInterval(() => setNow(hhmm(new Date())), 10_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

/** 새 알람 알림음. 브라우저 정책상 로그인 뒤 화면을 한 번 누른 다음부터 난다(T01 안내). */
let audio: AudioContext | null = null;
export function unlockSound(): void {
  if (audio || typeof window === "undefined" || !("AudioContext" in window)) return;
  try {
    audio = new AudioContext();
  } catch {
    audio = null;
  }
}
export function chime(): void {
  if (!audio) return;
  const o = audio.createOscillator();
  const g = audio.createGain();
  o.frequency.value = 880;
  g.gain.setValueAtTime(0.15, audio.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.4);
  o.connect(g).connect(audio.destination);
  o.start();
  o.stop(audio.currentTime + 0.4);
}
