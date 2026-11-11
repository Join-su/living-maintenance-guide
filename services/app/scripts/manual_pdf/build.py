"""MG-MM 매뉴얼 PDF용 HTML 생성기(EV-005) — 원본 data/manuals/MG-MM.md 글을 그대로 싣고, 꾸밈 · 그림 · 앞뒤 쪽만 더한다.

- 원본 한 줄 = 요소 하나, `data-src="L<줄 번호>"`(표는 행마다). 원본 글은 마크다운 기호만 서식으로 바꾸고 글자는 그대로.
- 꾸밈 요소는 `data-deco="<종류>"`(cover · docinfo · toc · guide · figure · copy · form · frame · srcref).
  PDF 전용 글은 pdf_only_text.json · figures_trace.json · 원본 front matter 값 · 원본 sha에서만 온다.
- 복사 요소(copy)는 data-src(출처 줄)도 단다: 1.3 보호구 줄 · 부록 D 표 · 판정 기준 모음.
- 같은 입력이면 같은 HTML(시각 · 난수 없음). 원본 sha256을 문서 정보 쪽 글로 싣는다.
- 글꼴은 맥의 Apple SD Gothic Neo(PDF에 박힘). 다른 OS에서 다시 만들면 대체 글꼴이 쓰인다.
사용: python3 -I build.py <저장소 루트> <나갈 폴더> [--pages-from <body.pdf>]
"""

import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import figures as F  # noqa: E402

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
SRC = ROOT / "data/manuals/MG-MM.md"
raw = SRC.read_bytes()
SHA = hashlib.sha256(raw).hexdigest()
L = raw.decode("utf-8").split("\n")
A = json.loads((HERE / "pdf_only_text.json").read_text(encoding="utf-8"))
TR = {f["fig"]: f for f in json.loads((HERE / "figures_trace.json").read_text(encoding="utf-8"))["figures"]}
e = html.escape
SEP = re.compile(r"^\|[-|\s:]+\|$")


def allow(kind, s):
    assert s in A[kind], (kind, s)  # 허용 목록 밖 글을 만들지 않음
    return e(s)


def inline(t):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(t))


def src(n):
    return f'data-src="L{n + 1}"'


# ── front matter
assert L[0] == "---"
FM_END = L.index("---", 1)
FM = {}
for l in L[1:FM_END]:
    k, v = l.split(": ", 1)
    FM[k] = v.strip('"')
TITLE = next(i for i, l in enumerate(L) if l.startswith("# "))
INTRO = next(i for i in range(TITLE + 1, len(L)) if L[i].startswith("> "))


def cells(l):
    return [c.strip() for c in l.strip().strip("|").split("|")]


def table(rows, deco_rows=None, cls=""):
    """rows = [(줄 번호, 원본 줄)] — 구분 행은 그리지 않음. deco_rows: 행마다 붙일 꾸밈 속성."""
    rows = [(n, l) for n, l in rows if not SEP.match(l)]
    (hn, hl), body = rows[0], rows[1:]
    at = (lambda n: f'{deco_rows} {src(n)}') if deco_rows else src
    head = f'<thead><tr {at(hn)}>' + "".join(f"<th>{inline(c)}</th>" for c in cells(hl)) + "</tr></thead>"
    tb = "".join(f'<tr {at(n)}>' + "".join(f"<td>{inline(c)}</td>" for c in cells(l)) + "</tr>" for n, l in body)
    return f'<table class="tbl {cls}">{head}<tbody>{tb}</tbody></table>'


def kind(l):
    if not l.strip():
        return "blank"
    if re.match(r"^\d+\. ", l):
        return "ol"
    if l.startswith("- [ ] "):
        return "check"
    if l.startswith("- "):
        return "ul"
    if l.startswith("|"):
        return "table"
    return "one"


def runs(a, b):
    """[a, b) 줄을 같은 꼴 묶음으로: (꼴, [(줄 번호, 줄)])."""
    out, i = [], a
    while i < b:
        k = kind(L[i])
        if k == "blank":
            i += 1
            continue
        j = i + 1
        if k != "one":
            while j < b and kind(L[j]) == k:
                j += 1
        out.append((k, [(n, L[n]) for n in range(i, j)]))
        i = j
    return out


