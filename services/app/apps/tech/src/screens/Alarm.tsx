// T03 알람 화면: 증상 · 계통 · 이탈 수 · 센서 표 · AI 해석(R8) · 찾은 기록 수(P-19 ③). '진단 보기'가 유일한 다음 행동.
// D-528(가이드 정비자 3): 경계 넘음 추이 그래프 · 「알람 확인 시각이 기록됩니다」 문장 뺌(센서 값은 숫자 표로).

import { hhmm, Icon, type SessionView } from "@c1a15/ui";
import { risk } from "./Home";

type Block = SessionView["alarm"];

const GROUP_ICON: Record<string, string> = { CL: "thermometer" };

/** past = T15 / alarm(지난 화면): 같은 화면, 조작은 잠김. */
export function AlarmScreen({
  s,
  onNext,
  past,
  busy,
}: {
  s: Pick<SessionView, "alarm">;
  onNext: () => void;
  past?: boolean;
  busy?: boolean; // EV-007: 알람 확인을 보내는 동안 잠김
}) {
  const a = s.alarm;
  const ml = a.alarm.origin === "ml_type" ? a.ml : null; // D-546 U5: ML 유형으로 정한 알람
  return (
    <div className={past ? "ap-body past" : "ap-body"}>
      <div className="g73">
        <div className="cd">
          <div className="alh">
            <span className="ali">
              <Icon name={GROUP_ICON[a.symptom_group] ?? "triangle-alert"} />
            </span>
            <div>
              <span className="sm">
                설비 {a.equipment_id} · {a.equipment_type} · {hhmm(a.occurred_at)} 발생
              </span>
              <h3>{a.group_name}</h3>
              <div className="tags">
                <span className="ch blue">증상군 · {a.system}</span>
                {ml ? ( // 꼴 01: 「ML 예측」(파랑 · 반짝 아이콘) · 「센서 이탈 n」(회색)
                  <>
                    <span className="ch blue mlc">
                      <Icon name="sparkles" />
                      ML 예측
                    </span>
                    <span className="ch gray">센서 이탈 {a.out_of_range_count}</span>
                  </>
                ) : a.out_of_range_count > 0 ? (
                  <span className="ch red">센서 {a.out_of_range_count}개 이탈</span>
                ) : null}
              </div>
            </div>
          </div>
          <table className="tbl">
            <tbody>
              <tr>
                <th>센서</th>
                <th>현재</th>
                <th>평소 범위</th>
                <th>상태</th>
              </tr>
              {a.sensors.map((r) => (
                <tr key={r.key} className={r.out_of_range ? "down" : ""}>
                  <td>{r.name}</td>
                  <td>
                    <b>{r.value_text}</b>
                  </td>
                  <td>{r.range_text}</td>
                  <td>
                    <span className={`ch ${r.out_of_range ? "red" : "gray"}`}>{r.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <div className="cd ai-c">
            <div className="cd-h">
              <Icon name="sparkles" />
              <b>AI 해석</b>
              {a.ai_reading.fallback ? <span className="ch fb">{a.ai_reading.label}</span> : null}
            </div>
            {ml ? ( // 꼴 01: 해석 글 자리 = 예측 유형 · 위험도 두 줄(글 0)
              <>
                <div className="mlr">
                  <span>예측 유형</span>
                  <b>{ml.type_name}</b>
                </div>
                <div className="mlr">
                  <span>위험도</span>
                  <b className="bad">{risk(ml.risk)}</b>
                </div>
              </>
            ) : (
              <p className="ai">{a.ai_reading.text}</p>
            )}
            <div className="ev">
              <Icon name="search" />
              <span>
                비슷한 과거 기록 <b>{a.similar_count}건</b>을 찾았습니다
              </span>
            </div>
            {/* 답장 001-zzx 다(가이드 5쪽): 「진단 보기」는 AI 해석 카드 안 아래 */}
            <div className="cta">
              <span className={past || busy ? "btn lg dis" : "btn lg"} role="button" tabIndex={0} onClick={past || busy ? undefined : onNext}>
                진단 보기 <Icon name="arrow-right" />
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
