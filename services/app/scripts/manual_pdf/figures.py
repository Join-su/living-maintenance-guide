"""MG-MM PDF 그림 25(EV-005) — 직접 그린 설비 도면식 선 그림(SVG). 남의 그림 0.

- 그림 안 글자는 figures_trace.json의 이름표 글(허용 목록 항목 또는 원본 줄의 부분 글)과 번호(풍선 1..k · 범례 1..n)뿐이다.
  이름표는 차례 번호로만 꺼내 쓰고, 여기에 글을 따로 적지 않는다.
- 선 굵기 1.4 · 강조(점검 자리) = 붉은 굵은 선 · 번호 풍선 = 흰 원. 인라인 SVG라 xmlns를 쓰지 않는다.
"""

import html
import json
from pathlib import Path

TRACE = {f["fig"]: f for f in json.loads((Path(__file__).resolve().parent / "figures_trace.json").read_text(encoding="utf-8"))["figures"]}
INK, SOFT, HIL, PAPER = "#1b1f24", "#8a939e", "#c8323c", "#ffffff"
W = 460


def _t(x, y, s, anchor="start", size=9.5, weight=400, fill=INK):
    return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{fill}">{html.escape(s)}</text>'


def _g(*parts, sw=1.4, stroke=INK, fill="none", extra=""):
    return f'<g fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round" stroke-linecap="round" {extra}>' + "".join(parts) + "</g>"


def rect(x, y, w, h, r=3, **k):
    a = "".join(f' {key.replace("_", "-")}="{v}"' for key, v in k.items())
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"{a}/>'


def circ(x, y, r, **k):
    a = "".join(f' {key.replace("_", "-")}="{v}"' for key, v in k.items())
    return f'<circle cx="{x}" cy="{y}" r="{r}"{a}/>'


def line(x1, y1, x2, y2, **k):
    a = "".join(f' {key.replace("_", "-")}="{v}"' for key, v in k.items())
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"{a}/>'


def path(d, **k):
    a = "".join(f' {key.replace("_", "-")}="{v}"' for key, v in k.items())
    return f'<path d="{d}"{a}/>'


def arrow(x1, y1, x2, y2, color=INK, sw=1.2, dash=None):
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    hx1, hy1 = x2 - 7 * math.cos(ang - 0.4), y2 - 7 * math.sin(ang - 0.4)
    hx2, hy2 = x2 - 7 * math.cos(ang + 0.4), y2 - 7 * math.sin(ang + 0.4)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"{d}/>'
            f'<path d="M {hx1:.1f} {hy1:.1f} L {x2} {y2} L {hx2:.1f} {hy2:.1f}" fill="none" stroke="{color}" stroke-width="{sw}"/>')


def hatch(x, y, w, h, step=6, color=SOFT):
    return f'<g stroke="{color}" stroke-width="0.6">' + "".join(
        f'<line x1="{x + i}" y1="{y + h}" x2="{x + i + min(h, w - i)}" y2="{y + h - min(h, w - i)}"/>' for i in range(0, w, step)) + "</g>"


def balloon(n, x, y, tx=None, ty=None, r=8.5):
    lead = "" if tx is None else (f'<line x1="{x}" y1="{y}" x2="{tx}" y2="{ty}" stroke="{INK}" stroke-width="0.8"/>'
                                   f'<circle cx="{tx}" cy="{ty}" r="1.8" fill="{INK}"/>')
    return (lead + f'<circle cx="{x}" cy="{y}" r="{r}" fill="{PAPER}" stroke="{INK}" stroke-width="1.2"/>'
            + _t(x, y + 3.6, str(n), "middle", 10, 700))


