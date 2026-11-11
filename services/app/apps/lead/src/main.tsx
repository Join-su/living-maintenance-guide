import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@c1a15/ui/styles/guide-a.css";
import "@c1a15/ui/styles/direction-guide.css";
import "@c1a15/ui/styles/final-report.css";
import "./app.css";
import { App } from "./App";

// 파트장 앱(노트북 브라우저 1280 × 800, D-528 최종안). 공통 토큰은 packages/ui/src/styles, 파트장 꼴은 app.css.
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
