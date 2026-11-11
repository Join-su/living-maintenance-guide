// 표시 형식만(값은 서버). 기기 시간대와 상관없이 현장 시각(KST)으로 보인다.

const TZ = "Asia/Seoul";

function parts(iso: string | Date) {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const f = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  return p as Record<"year" | "month" | "day" | "hour" | "minute", string>;
}

/** "02:14" */
export function hhmm(iso: string | Date): string {
  const p = parts(iso);
  return `${p.hour}:${p.minute}`;
}

/** "2026-09-30 22:05" */
export function dateTime(iso: string): string {
  const p = parts(iso);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** 숫자 천 단위 쉼표(서버가 준 값의 표시만). */
export function num(v: number, digits = 0): string {
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];

/** "2026-10-01 (목) 08:12"(파트장 앱 머리 시각) */
export function dateDay(iso: string): string {
  const p = parts(iso);
  const wd = new Date(`${p.year}-${p.month}-${p.day}T12:00:00+09:00`).getUTCDay(); // 현장 날짜의 정오
  return `${p.year}-${p.month}-${p.day} (${WEEKDAY[wd]}) ${p.hour}:${p.minute}`;
}

/**
 * 확정 원인 짧게(D-637 F10 · F13 — 표시만, 값은 서버 그대로): 괄호 안(절 · 근거 기록 번호)을 빼고
 * 첫 쉼표 · 마침표 앞 한 마디만, 그래도 길면 끝 쪽 max 글자 안(앞에 '…'). 긴 글은 보고서 화면에서 본다.
 */
export function shortCause(text: string | null | undefined, max = 28): string {
  const full = (text ?? "").replace(/\s+/g, " ").trim();
  if (!full) return "";
  const noParen = full.replace(/\s*[(（][^()（）]*[)）]/g, "").trim();
  const first = (noParen.split(/[,，;；]|\.\s|。/)[0] ?? "").replace(/[.。]+$/, "").trim() || noParen || full;
  const chars = [...first];
  if (chars.length <= max) return first;
  // 우리말 원인 글은 끝에 결론(예: '… 냉각 필터가 막힘')이 오므로 뒤쪽을 남기고, 잘린 앞 낱말은 뺀다.
  const cut = chars.length - (max - 1);
  let tail = chars.slice(cut).join("");
  if (chars[cut - 1] !== " " && tail.includes(" ")) tail = tail.slice(tail.indexOf(" ") + 1);
  return `…${tail.trimStart()}`;
}

type ExCheckLike = { what: string; how?: string | null; criteria?: string | null; instruction?: string | null };
type ExActionLike = { what: string; parts?: string[] | null; instruction?: string | null };

/** 예외 점검 한 줄 = 지시 문장(무엇을 · 어떻게 · 판정 기준) — Pack이 만든 문장이 있으면 그대로 · 없으면 세 칸을 이어 씀(서버 say_of와 같은 규칙). */
export function exCheckLine(c: ExCheckLike): string {
  const say = (c.instruction ?? "").trim();
  if (say) return say;
  const how = (c.how ?? "").trim();
  const crit = (c.criteria ?? "").trim();
  return [c.what, how && how !== c.what ? how : "", crit && `기준 ${crit}`].filter(Boolean).join(" · ");
}

/** 예외 조치 한 줄 = 지시 문장 · 없으면 무엇 · 부품. */
export function exActionLine(a: ExActionLike): string {
  const say = (a.instruction ?? "").trim();
  if (say) return say;
  return [a.what, a.parts?.length ? `부품 ${a.parts.join(", ")}` : ""].filter(Boolean).join(" · ");
}
