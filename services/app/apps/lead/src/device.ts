// 기기에 두는 값: 로그인 응답의 사용자 표시만(판정 값 · 비밀번호는 두지 않음).

const KEY = "c1a15.lead.me";

export interface Me {
  user_id: string;
  display_name: string;
}

export const device = {
  me(): Me | null {
    try {
      const v = localStorage.getItem(KEY);
      return v ? (JSON.parse(v) as Me) : null;
    } catch {
      return null;
    }
  },
  setMe(me: Me | null): void {
    try {
      if (me) localStorage.setItem(KEY, JSON.stringify(me));
      else localStorage.removeItem(KEY);
    } catch {
      /* 저장 공간이 없으면 기억하지 않는다 */
    }
  },
};
