"""AI4I 2020 분할 v2: 시간 가정 없음. 공구 마모 리셋 구간 묶음 단위, 시연 묶음(C1-a15 알람 네 증상군 PW · CL · LD · TW가 모두 들도록)을 먼저 뺌, 나머지는 라벨별 비율을 맞추는 무작위 탐색(seed 고정)."""
import math
import csv, sys, json, hashlib, random, collections
src, out = sys.argv[1], sys.argv[2]
raw = open(src, 'rb').read(); sha = hashlib.sha256(raw).hexdigest()
rows = list(csv.DictReader(open(src, encoding='utf-8-sig')))
L = ['TWF', 'HDF', 'PWF', 'OSF', 'RNF']
seg = []; cur = 0; prev = None
for r in rows:
    tw = int(r['Tool wear [min]'])
    if prev is not None and tw < prev: cur += 1
    seg.append(cur); prev = tw
NSEG = cur + 1
DEMO_UDI = [4492, 4494, 4495, 4496, 4497, 4511, 4563, 4730, 4732, 4733]  # C1-a15 data/demo/scenarios.json
udi_idx = {int(r['UDI']): i for i, r in enumerate(rows)}
base_demo = sorted({seg[udi_idx[u]] for u in DEMO_UDI})
# C1-a15 알람 규칙(scripts/build_data.py 와 같은 식): 경계 = 2.5 · 97.5 백분위, 우선순위 PW → CL → LD → TW
def feat(r):
    air, proc, rpm, tq, wear = (float(r[k]) for k in ('Air temperature [K]', 'Process temperature [K]', 'Rotational speed [rpm]', 'Torque [Nm]', 'Tool wear [min]'))
    return {'power': round(tq * rpm * 2 * math.pi / 60), 'temp_diff': round(proc - air, 1), 'torque': tq, 'wear': wear}
F = [feat(r) for r in rows]
RULES = [('PW', 'power', ('low', 'high')), ('CL', 'temp_diff', ('low',)), ('LD', 'torque', ('high',)), ('TW', 'wear', ('high',))]
def q(v, p): s = sorted(v); return s[round(p * (len(s) - 1))]
def thresholds(idx):
    th = {}
    for g, k, sides in RULES:
        vals = [F[i][k] for i in idx]
        for sd in sides: th[(g, sd)] = q(vals, .025 if sd == 'low' else .975)
    return th
def alarm_group(i, th):
    for g, k, sides in RULES:
        for sd in sides:
            b = th[(g, sd)]; v = F[i][k]
            if (sd == 'low' and v < b) or (sd == 'high' and v > b): return g
    return None
th0 = thresholds([i for i in range(len(rows)) if seg[i] not in base_demo])
def seg_alarms(s, th): return collections.Counter(alarm_group(i, th) for i in range(len(rows)) if seg[i] == s and alarm_group(i, th))
def seg_labels(s): return sum(int(rows[i][l]) for i in range(len(rows)) if seg[i] == s for l in L)
demo_segs = list(base_demo)
have = collections.Counter()
for s in demo_segs: have.update(seg_alarms(s, th0))
for g in ['LD', 'TW', 'PW', 'CL']:
    if have[g] >= 3: continue
    cand = [s for s in range(NSEG) if s not in demo_segs and seg_alarms(s, th0)[g] >= 3]
    cand.sort(key=lambda s: (seg_labels(s), -seg_alarms(s, th0)[g]))  # 라벨을 덜 빼앗는 구간 먼저
    demo_segs.append(cand[0]); have.update(seg_alarms(cand[0], th0))
demo_segs = sorted(demo_segs)
keys = L + ['fail', 'rows']
def segstats(s):
    c = collections.Counter()
    for i, r in enumerate(rows):
        if seg[i] != s: continue
        c['rows'] += 1; c['fail'] += int(r['Machine failure'])
        for l in L: c[l] += int(r[l])
    return c
