import cv2, numpy as np, json, sys
def turf_mask_of(frame):
    h, w = frame.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(frame, (7, 7), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    seed = lab[int(h*0.75):int(h*0.98), int(w*0.25):int(w*0.75)].reshape(-1,3)
    med = np.median(seed, axis=0)
    d = np.sqrt(((lab-med)**2).sum(axis=2))
    m = (d < 26).astype(np.uint8)*255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7,7),np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((31,31),np.uint8))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        k = 1+int(np.argmax(st[1:, cv2.CC_STAT_AREA])); m = (lbl==k).astype(np.uint8)*255
    cnts,_ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        hull = cv2.convexHull(max(cnts, key=cv2.contourArea)); m = np.zeros_like(m); cv2.fillConvexPoly(m, hull, 255)
    return m
cap = cv2.VideoCapture(sys.argv[1]); total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
masks=[]; frames=[]
for q in (0.15,0.4,0.65,0.9):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(total*q)); ok, fr = cap.read()
    if ok: masks.append(turf_mask_of(fr)); frames.append(fr)
pitch = (np.mean(masks,axis=0)>127).astype(np.uint8)
print("доля кадра под маской: %.1f %%" % (100*pitch.mean()))
# наложим маску на кадр и проверим точки треков из спорного эпизода
fr = frames[1].copy(); over = fr.copy(); over[pitch==0] = (over[pitch==0]*0.35).astype(np.uint8)
d = json.load(open(sys.argv[2]))
ins=out=0
for rec in d["frames"][::15]:
    for tid,x,y in rec["players"]:
        yi,xi=min(int(y),pitch.shape[0]-1),min(int(x),pitch.shape[1]-1)
        if pitch[yi,xi]: ins+=1
        else: out+=1; cv2.circle(over,(xi,yi),6,(0,0,255),-1)
print("точек треков на газоне %d, вне %d (%.0f %% вне)" % (ins,out,100*out/(ins+out)))
cv2.imwrite(sys.argv[3], cv2.resize(over,(960,540)))