class Fig:
    """그림 한 장 — 이름표는 TRACE의 차례 번호로만 놓는다(글자를 여기서 만들지 않음)."""

    def __init__(self, fid, h=240, w=W):
        self.f, self.h, self.w, self.parts, self.used = TRACE[fid], h, w, [], set()

    def label(self, i):
        self.used.add(i)
        return self.f["labels"][i]["text"]

    def draw(self, *s):
        self.parts += s
        return self

    def callout(self, i, tx, ty, side, y, x=None):
        """이름표 i를 왼쪽('l') · 오른쪽('r') 단 또는 x 자리에 두고 지시선으로 (tx, ty)를 가리킴."""
        s = self.label(i)
        if side == "l":
            x = 118 if x is None else x
            self.parts += [_t(x, y + 3, s, "end"), line(x + 4, y, tx, ty, stroke=INK, stroke_width=0.7), circ(tx, ty, 1.8, fill=INK)]
        elif side == "r":
            x = self.w - 118 if x is None else x
            self.parts += [_t(x, y + 3, s, "start"), line(x - 4, y, tx, ty, stroke=INK, stroke_width=0.7), circ(tx, ty, 1.8, fill=INK)]
        else:  # 'c' — 글 가운데, 지시선 위로
            self.parts += [_t(x, y + 3, s, "middle"), line(x, y - 8, tx, ty, stroke=INK, stroke_width=0.7), circ(tx, ty, 1.8, fill=INK)]
        return self

    def text_in(self, i, x, y, anchor="middle", size=9.5, weight=400):
        self.parts.append(_t(x, y, self.label(i), anchor, size, weight))
        return self

    def code(self, i, x, y):
        """원인 코드 꼬리표(원본 원인 표 줄의 코드)."""
        s = self.label(i)
        w = 8 + 6.2 * len(s)
        self.parts += [rect(x - w / 2, y - 10, w, 14, 2, fill=INK, stroke="none"), _t(x, y + 1, s, "middle", 8.5, 700, PAPER)]
        return self

    def balloons(self, *pts):
        assert len(pts) == self.f["balloons"], (self.f["fig"], len(pts))
        for n, p in enumerate(pts, 1):
            self.parts.append(balloon(n, *p))
        return self

    def legend(self, anchors, x0, y0, dy=17):
        """1-1 · 1-2 — 부품마다 범례 번호 풍선 + 오른쪽 범례(번호 · 이름표)."""
        for n, (ax, ay, bx, by) in enumerate(anchors, 1):
            self.parts.append(balloon(n, bx, by, ax, ay, 7.5))
            self.parts += [circ(x0, y0 + (n - 1) * dy - 3.5, 7, fill=PAPER, stroke=INK, stroke_width=1),
                           _t(x0, y0 + (n - 1) * dy, str(n), "middle", 9, 700), _t(x0 + 13, y0 + (n - 1) * dy, self.label(n - 1))]
        return self

    def svg(self):
        assert self.used == set(range(len(self.f["labels"]))), (self.f["fig"], "이름표를 다 쓰지 않음", sorted(self.used))
        return f'<svg viewBox="0 0 {self.w} {self.h}" font-family="inherit" role="img">' + "".join(self.parts) + "</svg>"


# ── 공통 부품 그림
def machine_front(ox=0, oy=0, s=1.0):
    """회전 가공 설비 정면(가상) — 몸체 · 작업 문 · 조작반 · 측면 냉각 커버 · 냉각수 탱크 · 칩 통."""
    def R(x, y, w, h, r=3, **k):
        return rect(ox + x * s, oy + y * s, w * s, h * s, r, **k)

    def L(x1, y1, x2, y2, **k):
        return line(ox + x1 * s, oy + y1 * s, ox + x2 * s, oy + y2 * s, **k)

    def C(x, y, r, **k):
        return circ(ox + x * s, oy + y * s, r * s, **k)
    return _g(
        R(40, 30, 250, 220, 6), R(60, 60, 150, 140), L(135, 60, 135, 200),
        R(68, 70, 58, 88, stroke_width=0.9), R(144, 70, 58, 88, stroke_width=0.9),
        L(126, 120, 131, 120, stroke_width=2), L(139, 120, 144, 120, stroke_width=2),
        R(222, 60, 56, 96), R(230, 68, 40, 28, stroke_width=0.9),
        C(238, 112, 4, stroke_width=0.9), C(252, 112, 4, stroke_width=0.9), C(266, 112, 4, stroke_width=0.9),
        C(250, 138, 8, stroke=HIL, stroke_width=1.6),
        R(290, 70, 44, 120), L(296, 90, 328, 90, stroke_width=0.7), L(296, 100, 328, 100, stroke_width=0.7), L(296, 110, 328, 110, stroke_width=0.7),
        R(290, 200, 70, 50), R(298, 208, 8, 34, 1, stroke_width=0.9), L(298, 222, 306, 222, stroke_width=1.6),
        R(60, 214, 110, 30, 2), L(30, 252, 370, 252))


