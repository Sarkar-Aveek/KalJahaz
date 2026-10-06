"""Run: py server.py  ->  http://localhost:8000"""
import json, re, sys, time, urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import live_data
import model

LOADED = 0.0


def reload():
    """Pull live market data (falls back to the saved CSV), then retrain."""
    global MARKET, META, LOADED
    try:
        live_data.refresh()
        live = True
    except Exception as e:
        print(f"Live market refresh failed ({e}); using saved data/market.csv")
        live = False
    m = model.Market()
    m.sources["live"] = live
    m.export(Path(__file__).parent / "data" / "model_weights.json")
    MARKET, META, LOADED = m, json.dumps(model.meta(m)).encode(), time.time()


reload()
WEB = Path(__file__).parent / "web"
# the Pages site (and its preview builds) may call this API from the browser
ALLOWED_ORIGIN = re.compile(r"^https://([a-z0-9-]+\.)?kaljahaz\.pages\.dev$|^http://(localhost|127\.0\.0\.1)(:\d+)?$")
_fx = {}


def usd_inr():
    """Daily USD->INR reference rate (Frankfurter, ECB data), cached an hour."""
    if _fx.get("t", 0) > time.time() - 3600:
        return _fx["v"]
    try:
        req = urllib.request.Request("https://api.frankfurter.dev/v1/latest?from=USD&to=INR",
                                     headers={"User-Agent": "KalJahaz/1.0"})  # default urllib agent gets 403
        with urllib.request.urlopen(req, timeout=5) as r:
            j = json.loads(r.read().decode())
        v = {"rate": j["rates"]["INR"], "date": j["date"]}
    except Exception:
        v = {"error": "rate unavailable"}
    _fx.update(t=time.time() if "rate" in v else time.time() - 3300, v=v)  # retry failures after 5 min
    return v


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(WEB), **k)

    def send_json(self, obj, status=200):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        origin = self.headers.get("Origin", "")
        if ALLOWED_ORIGIN.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/meta":
                if time.time() - LOADED > 6 * 3600:  # ponytail: refresh on page load after 6 h, a scheduler if it must be exact
                    reload()
                return self.send_json(META)
            if url.path == "/api/status":
                return self.send_json({"ok": True, "market_data_to": MARKET.m["date"][-1]})
            if url.path == "/api/fx":
                return self.send_json(usd_inr())
            if url.path == "/api/limits":
                return self.send_json(model.slider_limits(q["origin"], q["dest"]))
            if url.path == "/api/forecast":
                shocks = {k: max(-lim, min(lim, float(q["s_" + k])))  # clamp: query strings are untrusted
                          for k, lim in model.SHOCK_LIMITS.items() if q.get("s_" + k) not in (None, "", "0")}
                return self.send_json(model.forecast(MARKET.scenario(shocks), q["origin"], q["dest"], q["cls"], float(q["dwt"]),
                                                     use_live=q.get("live", "1") == "1"))
        except (KeyError, ValueError) as e:
            return self.send_json({"error": f"Bad request: {e}"}, 400)
        return super().do_GET()

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    import socket
    try:  # the address other devices on this network use (no packet is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan = s.getsockname()[0]
    except OSError:
        lan = "your-ip"
    print(f"KalJahaz on http://localhost:{port}  and on your network at http://{lan}:{port}")
    print(f"({'live' if MARKET.sources.get('live') else 'saved'} market data to {MARKET.m['date'][-1]})")
    ThreadingHTTPServer(("", port), Handler).serve_forever()
