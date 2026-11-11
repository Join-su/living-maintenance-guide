// 정비자 14 보고서 최종본(D-528 가이드 20 · 21쪽): 확정한 보고서를 문서 한 장으로 — 파트장 검토와 같은 FinalReport(packages/ui).
// 위 「보고서로」(그 세션 보고서) · 「출력」(버튼만 · 동작 0). 값은 /api/tech/reports/{id}/final(본인 보고서만).

import { useEffect, useState } from "react";
import { FinalReport, type Api, type Schemas } from "@c1a15/ui";

type Final = Schemas["FinalReport"];

export function FinalScreen({ api, rid, onReport }: { api: Api; rid: number; onReport: (sessionId: number) => void }) {
  const [data, setData] = useState<Final | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    void api.GET("/api/tech/reports/{report_id}/final", { params: { path: { report_id: rid } } }).then((r) => {
      if (!alive) return;
      if (r.data) setData(r.data);
      else setFailed(true);
    });
    return () => {
      alive = false;
    };
  }, [api, rid]);
  return (
    <div className="ap-body nostep fbody">
      <div className="fbar">
        <span className={`btn ghost${data ? "" : " dis"}`} role="button" tabIndex={0} onClick={() => data && onReport(data.session_id)}>
          보고서로
        </span>
        <span className="sp" />
        <span className="btn" role="button" tabIndex={0} onClick={() => undefined}>
          출력
        </span>
      </div>
      {data ? <FinalReport data={data} /> : null}
      {failed ? (
        <div className="banner bad" role="alert">
          <div>
            <b>최종본을 불러오지 못했습니다</b>
          </div>
        </div>
      ) : null}
    </div>
  );
}