def screen(x, y, w, h, rows=3):
    """조작반 화면(칸 줄) — 글자 없는 칸."""
    p = [rect(x, y, w, h, 4), rect(x + 6, y + 6, w - 12, h - 12, 2, stroke_width=0.9)]
    for i in range(rows):
        yy = y + 16 + i * (h - 28) / max(rows - 1, 1)
        p += [line(x + 14, yy, x + w * 0.45, yy, stroke=SOFT, stroke_width=3), rect(x + w * 0.55, yy - 5, w * 0.32, 10, 1.5, stroke_width=0.8)]
    return _g(*p)


def sheet(x, y, w, h, rows=5):
    """종이 표(공정 표준표 · 정비 이력)."""
    p = [path(f"M {x} {y} h {w - 12} l 12 12 v {h - 12} h {-w} z"), path(f"M {x + w - 12} {y} v 12 h 12", stroke_width=0.8)]
    for i in range(rows):
        yy = y + 22 + i * (h - 30) / max(rows - 1, 1)
        p.append(line(x + 8, yy, x + w - 8, yy, stroke=SOFT, stroke_width=0.8))
    p.append(line(x + w * 0.42, y + 16, x + w * 0.42, y + h - 6, stroke=SOFT, stroke_width=0.8))
    return _g(*p)


def spindle_tool(cx, top, tool_len=70, worn=False):
    """스핀들 끝 · 홀더 · 콜릿 · 공구(엔드밀) 옆모습."""
    p = [rect(cx - 34, top, 68, 40, 3), path(f"M {cx - 26} {top + 40} L {cx - 16} {top + 64} L {cx + 16} {top + 64} L {cx + 26} {top + 40}"),
         rect(cx - 14, top + 64, 28, 16, 2), rect(cx - 7, top + 80, 14, tool_len, 1.5)]
    for i in range(4):
        y = top + 92 + i * (tool_len - 18) / 4
        p.append(line(cx - 7, y, cx + 7, y + 9, stroke_width=0.8))
    return _g(*p)


def fan(cx, cy, r):
    p = [circ(cx, cy, r), circ(cx, cy, r * 0.22)]
    for k in range(5):
        import math
        a = k * 2 * math.pi / 5
        p.append(path(f"M {cx + r * 0.22 * math.cos(a):.1f} {cy + r * 0.22 * math.sin(a):.1f} Q {cx + r * 0.9 * math.cos(a + 0.5):.1f} {cy + r * 0.9 * math.sin(a + 0.5):.1f} {cx + r * 0.85 * math.cos(a + 1.0):.1f} {cy + r * 0.85 * math.sin(a + 1.0):.1f}", stroke_width=1))
    return _g(*p)


def padlock(x, y, s=1.0):
    """자물쇠(잠금 단계 표시) — 글자 0."""
    return _g(f'<path transform="translate({x},{y}) scale({s})" d="M3 10 V6 a6 6 0 0 1 12 0 V10"/>',
              f'<rect transform="translate({x},{y}) scale({s})" x="0" y="10" width="18" height="14" rx="2"/>',
              f'<circle transform="translate({x},{y}) scale({s})" cx="9" cy="17" r="1.8" fill="{INK}"/>')


def glove(x, y, s=1.0, color="#1f5aa6"):
    return (f'<path transform="translate({x},{y}) scale({s})" d="M0 26 V12 q0-3 3-3 V3 q0-3 3-3 q3 0 3 3 V2 q0-3 3-3 q3 0 3 3 v1 q0-3 3-3 q3 0 3 3 v18 '
            f'q0 7-7 7 h-6 q-6 0-6-6 z" fill="none" stroke="{color}" stroke-width="1.4" stroke-linejoin="round"/>')


# ── 아이콘(이 문서 읽는 법 · 상자) — 글자 0
def icon_warning(size=20):
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}"><path d="M12 2 L23 21 L1 21 Z" fill="#f2c200" stroke="{INK}" stroke-width="1.4" stroke-linejoin="round"/>'
            f'<rect x="11" y="8" width="2" height="7" fill="{INK}"/><rect x="11" y="16.6" width="2" height="2" fill="{INK}"/></svg>')


def icon_ppe(size=20):
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}"><circle cx="12" cy="12" r="11" fill="#1f5aa6"/>'
            '<path d="M8 17 l0 -6 q0 -1.4 1.2 -1.4 l0 -2.4 q0 -1.2 1.2 -1.2 q1.2 0 1.2 1.2 l0 -0.6 q0 -1.2 1.2 -1.2 q1.2 0 1.2 1.2 l0 1 q0 -1 1.1 -1 q1.1 0 1.1 1.2 l0 6.8 q0 2.6 -2.6 2.6 l-2.4 0 q-2.2 0 -2.2 -2.2 z" fill="#fff"/></svg>')


