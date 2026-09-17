"""Различают ли бутсы/волосы/рост игроков одной команды: пары треков с номером,
один номер против разных номеров. AUC 0.5 — не различают, 1.0 — идеально."""
import json, sys, itertools, numpy as np
pos, feats = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
num = {t: v["number"] for t, v in pos["numbers"].items() if v.get("number") not in (None, 0, 10)}
team = pos["team_of"]
T = [t for t in num if t in feats and team.get(t, -1) >= 0]
def bh(a, b): return 1 - np.sum(np.sqrt(np.array(a) * np.array(b)))
def auc(same, diff):
    s = np.array(same); d = np.array(diff); return float(np.mean(s[:, None] < d[None, :]) + 0.5 * np.mean(s[:, None] == d[None, :]))
for key in ("shoe", "head", "both"):
    same, diff = [], []
    for a, b in itertools.combinations(T, 2):
        if team[a] != team[b]: continue
        dist = bh(feats[a]["shoe"], feats[b]["shoe"]) + bh(feats[a]["head"], feats[b]["head"]) if key == "both" else bh(feats[a][key], feats[b][key])
        (same if num[a] == num[b] else diff).append(dist)
    print("%-5s пар один номер %d, разные %d, AUC %.2f" % (key, len(same), len(diff), auc(same, diff)))
