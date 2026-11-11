// EV-007: 새 화면 직후 누름 무시(0.4초)는 component 시험에서 끔 — 막기 자체는 clickGuard 시험이 봄
import { setFreshScreenMs } from "@c1a15/ui";

setFreshScreenMs(0);