def icon_crit(size=20):
    return f'<svg viewBox="0 0 24 24" width="{size}" height="{size}"><rect x="2" y="5" width="20" height="14" fill="none" stroke="{INK}" stroke-width="2.4"/></svg>'


def icon_balloon(size=20):
    return f'<svg viewBox="0 0 24 24" width="{size}" height="{size}"><circle cx="12" cy="12" r="9" fill="#fff" stroke="{INK}" stroke-width="1.6"/><circle cx="12" cy="12" r="2.4" fill="{INK}"/></svg>'


def icon_highlight(size=20):
    return f'<svg viewBox="0 0 24 24" width="{size}" height="{size}"><rect x="4" y="6" width="16" height="12" rx="2" fill="none" stroke="{HIL}" stroke-width="2.6"/></svg>'


# ── 그림 25
def f_cover():
    return f'<svg viewBox="0 0 400 270" font-family="inherit" role="img">{machine_front()}</svg>'


def f_1_1():
    f = Fig("1-1", 270, 560).draw(machine_front())
    anchors = [(100, 130, 100, 18), (225, 140, 205, 18), (250, 82, 300, 22), (250, 138, 252, 180), (312, 150, 360, 140), (345, 238, 380, 200), (302, 225, 266, 262), (115, 229, 48, 262)]
    return f.legend(anchors, 420, 40).svg()


def f_1_2():
    f = Fig("1-2", 270, 560)
    f.draw(_g(rect(40, 30, 250, 220, 6), line(30, 252, 370, 252),
              rect(70, 80, 110, 150, 3), circ(170, 155, 3), line(80, 92, 170, 92, stroke_width=0.7),   # 전기함
              rect(84, 110, 40, 28, 2), line(92, 124, 116, 124, stroke_width=3),                      # 주 차단기(손잡이)
              rect(136, 110, 30, 22, 2), circ(151, 121, 6, stroke_width=1),                          # 재기동 시험 스위치
              rect(84, 156, 70, 52, 2), *[line(92, 166 + i * 8, 146, 166 + i * 8, stroke_width=0.6) for i in range(5)],  # 인버터
              circ(230, 70, 26), circ(230, 70, 18, stroke_width=0.8), rect(250, 92, 18, 12, 2, stroke=HIL, stroke_width=1.6),  # 뒤쪽 커버 · 엔코더 커넥터
              path("M 268 98 C 300 98 300 140 330 140", stroke_width=1.1),
              line(290, 200, 350, 200, stroke_width=2.2), path("M 312 192 l 16 16 m 0 -16 l -16 16", stroke_width=1.2), line(320, 190, 320, 180)))  # 공압 밸브
    anchors = [(104, 124, 104, 30), (151, 121, 160, 30), (75, 220, 30, 220), (119, 182, 60, 262), (230, 48, 230, 14), (259, 98, 300, 70), (320, 200, 350, 230)]
    return f.legend(anchors, 420, 40).svg()


def f_1_3():
    """F.1 에너지 차단 여섯 단계 — 원본 목록 차례로 상자 여섯(번호 0)."""
    f = Fig("1-3", 170, 460)
    pos = [(10, 20), (160, 20), (310, 20), (310, 100), (160, 100), (10, 100)]
    for i, (x, y) in enumerate(pos):
        f.draw(_g(rect(x, y, 140, 46, 5)))
        f.text_in(i, x + 70, y + 27, weight=700)
    f.draw(arrow(150, 43, 158, 43), arrow(300, 43, 308, 43), arrow(380, 66, 380, 98), arrow(310, 123, 302, 123), arrow(160, 123, 152, 123))
    return f.svg()


def f_2_0():
    f = Fig("2-0", 250)
    f.draw(spindle_tool(230, 14, 70), _g(rect(150, 196, 160, 34, 2), hatch(150, 196, 160, 34)),
           _g(screen(340, 24, 100, 62, 2)))
    f.callout(0, 230, 196, "l", 214).code(1, 64, 230)        # 치수·가공면 · TW-1
    f.callout(2, 230, 160, "l", 160).code(3, 64, 176)        # 날 끝 · TW-2
    f.callout(4, 360, 60, "c", 112, x=390).code(5, 390, 128)  # 마모 카운터 · TW-3
    f.callout(6, 230, 88, "l", 86).code(7, 64, 102)          # 콜릿·홀더 · TW-4
    return f.svg()