def one(n, l):
    """원본 한 줄(목록 · 표 밖)."""
    if l.startswith("## "):
        m = re.match(r"^((?:부록 )?[\dA-J]+\.) (.+)$", l[3:])
        return f'<h2 class="chap" {src(n)}><span class="cn">{e(m.group(1))}</span> {inline(m.group(2))}</h2>'
    if l.startswith("### "):
        no, t = l[4:].split(" ", 1)
        return f'<h3 {src(n)}><span class="no">{e(no)}</span> {inline(t)}</h3>'
    if l.startswith("#### "):
        return f'<h4 {src(n)}>{inline(l[5:])}</h4>'
    if l.startswith("> **규칙** "):
        return f'<div class="box rule" {src(n)}><span class="ic">{F.icon_warning()}</span><div>{inline(l[2:])}</div></div>'
    if l.startswith("> "):
        return f'<div class="box note" {src(n)}>{inline(l[2:])}</div>'
    if re.fullmatch(r"\*\*[A-J]\.\d+ .+\*\*", l):
        return f'<h5 {src(n)}>{inline(l)}</h5>'
    if l.startswith("**판정 기준** "):
        return f'<div class="box crit" {src(n)}>{inline(l)}</div>'
    if l.startswith("**권한 등급**"):
        return f'<p class="meta" {src(n)}>{inline(l)}</p>'
    if re.fullmatch(r"\*\*[^*]+\*\*", l):
        return f'<p class="lbl" {src(n)}>{inline(l)}</p>'
    return f'<p {src(n)}>{inline(l)}</p>'


def items(k, rs, sec):
    if k == "table":
        return table(rs, cls="rev" if cells(rs[0][1]) == ["개정", "날짜", "내용"] else "long" if len(rs) > 30 else "")
    if k == "ol":
        out = []
        for n, l in rs:
            no, t = l.split(". ", 1)
            warn = sec == "3.6" and "손으로 조이지 않는다" in t  # 원본 3.6 줄 — 경고 꾸밈(글 그대로)
            ic = f'<span class="ic">{F.icon_warning(16)}</span>' if warn else ""
            out.append(f'<li class="{"warn" if warn else ""}" {src(n)}><span class="stepno">{e(no)}</span>{ic}<span class="tx">{inline(t)}</span></li>')
        return '<ol class="steps">' + "".join(out) + "</ol>"
    if k == "check":
        return '<ul class="ck">' + "".join(f'<li {src(n)}><span class="cb"></span>{inline(l[6:])}</li>' for n, l in rs) + "</ul>"
    if k == "ul":
        ref = lambda l: " ref" if l.startswith("- 참고") or "참고:" in l else ""  # 부록 F 참고 줄 — 주의 꾸밈(글 그대로)
        return '<ul class="dot">' + "".join(f'<li class="{ref(l).strip()}" {src(n)}>{inline(l[2:])}</li>' for n, l in rs) + "</ul>"
    return "".join(one(n, l) for n, l in rs)


# ── 꾸밈 조각
PPE = {m.group(1): i for i, l in enumerate(L) if (m := re.match(r"^- (공구·콜릿 작업|냉각 계통 작업|전기함 개방): ", l))}
H13 = next(i for i, l in enumerate(L) if l.startswith("### 1.3 "))


def srcref(h):
    """출처 표시 '— <절 번호> <원본 머리 글>'."""
    no, t = L[h][4:].split(" ", 1)
    return f'<span class="srcref" data-deco="srcref">— {e(no)} {e(t)}</span>'


def ppe_copy(a, b):
    """그 절 '보호구:' 줄이 가리키는 1.3 줄을 그대로 복사(주의 상자)."""
    out = []
    for i in range(a, b):
        for m in re.finditer(r"보호구와 고온부 절의 (공구·콜릿 작업|냉각 계통 작업|전기함 개방)", L[i]):
            n = PPE[m.group(1)]
            out.append(f'<div class="box caution" data-deco="copy" {src(n)}><span class="ic">{F.icon_ppe()}</span><div>'
                       f'<span class="lbl" data-deco="frame">{allow("frame", "주의")}</span><span class="tx">{inline(L[n][2:])}</span>{srcref(H13)}</div></div>')
    return "".join(dict.fromkeys(out))


def figure(fid):
    f = TR[fid]
    svg = getattr(F, "f_" + fid.replace("-", "_"))()
    cap = [f'<span class="fno" data-deco="figure">그림 {e(fid)}</span>']
    if f.get("caption_src"):
        assert f["caption_src"] in L[f["title_line"] - 1]
        cap.append(f'<span data-deco="figure">{e(f["caption_src"])}</span>')
    else:
        cap.append(f'<span data-deco="figure">{allow("figure_captions", f["caption_allow"])}</span>')
    if f["balloons"]:
        cap.append(f'<span class="fnote" data-deco="figure">— 번호는 {e(f["place"])}{"" if f["place"] == "6.1" else ".1"} 단계</span>')
    return f'<figure class="fig" data-deco="figure" data-fig="{e(fid)}"><div class="svg">{svg}</div><figcaption>{" ".join(cap)}</figcaption></figure>'


