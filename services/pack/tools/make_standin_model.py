"""C1-a15 Pack 자리 모델(EV-001 CH-04): 학습 없이 손으로 정한 json-linear 가중치 — 라벨 0 · 학습 0.

입력 4칸 = 증상군마다 'train 알람 경계를 넘은 정도 ÷ train 표준편차'(부호 있음: 넘으면 +, 안쪽이면 −, custom/features.py).
클래스 = NONE · TWF · HDF · PWF · OSF(AI4I 유형). 증상군 → 유형: TW → TWF · CL → HDF · PW → PWF · LD → OSF. RNF는 경계로 표현할 수 없어 클래스에 없음.
logit(NONE) = 0, logit(유형) = K × 그 증상군 칸 → 경계 바로 위(칸 = 0)에서 위험도 = 1 − P(NONE) ≈ 0.5, 넘을수록 커짐.
사용자 ONNX 모델(10-27)이 오면 같은 predict@1 node에 release만 바꿔 끼운다. 판정 문턱(0.5)은 앱 사용자 설정이고, 아래 threshold_config는 기록만.
실행: .venv/bin/python tools/make_standin_model.py   (releases/models/mr-c1a15-standin-v1/ 를 다시 씀, 같은 내용이면 같은 hash)
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RID = "mr-c1a15-standin-v1"
FIELDS = ["tw_excess_std", "cl_excess_std", "pw_excess_std", "ld_excess_std"]
CLASSES = ["NONE", "TWF", "HDF", "PWF", "OSF"]
K = 6.0


def canon(v) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def main() -> None:
    weights = [[0.0] * 4] + [[K if j == i else 0.0 for j in range(4)] for i in range(4)]
    model = {"bias": [0.0] * 5, "classes": CLASSES, "features": FIELDS, "weights": weights}
    d = ROOT / "releases" / "models" / RID
    d.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(model, ensure_ascii=False, indent=1).encode("utf-8") + b"\n"
    (d / "model.json").write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    tv = {"min_probability": 0.5, "normal_classes": ["NONE"]}
    manifest = {
        "artifact": {"file": "model.json", "format": "json-linear", "sha256": sha},
        "classes": CLASSES,
        "feature_schema": {"fields": FIELDS, "version": 1,
                           "derivation": "칸 = (train 알람 경계를 넘은 값 ÷ train 표준편차), 부호 있음 — TW: (공구 마모 − 상위) · CL: (하위 − 온도 차) · "
                                         "PW: max(하위 − 출력, 출력 − 상위) · LD: (토크 − 상위). 경계 · 표준편차 = 앱 data/alarm_rules.json 사본(custom/alarm_rules.json)"},
        "files": {"model.json": sha},
        "kind": "model",
        "note": "손 가중치 자리 모델(학습 0 · 라벨 0) — 경계 규칙을 확률 모양으로 옮긴 것, ML 일반화 주장 아님. 사용자 ONNX 모델로 바꿔 끼울 자리.",
        "provenance": {"method": f"hand weights K={K}, bias 0 (NONE logit 0)", "training": "none", "labels_used": 0, "tool": "tools/make_standin_model.py"},
        "release_id": RID,
        "threshold_config": {"sha256": hashlib.sha256(canon(tv)).hexdigest(), "values": tv},
        "trusted": True,
    }
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(RID, sha[:12])


if __name__ == "__main__":
    main()