def f_3_0():
    f = Fig("3-0", 230)
    f.draw(screen(150, 24, 110, 70), _g(rect(290, 120, 120, 50, 3), *[circ(306 + i * 22, 145, 6) for i in range(5)]),
           _g(rect(150, 130, 90, 70, 6), circ(195, 165, 22), rect(236, 158, 16, 14, 2, stroke=HIL, stroke_width=1.6), path("M 252 165 C 270 165 270 200 300 205")))
    f.callout(0, 200, 50, "l", 44).code(1, 64, 60)           # 회전수·이송 설정 · PW-1
    f.callout(2, 350, 132, "r", 100, x=330).code(3, 372, 116)  # 전원 단자대 · PW-2
    f.callout(4, 244, 165, "l", 190).code(5, 64, 206)        # 엔코더 커넥터·케이블 · PW-3
    return f.svg()


def f_4_0():
    f = Fig("4-0", 240)
    f.draw(_g(rect(150, 20, 70, 110, 3), *[line(156, 34 + i * 9, 214, 34 + i * 9, stroke=SOFT, stroke_width=0.8) for i in range(10)]),
           fan(270, 70, 34), _g(rect(170, 160, 90, 60, 3), rect(178, 168, 10, 44, 1, stroke_width=0.8), line(178, 196, 188, 196, stroke_width=1.6)),
           screen(300, 150, 110, 70, 2))
    f.callout(0, 185, 70, "l", 40).code(1, 64, 56)           # 흡기 필터 · CL-1
    f.callout(2, 270, 40, "r", 30, x=330).code(3, 372, 46)    # 냉각 팬 · CL-2
    f.callout(4, 210, 190, "l", 190).code(5, 64, 206)        # 냉각수 탱크 · CL-3
    f.callout(6, 355, 220, "c", 236 - 2, x=330).code(7, 430, 236 - 18)  # 냉각 팬 속도·절삭유 펌프 설정 · CL-4
    return f.svg()


def f_5_0():
    f = Fig("5-0", 240)
    f.draw(_g(rect(170, 16, 110, 70, 4), circ(200, 51, 14), circ(250, 51, 14), circ(200, 51, 6, stroke_width=0.8), circ(250, 51, 6, stroke_width=0.8)),
           spindle_tool(225, 86 - 40, 50), _g(path("M 300 120 L 262 160", stroke_width=3), circ(258, 164, 4)), _g(rect(160, 196, 140, 30, 2), hatch(160, 196, 140, 30)),
           _g(path("M 258 168 l -12 22", stroke=SOFT, stroke_width=0.9, stroke_dasharray="3 2"), path("M 262 168 l -6 24", stroke=SOFT, stroke_width=0.9, stroke_dasharray="3 2")))
    f.callout(0, 300, 120, "r", 120, x=330).code(1, 372, 136)  # 노즐 · LD-1
    f.callout(2, 200, 51, "l", 50).code(3, 64, 66)            # 베어링 · LD-2
    f.callout(4, 230, 196, "l", 200).code(5, 64, 216)         # 이송 속도·절삭 깊이 · LD-3
    return f.svg()


def f_2_3():
    f = Fig("2-3", 220)
    f.draw(sheet(140, 30, 70, 90, 6), screen(240, 30, 100, 70, 2), _g(rect(240, 140, 100, 44, 4), rect(252, 150, 76, 24, 2, stroke=HIL, stroke_width=1.8)),
           arrow(290, 104, 290, 136, SOFT, 1, "4 3"))
    f.callout(0, 150, 60, "l", 50).callout(1, 300, 46, "r", 30, x=356).callout(2, 330, 162, "r", 170, x=356).callout(3, 320, 82, "r", 90, x=356)
    return f.balloons((175, 132, 175, 120), (250, 118, 260, 98), (226, 162, 240, 162)).svg()


def f_2_4():
    f = Fig("2-4", 230)
    f.draw(spindle_tool(220, 10, 80), _g(rect(150, 196, 140, 14, 2), line(150, 216, 290, 216, stroke_dasharray="4 3")),
           _g(circ(300, 150, 26), line(318, 168, 344, 196, stroke_width=3)), _g(circ(220, 166, 11, stroke=HIL, stroke_width=2)))
    f.callout(0, 214, 120, "l", 110).callout(1, 200, 210, "l", 200).callout(2, 300, 124, "r", 112, x=356).callout(3, 230, 166, "r", 160, x=356)
    return f.balloons((180, 214, 190, 206), (290, 92, 300, 124), (246, 186, 228, 172)).svg()


