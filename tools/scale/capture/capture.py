#!/usr/bin/env python3
import argparse, http.client, json, os, random, signal, subprocess, sys, threading, time, urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
import lsc

P = lsc.paths(); stop = threading.Event(); MOUNTS = {}; LISTED = [0]; lock = threading.Lock(); NTP = [0, None]


def log(*a):
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), *a, flush=True)


def fetch_list():
    r = urllib.request.urlopen(urllib.request.Request(lsc.LIST_URL, headers={"User-Agent": lsc.UA}), timeout=30)
    new = lsc.mount_list(r.read().decode())
    with lock: MOUNTS.clear(); MOUNTS.update(new); LISTED[0] = int(time.time() * 1000)
    return len(new)


def lister():
    while not stop.wait(3600):
        try: fetch_list()
        except Exception as e: log("list refresh failed", e)


def ntp_state():
    with lock:
        if time.time() - NTP[0] > 30:
            out = {}
            for k, cmd in (("chronyc_tracking", ["chronyc", "tracking"]), ("timedatectl", ["timedatectl", "show", "-p", "NTPSynchronized", "-p", "TimeUSec"])):
                try: out[k] = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
                except Exception as e: out[k] = f"error: {e}"
            out["read_utc_ms"] = int(time.time() * 1000); NTP[0] = time.time(); NTP[1] = out
        return NTP[1]


class Seg:
    def __init__(self, m, hdrs, status, new_conn, t_conn, prefix, kind, now):
        self.m = m; self.new_conn = new_conn; self.t0 = now; self.kind = kind
        stamp = datetime.fromtimestamp(int(now), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        d = f"{P['buf']}/{m['mount']}"; os.makedirs(d, exist_ok=True)
        self.path = f"{d}/{stamp}.{m['ext']}"; self.scp = f"{d}/{stamp}.json.gz"
        self.f = open(self.path, "wb", buffering=65536); self.end = (int(now) // lsc.SEG_S + 1) * lsc.SEG_S
        if new_conn and self.end - now < 120: self.end += lsc.SEG_S  # a connection's first segment keeps its burst and needs >50 s to fit
        self.n = 0; self.last = 0; self.arr = []; self.hdr = len(prefix); self.saved = now
        self.sc = dict(mount=m["mount"], site_id=m["id"], name=m["name"], artist=m["artist"], city=m["city"], country=m["country"],
                       stream_url=m["url"], ext=m["ext"], kind=kind, lat=m["lat"], lng=m["lng"], list_fetched_utc_ms=LISTED[0],
                       http=dict(status=status, headers=hdrs), connect_utc_ms=int(t_conn * 1000), new_connection=new_conn,
                       ntp=ntp_state(), stamp=stamp, segment_start_utc_ms=int(now * 1000), arrival_log="[file offset after chunk, UTC ms]")
        if prefix: self.f.write(prefix); self.n = len(prefix)

    def add(self, d, now):
        self.f.write(d); self.n += len(d)
        if not self.arr or now - self.last >= 0.2: self.arr.append([self.n, int(now * 1000)]); self.last = now
        self.lastn = (self.n, now)
        if now - self.saved > 60: self.save(False); self.saved = now

    def save(self, complete):
        self.f.flush(); a = list(self.arr)
        if complete and self.lastn[0] != a[-1][0]: a.append([self.lastn[0], int(self.lastn[1] * 1000)])
        self.sc.update(bytes=self.n, header_bytes=self.hdr, complete=complete, arrival=a, segment_end_utc_ms=a[-1][1])
        self.sc["fit"] = None
        if complete:
            self.sc["state"] = "buffered"
            with open(self.path, "rb") as f: lsc.finish(self.sc, f.read())
        lsc.save_sc(self.scp, self.sc)

    def close(self):
        if not self.arr: self.f.close(); os.unlink(self.path); return
        self.save(True); self.f.close()
        fit = self.sc["fit"]
        if not fit:
            os.unlink(self.path); os.unlink(self.scp); log("drop fragment", self.m["mount"], self.n, "bytes"); return
        log("seg", self.m["mount"], os.path.basename(self.path), self.n, "B", fit["rate_Bps"], "B/s", "burst", fit.get("burst_bytes"))


def session(name):
    m = MOUNTS[name]; url = m["url"]
    for _ in range(4):
        u = urlparse(url); C = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        c = C(u.hostname, u.port, timeout=10); t_conn = time.time()
        c.request("GET", (u.path or "/") + (f"?{u.query}" if u.query else ""), headers={"User-Agent": lsc.UA, "Accept": "*/*"})
        r = c.getresponse()
        if r.status not in (301, 302, 303, 307, 308) or not r.getheader("Location"): break
        url = urljoin(url, r.getheader("Location")); c.close()
    if r.status != 200:
        ra = r.getheader("Retry-After", "")
        stop.wait(min(int(ra), 600) if ra.isdigit() else 0); raise RuntimeError(f"HTTP {r.status} retry-after={ra}")
    hdrs = [list(h) for h in r.getheaders()]; seg = None; head = bytearray(); hl = None; kind = None; got = 0; new_conn = True
    try:
        while not stop.is_set():
            d = r.read1(65536)
            if not d: raise EOFError("stream closed")
            now = time.time(); got += len(d)
            if kind is None: kind = "ogg" if d[:4] == b"OggS" else "mp3"
            if kind == "ogg" and hl is None:
                head += d; hl = lsc.ogg_header_len(bytes(head))
                if hl is None and len(head) > 262144: hl = 0
            if seg and now >= seg.end: seg.close(); seg = None; new_conn = False
            if not seg:
                m = MOUNTS.get(name)
                if not m: return got
                seg = Seg(m, hdrs, 200, new_conn, t_conn, bytes(head[:hl]) if (kind == "ogg" and not new_conn and hl) else b"", kind, now)
            seg.add(d, now)
            if kind == "ogg" and new_conn and hl and not seg.hdr: seg.hdr = hl
        return got
    finally:
        if seg: seg.close()
        c.close()


def reader(name, delay):
    time.sleep(delay); back = 2
    while not stop.is_set():
        t = time.time()
        try: session(name)
        except Exception as e: log("drop", name, type(e).__name__, e)
        if stop.is_set(): break
        back = 2 if time.time() - t > 120 else min(back * 2, 300)
        stop.wait(back + random.random() * back / 2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--mounts"); a = ap.parse_args()
    os.makedirs(P["log"], exist_ok=True); n = fetch_list()
    names = a.mounts.split(",") if a.mounts else sorted(MOUNTS)
    miss = [x for x in names if x not in MOUNTS]
    if miss: sys.exit(f"unknown mounts {miss}")
    log("start pid", os.getpid(), n, "fixed mounts listed,", len(names), "captured")
    signal.signal(signal.SIGTERM, lambda *_: stop.set()); signal.signal(signal.SIGINT, lambda *_: stop.set())
    ts = [threading.Thread(target=lister, daemon=True)] + [threading.Thread(target=reader, args=(x, i * 0.5)) for i, x in enumerate(names)]
    [t.start() for t in ts]; stop.wait()
    [t.join(15) for t in ts[1:]]; log("stopped")
