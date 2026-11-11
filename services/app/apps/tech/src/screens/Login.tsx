// T01 정비자 로그인(R9 · AC-12): 정비자 계정만. 역할이 다르면 서버가 403 role_mismatch.

import { useState, type FormEvent } from "react";
import { errorDetail, Icon, type Api } from "@c1a15/ui";
import type { Me } from "../device";

export function Login({ api, onDone }: { api: Api; onDone: (me: Me) => void }) {
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<"role" | "bad" | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (busy || !userId || !password) return;
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
    <div className="ap-body nostep" style={{ height: 740 }}>
      <div className="lg-wrap">
        <form className="lg-card" onSubmit={submit}>
          <div className="brand">
            {/* 답장 001-zzx 아(가이드 3쪽): 큰 로고 위 · 이름 아래 */}
            <span className="logo">
              <Icon name="map" />
            </span>
            <span className="bn">정비 길잡이</span>
          </div>
          <h3>정비자 로그인</h3>
          {error === "role" ? (
            <div className="banner bad" role="alert">
              <Icon name="circle-alert" />
              <div>
                <b>정비자 계정만 로그인할 수 있습니다</b>파트장 계정은 파트장 앱(사무실 PC)에서 로그인하십시오.
              </div>
            </div>
          ) : error === "bad" ? (
            <div className="banner bad" role="alert">
              <Icon name="circle-alert" />
              <div>
                <b>ID 또는 비밀번호가 맞지 않습니다</b>사전 등록된 정비자 계정으로 다시 로그인하십시오.
              </div>
            </div>
          ) : null}
          <div className="fld">
            ID
            <input
              aria-label="ID"
              className={`inp${error === "role" ? " err" : ""}`}
              name="user_id"
              autoComplete="username"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
            />
          </div>
          <div className="fld">
            비밀번호
            <input
              aria-label="비밀번호"
              className="inp"
              type="password"
              name="password"
              autoComplete="current-password"
              onKeyDown={(e) => e.key === "Enter" && void submit()}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <span
            className={`btn lg full${busy || !userId || !password ? " dis" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => void submit()}
          >
            로그인
          </span>
        </form>
      </div>
    </div>
  );
}