def f_2_5():
    f = Fig("2-5", 236)
    f.draw(_g(rect(150, 120, 80, 50, 3), hatch(150, 120, 80, 50)), _g(path("M 260 60 h 70 v 28 h -70 z"), rect(330, 66, 36, 16, 2), line(260, 88, 260, 120), line(250, 120, 270, 120)),
           _g(rect(270, 150, 70, 34, 2), *[line(276 + i * 10, 152, 276 + i * 10, 182, stroke_width=0.5) for i in range(7)]), _g(line(230, 145, 270, 165, stroke=SOFT, stroke_dasharray="4 3")), padlock(250, 196, 0.9))
    f.callout(0, 170, 140, "l", 140).callout(1, 230, 130, "l", 100).callout(2, 230, 160, "l", 200).callout(3, 305, 184, "r", 200, x=356).callout(4, 300, 60, "r", 40, x=356)
    return f.balloons((136, 172, 152, 164), (240, 60, 260, 70), (226, 220, 250, 210)).svg()


def f_2_6():
    f = Fig("2-6", 220)
    f.draw(_g(rect(150, 40, 16, 120, 1.5), *[line(150, 60 + i * 24, 166, 72 + i * 24, stroke_width=0.9) for i in range(4)], rect(146, 60, 24, 96, 2, stroke=HIL, stroke_width=1.8)),
           _g(circ(200, 90, 22), line(215, 106, 236, 128, stroke_width=3)),
           _g(path("M 270 50 l 40 0 l -8 70 l -24 0 z"), path("M 278 60 l 24 0 m -22 20 l 20 0", stroke=SOFT, stroke_width=0.8)),
           _g(path("M 340 150 q 12 -10 24 0", stroke=SOFT), path("M 340 170 q 12 10 24 0", stroke=SOFT), rect(348, 120, 8, 70, 1)))
    f.callout(0, 158, 100, "l", 90).callout(1, 200, 70, "l", 40).callout(2, 290, 50, "r", 30, x=356).callout(3, 290, 90, "r", 74, x=356).callout(4, 364, 160, "r", 200, x=376)
    return f.balloons((180, 150, 168, 130), (258, 100, 276, 90), (330, 200, 348, 180)).svg()


def f_3_3():
    f = Fig("3-3", 200)
    f.draw(screen(140, 30, 120, 90, 3), sheet(290, 30, 70, 100, 6), arrow(262, 80, 286, 80, SOFT, 1, "4 3"), _g(rect(145, 36, 110, 18, 2, stroke=HIL, stroke_width=1.8)))
    f.callout(0, 140, 110, "l", 140).callout(1, 150, 45, "l", 44).callout(2, 230, 90, "l", 88).callout(3, 330, 130, "r", 160, x=356)
    return f.balloons((200, 18, 200, 36), (274, 102, 274, 82)).svg()


def f_3_4():
    f = Fig("3-4", 220)
    f.draw(_g(rect(140, 40, 140, 120, 6), circ(210, 100, 40), circ(210, 100, 14)), _g(circ(330, 70, 30, stroke_dasharray="4 3"), circ(330, 70, 22, stroke_width=0.8)),
           _g(rect(240, 120, 26, 18, 2, stroke=HIL, stroke_width=2)), _g(path("M 266 129 C 300 129 310 180 360 186", stroke_width=3), path("M 330 172 l 6 10 m 4 -12 l 6 10", stroke=HIL, stroke_width=1.2)))
    f.callout(0, 330, 40, "r", 24, x=372).callout(1, 253, 129, "l", 190).callout(2, 340, 180, "r", 206, x=372)
    return f.balloons((300, 110, 312, 92), (240, 160, 250, 138), (360, 160, 350, 178)).svg()


def f_3_5():
    f = Fig("3-5", 180)
    f.draw(screen(150, 24, 160, 120, 4), _g(rect(158, 94, 144, 18, 2, stroke=HIL, stroke_width=1.8)))
    f.callout(0, 150, 60, "l", 50).callout(1, 300, 103, "r", 130, x=336)
    return f.balloons((230, 160, 230, 144)).svg()