def form(rs):
    """부록 I 빈칸 양식 — 칸 이름 = 원본 줄을 ' · '로만 나눈 조각(괄호 안은 나누지 않음)."""
    def split(s):
        out, depth, cur = [], 0, ""
        for part in re.split(r"( · |\(|\))", s):
            if part == "(":
                depth += 1
            elif part == ")":
                depth -= 1
            if part == " · " and depth == 0:
                out.append(cur)
                cur = ""
            else:
                cur += part
        return out + [cur]
    rows = "".join(f'<tr><th data-deco="form" {src(n)}>{e(p)}</th><td class="blank"></td></tr>' for n, l in rs for p in split(l[2:]))
    foot = "".join(f'<th data-deco="form">{allow("form", w)}</th><td class="blank"></td>' for w in A["form"])
    return f'<div class="formwrap"><table class="form" data-deco="form"><tbody>{rows}</tbody></table><table class="form sign" data-deco="form"><tbody><tr>{foot}</tr></tbody></table></div>'


# ── 본문
H2 = [i for i, l in enumerate(L) if l.startswith("## ")]
INSPECT = {f["place"] for f in TR.values() if re.fullmatch(r"[2-5]\.[3-6]", f["place"])}
CHAP_FIG = {"1": ["1-1", "1-2"], "2": ["2-0"], "3": ["3-0"], "4": ["4-0"], "5": ["5-0"]}
body, toc, crit = [], [], []
for k, i in enumerate(H2):
    end = H2[k + 1] if k + 1 < len(H2) else len(L)
    cn = re.match(r"^## ((?:부록 )?[\dA-J]+)\.", L[i]).group(1)
    toc.append(("chap", i))
    body.append('<section class="chapter">' + one(i, L[i]) + "".join(figure(x) for x in CHAP_FIG.get(cn, [])))
    h3 = [j for j in range(i + 1, end) if L[j].startswith("### ")]
    for k2, rs in runs(i + 1, h3[0] if h3 else end):
        body.append(items(k2, rs, None))
        if cn == "부록 F" and k2 == "ul" and rs[0][0] > 0 and L[rs[0][0] - 2].startswith("**F.1 "):
            body.append(figure("1-3"))
        if cn == "부록 I" and k2 == "ul":
            body.append(form(rs))
    for m, j in enumerate(h3):
        send = h3[m + 1] if m + 1 < len(h3) else end
        sid = L[j][4:].split(" ", 1)[0]
        toc.append(("sec", j))
        body.append(f'<section class="sec">{one(j, L[j])}')
        placed = False
        for k2, rs in runs(j + 1, send):
            first = rs[0][1]
            if not placed and sid in INSPECT and first.startswith("#### ") and first.endswith("점검 방법"):
                body.append(ppe_copy(j, send) + figure(sid.replace(".", "-")))
                placed = True
            if not placed and sid == "6.1" and k2 == "ol":
                body.append(figure("6-1"))
                placed = True
            body.append(items(k2, rs, sid))
            for n, l in rs:
                if l.startswith("**판정 기준** ") and sid in INSPECT:
                    crit.append((j, n))
        body.append("</section>")
    body.append("</section>")
assert len(crit) == 16, len(crit)

fr = lambda s: f'<th data-deco="frame">{allow("frame", s)}</th>'
crit_rows = "".join(
    f'<tr><td data-deco="copy" data-cg="h{j + 1}" {src(j)}>{e(L[j][4:].split(" ", 1)[0])}</td>'
    f'<td data-deco="copy" data-cg="h{j + 1}" {src(j)}>{e(L[j][4:].split(" ", 1)[1])}</td>'
    f'<td data-deco="copy" {src(n)}>{inline(L[n])}</td></tr>' for j, n in crit)
body.append(f'<section class="chapter crit-all"><h2 class="chap" data-deco="frame">{allow("frame", "판정 기준 모음")}</h2>'
            f'<table class="tbl crit"><thead><tr>{fr("절")}{fr("점검")}{fr("판정 기준")}</tr></thead><tbody>{crit_rows}</tbody></table></section>')

