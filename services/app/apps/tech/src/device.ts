// 기기에 두는 값(EP-C1-SYNC-001): 로그인 응답의 사용자 표시 · 세션별 보내지 않은 입력 · 떠난 화면 · 마지막으로 받은 지시.
// 모두 API가 준 값이거나 정비자가 아직 보내지 않은 입력이다. 판정 값은 두지 않는다.

const P = "c1a15.tech.";

function read<T>(key: string): T | null {
  try {
    const v = localStorage.getItem(P + key);
    return v ? (JSON.parse(v) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    if (value === null) localStorage.removeItem(P + key);
    else localStorage.setItem(P + key, JSON.stringify(value));
  } catch {
    /* 저장 공간이 없으면 기억하지 않는다 */
  }
}

export interface Me {
  user_id: string;
  display_name: string;
}

export const device = {
  me: () => read<Me>("me"),
  setMe: (me: Me | null) => write("me", me),
  /** 세션 안에서 떠난 화면(잠금 전 알람 · 진단). 잠금 뒤 단계는 서버 값으로 정한다. */
  stage: (sid: number) => read<"alarm" | "diagnosis" | "lock">(`stage.${sid}`),
  setStage: (sid: number, s: "alarm" | "diagnosis" | "lock" | null) => write(`stage.${sid}`, s),
  draft: (sid: number) => read<Draft>(`draft.${sid}`) ?? emptyDraft(),
  setDraft: (sid: number, d: Draft | null) => write(`draft.${sid}`, d),
  /** 확인 질문 동안 뒤 화면에 보일 지시(그 직전 응답에서 받은 값). */
  lastInstruction: <T>(sid: number) => read<T>(`instr.${sid}`),
  setLastInstruction: (sid: number, v: unknown) => write(`instr.${sid}`, v),
  /** 화면을 넘긴 표시(승인 답 화면에서 '점검 시작' · 주간 처리 답에서 '보고서 작성'). 판정 값이 아니다. */
  seen: (sid: number, key: string) => read<boolean>(`seen.${sid}.${key}`) === true,
  setSeen: (sid: number, key: string) => write(`seen.${sid}.${key}`, true),
};

export interface Draft {
  section: string | null;
  value: "정상" | "이상" | "확인 불가" | null;
  memo: string;
  precheck: string;
  checked: string[];
}

export function emptyDraft(): Draft {
  return { section: null, value: null, memo: "", precheck: "", checked: [] };
}
