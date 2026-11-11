// 두 앱이 함께 쓰는 공통 패키지. 앱끼리 서로를 import하지 않고 이 패키지만 쓴다(EP-C1-SURFACE-001).

export type SurfaceId = "tech" | "lead";

/** 앱별 포트 · 세션 쿠키 이름(EP-C1-WEB-001 · EP-C1-AUTH-002). 서버가 같은 값으로 앱 · 역할을 대조한다. */
export const SURFACES: Record<SurfaceId, { port: number; sessionCookie: string }> = {
  tech: { port: 8081, sessionCookie: "tech_session" },
  lead: { port: 8082, sessionCookie: "lead_session" },
};

export * from "./api";
export * from "./clickGuard";
export * from "./components";
export * from "./format";
export { FinalReport } from "./FinalReport";
export { Icon } from "./icons";
