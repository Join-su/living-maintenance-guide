// 파트장 4 권한 등급(가이드 39 · 40쪽): 요약 넷 → 등급표(절 · 작업 · A ~ D 글자 칸 · 처리) · 오른쪽 변경 기록 · 등급의 뜻.
// 등급 글자를 누르면 확인 창(지금 · 바꿀 등급 → 「바꾸기」). 낮춤 · 올림 경고 문구(EV-062)는 서버가 주고 확인 창 안에 그대로(D-530 답장 001-zzo 2).
// 바꾼 내용은 감사 기록에도 남는다(서버).

import { useRef, useState } from "react";
import { errorDetail, GRADE_LABEL, hhmm, type Api, type Schemas } from "@c1a15/ui";
import { Bar, Modal, SaveError } from "./Frame";

type Grades = Schemas["LeadGrades"];
type Warning = Schemas["GradeWarning"];
const LETTERS = ["A", "B", "C", "D"] as const;

export function GradesScreen({ api, data, onChanged }: { api: Api; data: Grades; onChanged: () => void }) {
  const [pending, setPending] = useState<{ section: string; title: string; from: string; to: string; warning: Warning } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const count = (g: string) => data.rows.filter((r) => r.grade === g).length;

  const pick = async (section: string, title: string, from: string, to: string) => {
    if (to === from) return;
    setError(null);
    const { data: w } = await api.GET("/api/lead/grades/{section}/warning", { params: { path: { section }, query: { grade: to } } });
    if (w) setPending({ section, title, from, to, warning: w });
  };

  const saving = useRef(false); // EV-007: 같은 순간 두 번 누름도 한 번만 보냄
  const save = async () => {
    if (!pending || saving.current) return;
    saving.current = true;
    setBusy(true);
    setError(null);
    const { data: r, error: err } = await api.POST("/api/lead/grades/{section}", {
      params: { path: { section: pending.section } },
      body: { grade: pending.to as "A" | "B" | "C" | "D", confirmed: true },
    }).finally(() => {
      saving.current = false; // 예외로 끝나도 풀림(EV-007 review)
    });
    setBusy(false);
    if (r) {
      setPending(null);
      onChanged();
    } else setError(errorDetail(err) ?? "save_failed");
  };

  return (
    <>
      <Bar title="권한 등급" date="매뉴얼 절별 권한 등급" />
      <div className="ltiles four">
        <div>
          <b>{data.total}절</b>
          <span>매뉴얼</span>
        </div>
        <div>
          <b>{count("A")}절</b>
          <span>A 단독 가능</span>
        </div>
        <div>
          <b>{count("B")}절</b>
          <span>B 확인 후</span>
        </div>
        <div>
          <b>{count("C") + count("D")}절</b>
          <span>C 승인 필요 · D 범위 밖</span>
        </div>
      </div>
      <div className="gsplit">
        <div className="lsect">
          <table className="lt">
            <tbody>
              <tr>
                <th>절</th>
                <th>작업</th>
                <th>등급</th>
                <th>처리</th>
              </tr>
              {data.rows.map((r) => (
                <tr key={r.section} data-section={r.section}>
                  <td>{r.section}</td>
                  <td>{r.title}</td>
                  <td>
                    <span className="lseg" role="radiogroup" aria-label={`${r.section} 등급`}>
                      {LETTERS.map((g) => (
                        <button
                          key={g}
                          type="button"
                          role="radio"
                          aria-checked={r.grade === g}
                          className={r.grade === g ? "on" : ""}
                          onClick={() => void pick(r.section, r.title, r.grade, g)}
                        >
                          {g}
                        </button>
                      ))}
                    </span>
                  </td>
                  <td>
                    <span className="m">{r.label}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="lsect">
          <div className="lsec">변경 기록</div>
          <div className="lact" data-part="changes">
            {data.changes.length ? (
              data.changes.map((ch, i) => (
                <div key={i}>
                  <span>{ch.at ? hhmm(ch.at) : ""}</span>
                  {ch.section} {ch.title} · {ch.before} → {ch.after} · {ch.by_name}
                </div>
              ))
            ) : (
              <div>
                <span />
                변경 기록 없음
              </div>
            )}
          </div>
          <div className="lsec">등급의 뜻</div>
          <div className="lkv">
            {LETTERS.map((g) => (
              <div key={g}>
                <span className="gl">{g}</span>
                {GRADE_LABEL[g]}
              </div>
            ))}
          </div>
        </div>
      </div>
      {pending ? (
        <Modal
          title={`${pending.section} ${pending.title} 등급을 ${pending.from} → ${pending.to}로 바꿉니까?`}
          okLabel="바꾸기"
          okOff={busy}
          onCancel={() => setPending(null)}
          onOk={() => void save()}
        >
          <table className="vt">
            <tbody>
              <tr>
                <td>지금</td>
                <td>
                  {pending.from} {GRADE_LABEL[pending.from]}
                </td>
              </tr>
              <tr>
                <td>바꿀 등급</td>
                <td>
                  {pending.to} {GRADE_LABEL[pending.to]}
                </td>
              </tr>
            </tbody>
          </table>
          <div className={`lwarn ${pending.warning.direction}`} data-part="warning">
            <b>{pending.warning.head}</b>
            {pending.warning.body}
          </div>
          <SaveError error={error} />
        </Modal>
      ) : null}
    </>
  );
}
