"""Лист игроков для определения формы человеком: по 8 игроков из каждой группы разбора.
    python sheet.py crops_dir out.jpg"""
import json, sys, os.path as osp, cv2, numpy as np
d, out = sys.argv[1], sys.argv[2]
tr = json.load(open(osp.join(d, "tracks.json")))
rows = []
for team, label in ((0, "группа 1"), (1, "группа 2"), (-1, "без группы")):
    ts = sorted([(m["frames_seen"], t, m) for t, m in tr.items() if m.get("team") == team], reverse=True)[:8]
    cells = []
    for _, t, m in ts:
        s = m["shots"][len(m["shots"]) // 2]; p = osp.join(d, s)
        if osp.exists(p): cells.append(cv2.resize(cv2.imread(p), (110, 220)))
    while len(cells) < 8: cells.append(np.zeros((220, 110, 3), np.uint8))
    row = np.hstack(cells); bar = np.zeros((26, row.shape[1], 3), np.uint8)
    cv2.putText(bar, label.encode("ascii", "ignore").decode() or str(team), (6, 19), 0, .6, (255, 255, 255), 1)
    rows += [bar, row]
cv2.imwrite(out, np.vstack(rows)); print("лист", out)
