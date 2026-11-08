"""Makes a contact sheet of one episode: 6 overhead frames across the take, plus 3 right-wrist frames."""
import sys, av, numpy as np, cv2
d, ep = sys.argv[1], int(sys.argv[2])
def frames(cam, k):
    c = av.open(f"{d}/videos/chunk-000/observation.images.{cam}/episode_{ep:06d}.mp4")
    fs = [f.to_ndarray(format="bgr24") for f in c.decode(video=0)]
    idx = np.linspace(0, len(fs) - 1, k).astype(int)
    return [cv2.resize(fs[i], (320, 180)) for i in idx], [i / 30 for i in idx]
top, tt = frames("overhead", 6)
wr, tw = frames("wrist_r", 3)
wl, tl = frames("wrist_l", 3)
for img, t in zip(top + wr + wl, tt + tw + tl):
    cv2.putText(img, f"{t:.1f}s", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
rows = [np.hstack(top[:3]), np.hstack(top[3:]), np.hstack(wr), np.hstack(wl)]
cv2.imwrite(sys.argv[3], np.vstack(rows))
