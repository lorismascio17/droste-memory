"""Steady-state (sequential) latency probe for /api/zoom-query — characterises
single-client latency vs the 50-way burst in wave 1."""
import http.client, json, time, statistics

def post(q):
    c = http.client.HTTPConnection("127.0.0.1", 5000, timeout=10)
    body = json.dumps({"query": q})
    t = time.perf_counter()
    c.request("POST", "/api/zoom-query", body=body,
              headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
    r = c.getresponse(); r.read(); c.close()
    return (time.perf_counter() - t) * 1000.0, r.status

qs = ["find_drama_clusters", "camera calibration math", "tracking moving objects",
      "_is_celebration_window", "homography"]
post("warm")  # warm caches
lat = []
for i in range(15):
    ms, st = post(qs[i % len(qs)])
    lat.append(ms)
    print(f"req{i:>2} {st} {ms:6.1f}ms", flush=True)
lat.sort()
print(f"\nSTEADY-STATE sequential: p50={statistics.median(lat):.1f}ms "
      f"p95={lat[int(len(lat)*0.95)-1]:.1f}ms max={max(lat):.1f}ms "
      f"-> <200ms: {'YES' if lat[int(len(lat)*0.95)-1] < 200 else 'NO'}")
