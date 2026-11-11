// API 클라이언트(openapi-fetch) · 타입 = 서버 OpenAPI 정본에서 생성(packages/ui/src/api/schema.d.ts, 손으로 고치지 않음).
// 앱 표시(X-App)는 nginx가 붙이고 쿠키는 브라우저가 보낸다. 화면은 이 응답 값만 그린다.

import createClient from "openapi-fetch";
import type { components, paths } from "./api/schema";

export type Schemas = components["schemas"];
export type SessionView = Schemas["SessionView"];
export type SessionWithPreview = Schemas["SessionWithPreview"];
export type Home = Schemas["Home"];
export type Notifications = Schemas["Notifications"];
export type HistoryRecord = Schemas["Record"];
export type Instruction = Schemas["Instruction"];

export type Api = ReturnType<typeof createApi>;

export function createApi(baseUrl = "") {
  return createClient<paths>({ baseUrl, credentials: "same-origin" });
}

/** 오류 응답의 detail(서버가 정한 코드, 예 role_mismatch · stale_seq). */
export function errorDetail(error: unknown): string | undefined {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    return typeof d === "string" ? d : undefined;
  }
  return undefined;
}
