// 파트장 1 로그인(가이드 29쪽 · AC-12): 파트장 계정만. 역할이 다르면 서버가 403 role_mismatch. ID · 비밀번호를 다 넣어야 버튼이 열린다.

import { useState } from "react";
import { errorDetail, type Api } from "@c1a15/ui";
import type { Me } from "../device";
import { LBtn } from "./Frame";

export function Login({ api, onDone }: { api: Api; onDone: (me: Me) => void }) {
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<"role" | "bad" | null>(null);
  const [busy, setBusy] = useState(false);
  const ready = !busy && !!userId && !!password;

  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    const { data, error: err, response } = await api.POST("/api/login", { body: { user_id: userId, password } });
    setBusy(false);
    if (data) {
      const d = data as { user_id: string; display_name: string };
      onDone({ user_id: d.user_id, display_name: d.display_name });
      return;
    }
    setError(response.status === 403 && errorDetail(err) === "role_mismatch" ? "role" : "bad");
  };

  return (
    <div className="L lg">
      <div className="lg-card">
        <div className="lg-b">
          정비 길잡이<span>파트장</span>
        </div>
        <h2>파트장 로그인</h2>
        {error ? (
          <div className="lerr" role="alert">
            <b>{error === "role" ? "파트장 계정만 로그인할 수 있습니다" : "ID 또는 비밀번호가 맞지 않습니다"}</b>
          </div>
        ) : null}
        <label htmlFor="lg-id">ID</label>
        <input
          id="lg-id"
          aria-label="ID"
          className={error === "role" ? "err" : ""}
          autoComplete="username"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
        />
        <label htmlFor="lg-pw">비밀번호</label>
        <input
          id="lg-pw"
          aria-label="비밀번호"
          type="password"
          autoComplete="current-password"
          value={password}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
          onChange={(e) => setPassword(e.target.value)}
        />
        <LBtn cls="full" label="로그인" off={!ready} onClick={() => void submit()} />
      </div>
    </div>
  );
}