def f_3_6():
    f = Fig("3-6", 220)
    f.draw(_g(rect(150, 20, 120, 180, 3), path("M 270 20 l 50 20 v 160 l -50 -0", stroke_dasharray="4 3"),
              rect(166, 110, 88, 30, 2, stroke=HIL, stroke_width=1.8), *[circ(178 + i * 16, 125, 5) for i in range(6)]), glove(110, 40, 1.4))
    f.callout(0, 116, 52, "l", 100, x=100).callout(1, 200, 30, "r", 30, x=336).callout(2, 210, 125, "r", 150, x=336)
    return f.balloons((300, 70, 290, 60), (210, 170, 210, 140)).svg()


def f_4_3():
    f = Fig("4-3", 240)
    f.draw(_g(rect(150, 30, 120, 170, 4), rect(180, 60, 70, 110, 3)),
           f'<g stroke="{SOFT}" stroke-width="0.8">' + "".join(line(186, y, 244, y) for y in range(70, 166, 8)) + "</g>",
           _g(rect(180, 60, 70, 110, 3, stroke=HIL, stroke_width=2)), _g(path("M 300 50 h 60 v 140 h -60 z"), circ(310, 62, 5), circ(350, 178, 5)),
           _g(*[path(f"M {x} 214 v -18 m -4 6 l 4 -6 l 4 6") for x in (196, 215, 234)], stroke=SOFT))
    f.callout(0, 330, 120, "r", 120, x=376).callout(1, 310, 62, "r", 40, x=376).callout(2, 186, 120, "l", 110).callout(3, 215, 204, "l", 210)
    return f.balloons((300, 24, 308, 56), (210, 44, 214, 66), (160, 150, 182, 150), (340, 214, 330, 190)).svg()


def f_4_4():
    f = Fig("4-4", 230)
    f.draw(_g(path("M 140 30 h 60 v 170 h -60 z", stroke_dasharray="4 3")), _g(rect(220, 40, 130, 130, 4)), fan(285, 105, 48),
           _g(*[circ(x, y, 4) for x, y in ((230, 50), (340, 50), (230, 160), (340, 160))]), _g(rect(300, 176, 24, 14, 2)),
           _g(circ(285, 105, 11, stroke=HIL, stroke_width=2)))
    f.callout(0, 170, 100, "l", 100).callout(1, 222, 70, "l", 40, x=118).callout(2, 312, 183, "r", 210, x=376).callout(3, 340, 50, "r", 30, x=376)
    f.callout(4, 290, 105, "r", 96, x=376).callout(5, 320, 128, "r", 140, x=376)
    return f.balloons((330, 206, 316, 190), (356, 66, 344, 54), (262, 160, 280, 116)).svg()


def f_4_5():
    f = Fig("4-5", 230)
    f.draw(_g(rect(150, 40, 150, 140, 4), path("M 150 40 h 60 v 140 h -60", stroke_dasharray="4 3")),
           _g(rect(250, 60, 22, 100, 2, stroke=HIL, stroke_width=1.8), line(246, 80, 276, 80, stroke_width=1.2), line(246, 140, 276, 140, stroke_width=1.2)),
           f'<rect x="252" y="112" width="18" height="46" fill="#d7e6f5" stroke="none"/>', _g(line(250, 112, 272, 112, stroke="#1f5aa6", stroke_width=1.6)),
           _g(line(120, 196, 340, 196), *[path(f"M {x} 202 q 6 4 12 0") for x in (180, 210, 240)], stroke=SOFT))
    f.callout(0, 225, 170, "l", 170).callout(1, 180, 60, "l", 50).callout(2, 262, 66, "r", 30, x=330).callout(3, 274, 80, "r", 80, x=330)
    f.callout(4, 270, 112, "r", 116, x=330).callout(5, 210, 202, "l", 210)
    return f.balloons((190, 22, 186, 42), (300, 154, 272, 150), (270, 214, 245, 204)).svg()


def f_4_6():
    f = Fig("4-6", 200)
    f.draw(screen(150, 30, 160, 110, 2), _g(rect(156, 34, 148, 104, 3, stroke=HIL, stroke_width=1.8)))
    f.callout(0, 150, 50, "l", 30).callout(1, 230, 46, "l", 80).callout(2, 230, 120, "l", 130)
    return f.balloons((240, 160, 240, 140), (330, 90, 300, 90)).svg()