# ── 앞쪽: 문서 정보 · 개정 이력(부록 D 표 복사) · 차례 · 이 문서 읽는 법
DOCKEY = {"doc_id": "문서 번호", "title": "제목", "revision": "개정", "issued": "제정", "doc_type": "문서 종류", "provenance": "출처 구분"}
D = next(i for i, l in enumerate(L) if l.startswith("## 부록 D."))
drows = [(n, L[n]) for n in range(D + 1, H2[H2.index(D) + 1]) if L[n].startswith("|")]
info = (f'<section class="front"><h2 class="chap" data-deco="docinfo">{allow("docinfo", "문서 정보")}</h2><table class="tbl kv"><tbody>'
        + "".join(f'<tr><th data-deco="docinfo">{allow("docinfo", DOCKEY[k])}</th><td data-deco="docinfo">{e(FM[k])}</td></tr>' for k in DOCKEY)
        + f'<tr><th data-deco="docinfo">{allow("docinfo", "원본 파일 sha256")}</th><td class="mono" data-deco="docinfo">{SHA}</td></tr></tbody></table>'
        + f'<h3 data-deco="docinfo">{allow("docinfo", "개정 이력")}</h3>' + table(drows, deco_rows='data-deco="copy"', cls="rev") + "</section>")

PAGES = {}
if "--pages-from" in sys.argv:
    pdf = sys.argv[sys.argv.index("--pages-from") + 1]
    txt = subprocess.run(["pdftotext", "-raw", pdf, "-"], capture_output=True, text=True, check=True).stdout.split("\f")
    norm = lambda s: re.sub(r"\s+", "", s)
    first = next(p for p, t in enumerate(txt) if norm(A["frame"][3]) in [norm(x) for x in t.split("\n")])  # '이 문서 읽는 법' 쪽
    p = first + 1
    heads = [(f"L{n + 1}", re.sub(r"^#+ ", "", L[n])) for _, n in toc] + [("crit", "판정 기준 모음")]
    for ref, h in heads:
        while norm(h) not in [norm(x) for x in txt[p].split("\n")]:
            p += 1
        PAGES[ref] = p + 1  # 본문 인쇄 쪽 번호(1부터)


def toc_html():
    rows = []
    for kd, n in toc:
        rows.append(f'<div class="toc-{kd}"><span class="t" data-deco="toc">{e(re.sub(r"^#+ ", "", L[n]))}</span>'
                    f'<span class="dots"></span><span class="pg" data-deco="toc">{PAGES.get(f"L{n + 1}", 0)}</span></div>')
    rows.append(f'<div class="toc-chap"><span class="t" data-deco="toc">{allow("frame", "판정 기준 모음")}</span><span class="dots"></span>'
                f'<span class="pg" data-deco="toc">{PAGES.get("crit", 0)}</span></div>')
    return f'<section class="front toc"><h2 class="chap" data-deco="toc">{allow("frame", "차례")}</h2>{"".join(rows)}</section>'


icons = [F.icon_warning, F.icon_ppe, F.icon_crit, F.icon_balloon, F.icon_highlight]
guide = (f'<section class="front"><h2 class="chap" data-deco="guide">{allow("frame", "이 문서 읽는 법")}</h2><table class="tbl guide"><tbody>'
         + "".join(f'<tr><td class="gi">{ic()}</td><td data-deco="guide">{allow("guide_sentences", s)}</td></tr>' for ic, s in zip(icons, A["guide_sentences"]))
         + "</tbody></table></section>")

cover = (f'<section class="cover"><div class="cv-top"><span data-deco="cover">{e(FM["doc_id"])}</span>'
         f'<span><span data-deco="cover">{allow("cover", "개정")}</span> <span data-deco="cover">{e(FM["revision"])}</span></span></div>'
         f'<div class="cv-title"><div class="kicker" data-deco="cover">{allow("cover", "정비 매뉴얼")}</div><h1 {src(TITLE)}>{inline(L[TITLE][2:])}</h1>'
         f'<div class="model" data-deco="cover">{allow("cover", "RV-400 계열")}</div></div>'
         f'<div class="cv-fig">{F.f_cover()}</div>'
         f'<table class="cv-meta"><tbody><tr>'
         + "".join(f'<th data-deco="cover">{allow("cover", a)}</th><td data-deco="cover">{e(FM[k])}</td>' for a, k in (("문서 번호", "doc_id"), ("개정", "revision"), ("제정", "issued")))
         + f'</tr></tbody></table><p class="cv-intro" {src(INTRO)}>{inline(L[INTRO][2:])}</p>'
         f'<p class="cv-note" data-deco="cover">{allow("cover", "가상 문서")}</p></section>')

CSS = (HERE / "manual.css").read_text(encoding="utf-8")
OUT.mkdir(parents=True, exist_ok=True)
page = lambda inner: f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{e(FM["doc_id"])}</title><style>{CSS}</style></head><body>{inner}</body></html>\n'
(OUT / "cover.html").write_text(page(cover), encoding="utf-8")
(OUT / "body.html").write_text(page(info + toc_html() + guide + "".join(body)), encoding="utf-8")
(OUT / "pages.json").write_text(json.dumps(PAGES, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
print("html", OUT, SHA[:12], "toc", "채움" if PAGES else "자리")
