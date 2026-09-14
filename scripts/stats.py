"""Derive possession, passes, turnovers and tackles from tracked positions."""
import json, sys
import numpy as np
from collections import Counter, defaultdict

path, title = sys.argv[1], sys.argv[2]
d = json.load(open(path))
fps = d["fps"]
team_of = {int(k): v for k, v in d["team_of"].items()}
# -1 означает «не игрок или вратарь»: посторонние у бровки теперь отсеиваются
# на этапе разбора, и приписывать им владение нельзя.
NAMES = {int(k): v for k, v in (d.get("team_names") or {}).items()}
NAME = lambda t: NAMES.get(t, "команда %s" % ("A" if t == 0 else "B" if t == 1 else "?"))
frames = d["frames"]

# A player "has" the ball when his feet are the closest to it and close enough that
# contact is plausible. The threshold is in pixels and scales with nothing, so it is
# deliberately generous; possession is then smoothed over time.
MAX_DIST = 170
HOLD = 6          # frames a new owner must persist before possession is credited
# Мяч виден лишь в трети кадров. Раньше владение переносилось через любой провал,
# и выходило «владение определено 99,9 %» при мяче в 31,6 % кадров: две трети
# времени приписывались последнему хозяину. Держим владение не дольше полсекунды
# после того, как мяч пропал из виду.
MAX_CARRY = 15

raw = []
for f in frames:
    if f["ball"] is None or not f["players"]:
        raw.append(None)
        continue
    bx, by = f["ball"]
    best, bd = None, 1e9
    for tid, px, py in f["players"]:
        dist = ((px - bx) ** 2 + (py - by) ** 2) ** 0.5
        if dist < bd:
            best, bd = tid, dist
    raw.append(best if bd <= MAX_DIST else None)

# smooth: only switch owner once the new one has held for HOLD frames
owner, pending, pend_n = None, None, 0
smooth = []
since_ball = 0
for r, f in zip(raw, frames):
    since_ball = 0 if f["ball"] is not None else since_ball + 1
    if since_ball > MAX_CARRY:
        owner, pending, pend_n = None, None, 0
        smooth.append(None)
        continue
    if r is None:
        smooth.append(owner)
        continue
    if r == owner:
        pending, pend_n = None, 0
    else:
        if r == pending:
            pend_n += 1
        else:
            pending, pend_n = r, 1
        if pend_n >= HOLD:
            owner, pending, pend_n = r, None, 0
    smooth.append(owner)

held = [s for s in smooth if s is not None and team_of.get(s, -1) in (0, 1)]
by_team = Counter(team_of.get(s, -1) for s in held)
tot = sum(by_team.values())

# events: every change of owner
events = []
prev = None
for s in smooth:
    if s is not None and s != prev:
        if prev is not None:
            events.append((prev, s))
        prev = s

# события с участием неопознанных (вратарь, посторонний) не считаем ни пасом,
# ни потерей: раньше такой «пас» проходил между зрителем у забора и игроком
events = [(x, y) for x, y in events
          if team_of.get(x, -1) in (0, 1) and team_of.get(y, -1) in (0, 1)]
passes = [(a, b) for a, b in events if team_of.get(a) == team_of.get(b)]
losses = [(a, b) for a, b in events if team_of.get(a) != team_of.get(b)]

print("=" * 58)
print(title)
print("=" * 58)
n = len(frames)
det = sum(1 for f in frames if f["ball"] is not None)
print("длительность            %.1f с, %d кадров" % (n / fps, n))
print("мяч распознан           %.1f %% кадров" % (100 * det / n))
print("владение определено     %.1f %% кадров" % (100 * len(held) / n))
print("стабильность команд     %.3f" % d["vote_stability"])
print("-" * 58)
for t in (0, 1):
    share = 100 * by_team.get(t, 0) / tot if tot else 0
    print("владение, %-14s %5.1f %%   (%.1f с)" % (NAME(t), share, by_team.get(t, 0) / fps))
print("-" * 58)
print("смен владельца мяча     %d" % len(events))
print("передач внутри команды  %d" % len(passes))
print("потерь сопернику        %d" % len(losses))
for t in (0, 1):
    pa = sum(1 for a, b in passes if team_of.get(a) == t)
    lo = sum(1 for a, b in losses if team_of.get(a) == t)
    tk = sum(1 for a, b in losses if team_of.get(b) == t)
    tot_att = pa + lo
    acc = 100 * pa / tot_att if tot_att else 0
    print("  %-14s передач %3d, потерь %3d, точность %3.0f %%, отборов %3d"
          % (NAME(t) + ":", pa, lo, acc, tk))
print("-" * 58)
print("по игрокам, топ владения (идентификатор трека, не игрока):")
per = Counter(held)
for tid, c in per.most_common(6):
    pa = sum(1 for a, b in events if a == tid and team_of.get(b) == team_of.get(tid))
    lo = sum(1 for a, b in events if a == tid and team_of.get(b) != team_of.get(tid))
    print("  трек #%-5d %-10s владел %4.1f с  передач %d  потерь %d"
          % (tid, NAME(team_of.get(tid, -1)), c / fps, pa, lo))