def f_5_3():
    f = Fig("5-3", 220)
    f.draw(_g(rect(150, 140, 70, 50, 4), circ(185, 165, 14)), _g(rect(240, 150, 30, 24, 3), circ(255, 162, 6)),
           _g(path("M 185 140 V 80 H 300", stroke_width=2.4), path("M 300 80 l 24 30", stroke_width=3), path("M 300 80 l 8 34", stroke_width=3)),
           _g(path("M 326 114 l 10 30 M 330 114 l 2 30 M 310 116 l 4 30", stroke="#1f5aa6", stroke_width=1, stroke_dasharray="3 2")),
           _g(circ(324, 112, 8, stroke=HIL, stroke_width=1.8)))
    f.callout(0, 170, 190, "l", 196).callout(1, 255, 174, "r", 204, x=300).callout(2, 314, 104, "r", 70, x=356).callout(3, 334, 140, "r", 150, x=356)
    return f.balloons((255, 126, 255, 152), (350, 100, 330, 108)).svg()


def f_5_4():
    f = Fig("5-4", 200)
    f.draw(screen(140, 30, 120, 90, 2), sheet(290, 30, 70, 110, 6), _g(rect(292, 74, 66, 14, 2, stroke=HIL, stroke_width=1.6)), arrow(262, 80, 286, 80, SOFT, 1, "4 3"))
    f.callout(0, 200, 46, "l", 46).callout(1, 200, 104, "l", 104).callout(2, 330, 130, "r", 160, x=376).callout(3, 350, 81, "r", 60, x=376)
    return f.balloons((200, 136, 200, 120), (274, 104, 274, 84)).svg()


def f_5_5():
    f = Fig("5-5", 220)
    f.draw(_g(rect(160, 30, 110, 90, 6), circ(215, 75, 20)), spindle_tool(215, 120 - 40, 40),
           _g(rect(300, 60, 50, 70, 4), rect(306, 68, 38, 22, 2, stroke_width=0.8), path("M 300 100 C 290 100 285 90 272 90")),
           _g(circ(270, 90, 6, stroke=HIL, stroke_width=2)), _g(path("M 190 172 a 26 8 0 1 0 50 0", stroke=SOFT), arrow(238, 174, 242, 168, SOFT, 1)), padlock(126, 120, 0.9))
    f.callout(0, 325, 60, "r", 40, x=366).callout(1, 270, 90, "r", 150, x=366).callout(2, 215, 180, "l", 190)
    return f.balloons((290, 22, 300, 60), (170, 162, 190, 172), (110, 100, 128, 124)).svg()


def f_5_6():
    f = Fig("5-6", 200)
    f.draw(_g(rect(150, 40, 120, 110, 6), circ(185, 95, 22), circ(235, 95, 22), circ(185, 95, 10, stroke_width=0.8), circ(235, 95, 10, stroke_width=0.8)),
           _g(rect(300, 52, 140, 34, 4, stroke_width=1.8)), _g(rect(300, 112, 140, 34, 4)), arrow(370, 88, 370, 110))
    f.text_in(0, 370, 73, weight=700).text_in(1, 370, 133, weight=700)
    f.callout(2, 185, 95, "l", 120)
    return f.svg()


def f_6_1():
    f = Fig("6-1", 150)
    xs = [10, 100, 190, 280, 370]
    for i, x in enumerate(xs):
        f.draw(_g(rect(x, 30, 80, 40, 5)))
        f.text_in(i, x + 40, 54, size=9)
    f.draw(*[arrow(x + 80, 50, x + 98, 50) for x in xs[:-1]])
    # 원본 6.1 단계 묶음: 1 = 체크리스트 · 잠금을 푼다(609) · 2 = 무부하(610) · 3 = 경계 이내 · 기록한다(611)
    f.draw(_g(path("M 14 80 v 8 h 162 v -8"), path("M 194 80 v 8 h 72 v -8"), path("M 284 80 v 8 h 162 v -8"), sw=1.1))
    return f.balloons((95, 112, 95, 88), (230, 112, 230, 88), (365, 112, 365, 88)).svg()


FIGS = {"1-1": f_1_1, "1-2": f_1_2, "1-3": f_1_3, "2-0": f_2_0, "3-0": f_3_0, "4-0": f_4_0, "5-0": f_5_0,
        "2-3": f_2_3, "2-4": f_2_4, "2-5": f_2_5, "2-6": f_2_6, "3-3": f_3_3, "3-4": f_3_4, "3-5": f_3_5, "3-6": f_3_6,
        "4-3": f_4_3, "4-4": f_4_4, "4-5": f_4_5, "4-6": f_4_6, "5-3": f_5_3, "5-4": f_5_4, "5-5": f_5_5, "5-6": f_5_6, "6-1": f_6_1}