S = {s: segstats(s) for s in range(NSEG)}
pool = [s for s in range(NSEG) if s not in demo_segs]
tot = collections.Counter()
for s in pool: tot.update(S[s])
target = {'train': .6, 'val': .2, 'test': .2}
MIN = {'RNF': 3, 'TWF': 6, 'HDF': 10}  # val · test 최소 건수
def cost(assign):
    c = {k: collections.Counter() for k in target}
    for s, k in assign.items(): c[k].update(S[s])
    e = 0.0
    for k, f in target.items():
        for key in keys:
            if tot[key] == 0: continue
            w = 3.0 if key in L else (2.0 if key == 'fail' else 1.0)
            e += w * ((c[k][key] - f * tot[key]) / (f * tot[key])) ** 2
        if k != 'train':
            for l, m in MIN.items():
                if c[k][l] < m: e += 100
    return e, c
rng = random.Random(20261022)
best = None
for trial in range(400):
    order = pool[:]; rng.shuffle(order)
    assign = {}; n = len(order)
    for j, s in enumerate(order): assign[s] = 'train' if j < .6 * n else ('val' if j < .8 * n else 'test')
    e, _ = cost(assign)
    for it in range(3000):  # 한 묶음 옮기기 또는 두 묶음 바꾸기
        a = rng.choice(pool)
        if rng.random() < .5:
            old = assign[a]; assign[a] = rng.choice([k for k in target if k != old])
            e2, _ = cost(assign)
            if e2 <= e: e = e2
            else: assign[a] = old
        else:
            b = rng.choice(pool)
            if assign[a] == assign[b]: continue
            assign[a], assign[b] = assign[b], assign[a]
            e2, _ = cost(assign)
            if e2 <= e: e = e2
            else: assign[a], assign[b] = assign[b], assign[a]
    if best is None or e < best[0]: best = (e, dict(assign))
e, assign = best
for s in demo_segs: assign[s] = 'demo'
_, c = cost({s: k for s, k in assign.items() if k != 'demo'})
c['demo'] = collections.Counter()
for s in demo_segs: c['demo'].update(S[s])
typ = {k: collections.Counter(rows[i]['Type'] for i in range(len(rows)) if assign[seg[i]] == k) for k in ['train', 'val', 'test', 'demo']}
summary = {k: {**{key: c[k][key] for key in keys}, 'type': dict(typ[k]), 'segments': sorted(s for s, v in assign.items() if v == k)} for k in ['train', 'val', 'test', 'demo']}
meta = {'source': 'UCI AI4I 2020 (manifact_10/data/raw/ai4i2020.csv)', 'sha256': sha,
        'method': 'no time assumption (AI4I has no timestamp). group split by tool-wear reset segment (120); demo = segments of C1-a15 DEMO-01..10 + fewest-label segments adding C1-a15 alarm groups LD/TW (>=3 alarm rows each); remaining segments assigned by seeded random search to match 60/20/20 rows and per-label shares (TWF/HDF/PWF/OSF/RNF/failure), min val·test counts RNF 3 · TWF 6 · HDF 10',
        'seed': 20261022, 'cost': round(e, 4), 'demo_udi': DEMO_UDI, 'summary': summary}
tr = [i for i in range(len(rows)) if assign[seg[i]] == 'train']; thT = thresholds(tr)
meta['alarm_thresholds_from_train'] = {f'{g}-{sd}': v for (g, sd), v in thT.items()}
meta['demo_alarm_rows_by_group_train_thresholds'] = dict(collections.Counter(alarm_group(i, thT) for i in range(len(rows)) if assign[seg[i]] == 'demo' and alarm_group(i, thT)))
json.dump(meta, open(out + '/ai4i-split.json', 'w'), ensure_ascii=False, indent=1)
print('demo segs', demo_segs, 'demo alarms(train th)', meta['demo_alarm_rows_by_group_train_thresholds'])
with open(out + '/ai4i-split-by-udi.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['UDI', 'segment', 'split'])
    for i, r in enumerate(rows): w.writerow([r['UDI'], seg[i], assign[seg[i]]])
for k, v in summary.items(): print(k, {key: v[key] for key in keys}, v['type'], len(v['segments']))
print('cost', round(e, 4), 'sha', sha[:12])
