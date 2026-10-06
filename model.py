"""Freight forecasting + voyage cost model for dry bulk into India's East Coast.

Three parts:
  1. Market: an adaptive (recursive least squares, forgetting factor) model per Baltic
     sub-index, fed with momentum, mean reversion, China PMI, India IIP, China iron-ore
     import volume (IIV), world trade volume and seasonality. Learns online week by week.
  2. Ports & vessels: LOA / beam / draft limits decide what may call; draft overruns
     become part cargo via TPC (tonnes per cm immersion).
  3. Voyage: route split into named legs (great-circle between lane waypoints), speed loss
     from wind via Kwon's method, ocean current, tidal windows, congestion -> days -> cost.

Stdlib only. data/market.csv is rebuilt from live public sources by live_data.py (see data/market_sources.json).
"""
import copy, csv, json, math, datetime as dt, urllib.request, urllib.parse, time
from pathlib import Path

DATA = Path(__file__).parent / "data" / "market.csv"
HORIZONS = [("now", 0), ("d7", 7), ("d30", 30), ("d60", 60)]
# What-if limits: fraction of the latest reading (China PMI in index points). Server clamps to these.
SHOCK_LIMITS = {"bdi": 0.5, "bci": 0.5, "bpi": 0.5, "bsi": 0.5, "bhsi": 0.5,
                "india_iip": 0.3, "iron_ore_usd": 0.5, "world_trade_idx": 0.3, "china_pmi": 5.0}

# ---------------------------------------------------------------- vessels
# dims interpolate linearly from min to max DWT. index = the class's own Baltic index.
VESSELS = {
    "handysize": dict(name="Handysize", dwt=(20000, 42000), typical=38000, loa=(150, 190), beam=(23, 32.2),
                      draft=(9.0, 10.8), tpc=(30, 48), speed=13.0, ballast_speed=13.5, fuel_sea=20, fuel_port=3.5,
                      index="bhsi", std_dwt=38000, tce_factor=17.5),
    "supramax": dict(name="Supramax", dwt=(42000, 66000), typical=58000, loa=(185, 200), beam=(32.2, 32.3),
                     draft=(11.5, 13.5), tpc=(52, 62), speed=13.5, ballast_speed=14.0, fuel_sea=25, fuel_port=4,
                     index="bsi", std_dwt=63000, tce_factor=10.9),
    "panamax": dict(name="Panamax", dwt=(66000, 100000), typical=82000, loa=(225, 240), beam=(32.2, 38),
                    draft=(13.8, 14.8), tpc=(68, 85), speed=13.0, ballast_speed=13.5, fuel_sea=30, fuel_port=4.5,
                    index="bpi", std_dwt=82000, tce_factor=9.0),
    "capesize": dict(name="Capesize", dwt=(100000, 210000), typical=180000, loa=(250, 300), beam=(43, 50),
                     draft=(15.0, 18.3), tpc=(95, 125), speed=12.5, ballast_speed=13.5, fuel_sea=42, fuel_port=5.5,
                     index="bci", std_dwt=180000, tce_factor=8.3),
}
INDEX_NAMES = {"bdi": "BDI", "bci": "BCI", "bpi": "BPI", "bsi": "BSI", "bhsi": "BHSI"}


def vessel_dims(cls, dwt):
    v = VESSELS[cls]
    lo, hi = v["dwt"]
    f = min(1, max(0, (dwt - lo) / (hi - lo)))
    lerp = lambda k: v[k][0] + (v[k][1] - v[k][0]) * f
    return dict(cls=cls, name=v["name"], dwt=dwt, loa=round(lerp("loa"), 1), beam=round(lerp("beam"), 1),
                draft=round(lerp("draft"), 2), tpc=round(lerp("tpc"), 1), speed=v["speed"],
                ballast_speed=v["ballast_speed"], index=v["index"])


# ---------------------------------------------------------------- ports
# Approximate public figures, not official. draft = max sailing draft at high water.
# congestion = indicative average waiting days. dues = USD per DWT per call.
DESTINATIONS = {
    "VTZ": dict(name="Visakhapatnam", country="India", lat=17.685, lon=83.30, loa=300, beam=50, draft=17.0,
                tidal_range=1.5, rate=35000, congestion=1.5, dues=0.85,
                availability="Open · outer harbour mechanised coal berths",
                path=[]),
    "MAA": dict(name="Chennai", country="India", lat=13.10, lon=80.31, loa=280, beam=45, draft=16.5,
                tidal_range=1.0, rate=18000, congestion=1.0, dues=0.9,
                availability="Restricted · coal & ore largely shifted to Kamarajar since 2011",
                path=[]),
    "ENR": dict(name="Kamarajar (Ennore)", country="India", lat=13.255, lon=80.34, loa=290, beam=47, draft=16.0,
                tidal_range=1.0, rate=30000, congestion=2.0, dues=0.8,
                availability="Open · dedicated coal berths",
                path=[]),
    "PRT": dict(name="Paradip", country="India", lat=20.26, lon=86.69, loa=300, beam=50, draft=17.5,
                tidal_range=2.0, rate=50000, congestion=2.5, dues=0.8,
                availability="Open · high coal throughput, berth queues common",
                path=[]),
    "HAL": dict(name="Syama Prasad Mookerjee (Haldia)", country="India", lat=22.03, lon=88.07, loa=277, beam=39,
                draft=8.5, tidal_range=4.5, rate=12000, congestion=3.0, dues=0.95,
                availability="Tidal river port · lock-limited; Capesize transload at Sandheads",
                path=[("Sandheads pilot station", 20.95, 88.20), ("Hooghly channel", 21.60, 87.98)]),
}

ORIGINS = {
    "NEW": dict(name="Newcastle", country="Australia", lat=-32.92, lon=151.79, loa=300, beam=50, draft=16.0,
                tidal_range=1.6, rate=60000, congestion=3.0, dues=1.0, availability="Open · vessel queue managed by PWCS/NCIG",
                hub="E", path=[("Newcastle offing", -33.20, 152.20), ("Gabo Island", -37.60, 150.40),
                               ("Bass Strait", -39.25, 146.75), ("Cape Otway", -39.30, 143.60),
                               ("Cape Leeuwin", -35.20, 114.60)]),
    "PHE": dict(name="Port Hedland", country="Australia", lat=-20.31, lon=118.58, loa=330, beam=60, draft=19.5,
                tidal_range=7.0, rate=90000, congestion=1.0, dues=0.9, availability="Open · tide-dependent sailing windows",
                hub="E", path=[("Port Hedland offing", -19.60, 118.40), ("Eastern Indian Ocean", -10.0, 100.0)]),
    "TAB": dict(name="Taboneo anchorage", country="Indonesia", lat=-3.70, lon=114.45, loa=300, beam=55, draft=20.0,
                tidal_range=1.2, rate=15000, congestion=2.0, dues=0.5, availability="Open · floating cranes, barge-fed",
                hub="E", path=[("Java Sea", -5.00, 111.00), ("Java Sea West", -5.10, 107.00),
                               ("Sunda Strait", -5.75, 106.00), ("Sunda Strait South", -6.05, 105.75),
                               ("Off Java West", -7.60, 105.00), ("Off Mentawai", -1.00, 96.50)]),
    "MBR": dict(name="Muara Berau anchorage", country="Indonesia", lat=-0.30, lon=117.70, loa=300, beam=55, draft=17.0,
                tidal_range=1.5, rate=18000, congestion=2.5, dues=0.5, availability="Open · floating cranes, barge-fed",
                hub="E", path=[("Makassar Strait", -1.50, 118.30), ("Makassar Strait South", -4.50, 117.80),
                               ("Lombok Strait", -8.00, 115.75), ("Lombok Strait South", -9.30, 115.85),
                               ("South of Java", -9.00, 110.00), ("Off Java West", -7.60, 105.00),
                               ("Off Mentawai", -1.00, 96.50)]),
    "HRD": dict(name="Hampton Roads", country="USA", lat=36.95, lon=-76.33, loa=300, beam=50, draft=15.2,
                tidal_range=0.8, rate=40000, congestion=1.5, dues=0.7, availability="Open · Norfolk/Newport News coal piers",
                hub="W", path=[("Chesapeake entrance", 36.90, -75.60), ("Mid-Atlantic", 2.0, -22.0),
                               ("Cape of Good Hope", -35.20, 18.50), ("Cape Agulhas", -35.50, 20.50)]),
    "MOB": dict(name="Mobile", country="USA", lat=30.68, lon=-88.04, loa=274, beam=45, draft=14.0,
                tidal_range=0.5, rate=25000, congestion=1.0, dues=0.7, availability="Open · McDuffie coal terminal",
                hub="W", path=[("Mobile Bay entrance", 30.00, -88.05), ("Gulf of Mexico", 28.80, -88.00),
                               ("Dry Tortugas", 24.20, -83.20), ("Florida Strait", 23.90, -81.00),
                               ("Florida Strait North", 25.50, -79.75), ("Off Cape Canaveral", 28.00, -79.50),
                               ("North of Abaco", 27.50, -76.00), ("Mid-Atlantic", 2.0, -22.0),
                               ("Cape of Good Hope", -35.20, 18.50), ("Cape Agulhas", -35.50, 20.50)]),
    "MPM": dict(name="Maputo", country="Mozambique", lat=-25.97, lon=32.57, loa=270, beam=45, draft=14.2,
                tidal_range=3.0, rate=20000, congestion=2.0, dues=0.75, availability="Open · Matola coal terminal, dredged channel",
                hub="W", path=[("Maputo Bay entrance", -25.75, 33.10), ("South of Madagascar", -27.00, 45.50)]),
    "NAC": dict(name="Nacala", country="Mozambique", lat=-14.50, lon=40.72, loa=330, beam=60, draft=20.0,
                tidal_range=3.5, rate=50000, congestion=1.0, dues=0.7, availability="Open · Nacala-a-Velha deepwater coal terminal",
                hub="W", path=[("Nacala offing", -14.40, 41.20), ("Mozambique Channel North", -13.60, 46.50),
                               ("Cap d'Ambre", -11.20, 49.30)]),
    "VVO": dict(name="Vostochny", country="Russia", lat=42.73, lon=133.07, loa=300, beam=50, draft=16.5,
                tidal_range=0.4, rate=40000, congestion=2.0, dues=0.6, availability="Open · Pacific coal terminal",
                hub="E", path=[("Sea of Japan", 41.50, 133.00), ("Ulleung Basin", 36.50, 130.50),
                               ("Korea Strait", 34.60, 128.80), ("South of Jeju", 32.80, 126.60),
                               ("East China Sea", 29.00, 124.00), ("Taiwan Strait", 25.00, 120.00),
                               ("Off Hong Kong", 22.00, 117.50), ("South China Sea", 12.00, 111.50),
                               ("Off Con Son", 5.00, 106.00), ("Singapore Strait East", 1.35, 104.45),
                               ("Singapore Strait", 1.20, 103.80), ("Malacca Strait South", 2.20, 101.60),
                               ("One Fathom Bank", 3.20, 100.50), ("Malacca Strait North", 4.50, 99.00),
                               ("Off Lhokseumawe", 6.00, 97.00), ("Great Channel", 6.25, 95.30)]),
    "ULU": dict(name="Ust-Luga", country="Russia", lat=59.68, lon=28.40, loa=300, beam=50, draft=16.4,
                tidal_range=0.1, rate=35000, congestion=2.5, dues=0.65, availability="Open · Baltic coal terminal, winter ice Jan–Mar",
                hub="W", path=[("Gulf of Finland", 59.90, 26.50), ("Gulf of Finland West", 59.60, 23.00),
                               ("North of Gotland", 58.30, 20.50), ("East of Gotland", 57.30, 19.80),
                               ("East of Öland", 55.90, 16.60), ("Bornholmsgat", 55.30, 14.30),
                               ("Kadet Channel", 54.45, 12.20), ("Fehmarn Belt", 54.60, 11.00),
                               ("Great Belt", 55.35, 11.00), ("Kattegat", 56.20, 11.20),
                               ("East of Læsø", 57.30, 11.50), ("Skagen", 57.95, 10.80),
                               ("Skagerrak", 57.60, 8.00), ("North Sea", 53.50, 3.00),
                               ("Dover Strait", 51.00, 1.45), ("English Channel", 50.20, -1.00),
                               ("Off Ushant", 48.60, -5.90), ("Off Finisterre", 43.00, -9.90),
                               ("Off Madeira", 35.00, -13.00), ("West of Canaries", 29.00, -19.50),
                               ("Off Cape Verde", 14.00, -19.50), ("Cape of Good Hope", -35.20, 18.50),
                               ("Cape Agulhas", -35.50, 20.50)]),
}
HUBS = {"E": [("Bay of Bengal South", 6.00, 90.00)],
        "W": [("Dondra Head", 5.60, 80.60), ("Off Sri Lanka SE", 6.30, 82.30)]}
# Lane chokepoints that cap draft (others on these lanes are deeper than any bulker).
CHOKEPOINTS = {"Great Belt": 15.4}

# ---------------------------------------------------------------- weather zones
# Approximate seasonal hazard areas. bn = typical Beaufort when active; wind_from in degrees.
ZONES = [
    dict(id="bob_cyclone", name="Bay of Bengal cyclone belt", months=[4, 5, 10, 11, 12], bn=7, wind_from=45,
         poly=[(5, 80), (22, 86), (22, 92), (10, 95), (5, 92)]),
    dict(id="sw_monsoon", name="Southwest monsoon, North Indian Ocean", months=[6, 7, 8, 9], bn=6, wind_from=225,
         poly=[(5, 50), (22, 60), (22, 92), (5, 92)]),
    dict(id="scs_typhoon", name="South China Sea typhoons", months=[7, 8, 9, 10, 11], bn=8, wind_from=90,
         poly=[(8, 108), (22, 112), (25, 122), (18, 125), (8, 118)]),
    dict(id="ecs_typhoon", name="East China Sea typhoons", months=[7, 8, 9], bn=8, wind_from=135,
         poly=[(25, 120), (35, 125), (35, 135), (25, 135)]),
    dict(id="agulhas", name="Agulhas / Cape winter storms", months=[5, 6, 7, 8, 9], bn=8, wind_from=270,
         poly=[(-32, 15), (-32, 35), (-42, 35), (-42, 15)]),
    dict(id="bight", name="Great Australian Bight westerlies", months=[5, 6, 7, 8, 9], bn=7, wind_from=260,
         poly=[(-33, 115), (-33, 148), (-42, 148), (-42, 115)]),
    dict(id="swio_cyclone", name="SW Indian Ocean cyclones", months=[1, 2, 3], bn=7, wind_from=90,
         poly=[(-10, 35), (-10, 65), (-25, 65), (-25, 35)]),
    dict(id="nw_aus_cyclone", name="NW Australia cyclones", months=[12, 1, 2, 3, 4], bn=7, wind_from=90,
         poly=[(-10, 110), (-10, 125), (-22, 122), (-22, 110)]),
    dict(id="n_atlantic", name="North Atlantic winter gales", months=[11, 12, 1, 2, 3], bn=7, wind_from=250,
         poly=[(43, -12), (50, -5), (58, 5), (60, 0), (50, -20), (43, -20)]),
    dict(id="hurricane", name="Atlantic hurricane belt", months=[8, 9, 10], bn=8, wind_from=90,
         poly=[(10, -60), (30, -60), (32, -80), (25, -92), (18, -92), (10, -80)]),
    dict(id="baltic_ice", name="Baltic winter ice", months=[12, 1, 2, 3], bn=5, wind_from=45, ice=True,
         poly=[(59, 22), (61, 22), (61, 30.5), (59, 30.5)]),
]


def in_poly(lat, lon, poly):
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        (yi, xi), (yj, xj) = poly[i], poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def zones_at(lat, lon, month):
    return [z for z in ZONES if month in z["months"] and in_poly(lat, lon, z["poly"])]


# ---------------------------------------------------------------- geometry
def haversine_nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(h))


def bearing(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def gc_points(a, b, n):
    """n+1 points along the great circle a->b (for drawing and zone sampling)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = haversine_nm(a, b) / 3440.065
    if d == 0:
        return [a, b]
    out = []
    for i in range(n + 1):
        f = i / n
        A, B = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = A * math.cos(la1) * math.cos(lo1) + B * math.cos(la2) * math.cos(lo2)
        y = A * math.cos(la1) * math.sin(lo1) + B * math.cos(la2) * math.sin(lo2)
        z = A * math.sin(la1) + B * math.sin(la2)
        out.append((math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))))
    return out


def route_points(origin, dest):
    o, d = ORIGINS[origin], DESTINATIONS[dest]
    return ([(o["name"], o["lat"], o["lon"])] + o["path"] + HUBS[o["hub"]] + d["path"]
            + [(d["name"], d["lat"], d["lon"])])


def route_coords(origin, dest):
    pts = route_points(origin, dest)
    coords = [(pts[0][1], pts[0][2])]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(haversine_nm(a[1:], b[1:]) / 150))
        coords += gc_points(a[1:], b[1:], n)[1:]
    return [[round(la, 3), round(lo, 3)] for la, lo in coords]


# ---------------------------------------------------------------- weather
BEAUFORT_KN = [1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64]
BN_KN = [0, 2, 5, 9, 14, 19, 25, 31, 38, 44, 52, 60, 68]  # representative knots per Beaufort number


def beaufort(kn):
    return sum(kn >= t for t in BEAUFORT_KN)


def climate_wind(lat, lon, month):
    """Seasonal-norm wind (bn, from-degrees) when live data is unavailable."""
    z = zones_at(lat, lon, month)
    if z:
        worst = max(z, key=lambda q: q["bn"])
        return worst["bn"] - 1, worst["wind_from"]  # typical, not peak
    a = abs(lat)
    if a < 10:
        return 3, 90
    if a < 30:
        return 4, 60 if lat > 0 else 120  # trade winds
    if a < 40:
        return 4, 270
    return 6, 270  # westerlies


def kwon_speed_loss(bn, rel_angle, speed_kn, loa, dwt):
    """Kwon (2008) involuntary speed loss in %, laden bulk carrier (Cb ~0.85)."""
    if bn <= 0:
        return 0.0
    fn = speed_kn * 0.5144 / math.sqrt(9.81 * loa)
    cu = 3.1 - 18.7 * fn + 28.0 * fn ** 2
    disp = dwt * 1.2 / 1.025
    cform = 0.5 * bn + bn ** 6.5 / (2.7 * disp ** (2 / 3))
    if rel_angle <= 30:
        cb = 1.0
    elif rel_angle <= 60:
        cb = (1.7 - 0.03 * (bn - 4) ** 2) / 2
    elif rel_angle <= 150:
        cb = (0.9 - 0.06 * (bn - 6) ** 2) / 2
    else:
        cb = (0.4 - 0.03 * (bn - 8) ** 2) / 2
    return max(0.0, min(45.0, cb * cu * cform))


_wx_cache = {}


def _get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def live_weather(points):
    """Hourly 16-day wind/wave/current at points from Open-Meteo, or None. Cached 30 min."""
    key = tuple((round(a, 1), round(b, 1)) for a, b in points)
    hit = _wx_cache.get(key)
    if hit and time.time() - hit[0] < (1800 if hit[1] else 300):
        return hit[1]
    q = dict(latitude=",".join(str(p[0]) for p in key), longitude=",".join(str(p[1]) for p in key),
             forecast_days=16, timezone="UTC")
    out = None
    try:
        w = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
            dict(q, hourly="wind_speed_10m,wind_direction_10m", wind_speed_unit="kn")))
        w = w if isinstance(w, list) else [w]
        try:
            m = _get_json("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(
                dict(q, hourly="wave_height,ocean_current_velocity,ocean_current_direction")))
            m = m if isinstance(m, list) else [m]
        except Exception:
            m = [None] * len(w)
        out = []
        for wi, mi in zip(w, m):
            h = wi["hourly"]
            mh = (mi or {}).get("hourly", {})
            out.append(dict(time=h["time"], wind=h["wind_speed_10m"], dir=h["wind_direction_10m"],
                            wave=mh.get("wave_height"), cur=mh.get("ocean_current_velocity"),
                            cur_dir=mh.get("ocean_current_direction")))
    except Exception:
        out = None
    _wx_cache[key] = (time.time(), out)
    return out


def _at(series, i):
    if not series or i >= len(series):
        return None
    return series[i]


# ---------------------------------------------------------------- market model
def load_market(path=DATA):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    cols = {k: [] for k in rows[0]}
    for r in rows:
        for k in cols:
            v = r[k]
            cols[k].append(v if k == "date" else (float(v) if v not in ("", None) else None))
    return cols


class AdaptiveRLS:
    """Recursive least squares with exponential forgetting: weights track regime changes."""

    def __init__(self, n, lam=0.99, delta=0.05):
        self.w = [0.0] * n
        self.P = [[delta if i == j else 0.0 for j in range(n)] for i in range(n)]
        self.lam = lam

    def predict(self, x):
        return sum(wi * xi for wi, xi in zip(self.w, x))

    def update(self, x, y):
        n = len(x)
        Px = [sum(self.P[i][j] * x[j] for j in range(n)) for i in range(n)]
        g = self.lam + sum(x[i] * Px[i] for i in range(n))
        k = [p / g for p in Px]
        e = y - self.predict(x)
        self.w = [wi + ki * e for wi, ki in zip(self.w, k)]
        self.P = [[(self.P[i][j] - k[i] * Px[j]) / self.lam for j in range(n)] for i in range(n)]
        return e


FEATURES = ["bias", "momentum 1w", "momentum 4w", "momentum 13w", "deviation from 26w mean", "BDI 4w trend",
            "China PMI level", "China PMI change", "India IIP yoy", "IIV iron-ore price yoy",
            "World trade volume yoy", "season (sin)", "season (cos)"]


def index_series(m, key):
    """Vessel's own index; fall back to BDI (scaled) only where the sub-index is missing."""
    bdi = m["bdi"]
    own = m.get(key) or [None] * len(bdi)
    pairs = [(o / b) for o, b in zip(own, bdi) if o and b]
    ratio = sorted(pairs)[len(pairs) // 2] if pairs else 1.0
    out, fell_back = [], 0
    for o, b in zip(own, bdi):
        if o:
            out.append(o)
        else:
            out.append(b * ratio); fell_back += 1
    return out, fell_back


def features(m, s, t):
    ln = math.log
    yoy = lambda col: 10 * ln(m[col][t] / m[col][t - 52])
    week = dt.date.fromisoformat(m["date"][t]).isocalendar()[1]
    return [1.0,
            ln(s[t] / s[t - 1]), ln(s[t] / s[t - 4]), ln(s[t] / s[t - 13]),
            ln(s[t] / (sum(s[t - 25:t + 1]) / 26)),
            ln(m["bdi"][t] / m["bdi"][t - 4]),
            (m["china_pmi"][t] - 50) / 2, (m["china_pmi"][t] - m["china_pmi"][t - 4]) / 2,
            yoy("india_iip"), yoy("iron_ore_usd"), yoy("world_trade_idx"),
            math.sin(2 * math.pi * week / 52), math.cos(2 * math.pi * week / 52)]


def train_index(m, key, horizons_w=(1, 4, 9), warmup=52):
    s, fell_back = index_series(m, key)
    T = len(s)
    out = {"key": key, "name": INDEX_NAMES[key], "fallback_rows": fell_back, "horizons": {}}
    for h in horizons_w:
        model = AdaptiveRLS(len(FEATURES))
        raw, preds, var, errs, naive, bt = {}, {}, None, [], [], []
        v_model = v_naive = 0.01  # EWMA squared errors: RLS vs "no change"
        for t in range(warmup, T):
            if t - h >= warmup:  # target for prediction made at t-h is now known
                y = math.log(s[t] / s[t - h])
                p = preds[t - h]
                e = y - p
                var = e * e if var is None else 0.94 * var + 0.06 * e * e
                v_model = 0.94 * v_model + 0.06 * (y - raw[t - h]) ** 2
                v_naive = 0.94 * v_naive + 0.06 * y * y
                if t - h >= warmup + 26:  # score after the model has had half a year to learn
                    errs.append(abs(math.exp(p) * s[t - h] - s[t]) / s[t])
                    naive.append(abs(s[t - h] - s[t]) / s[t])
                bt.append((m["date"][t], s[t], math.exp(p) * s[t - h]))
                model.update(features(m, s, t - h), y)
            raw[t] = max(-0.6, min(0.6, model.predict(features(m, s, t))))
            # adaptive forecast combination: trust the regression as much as it has recently earned
            trust = v_naive / (v_model + v_naive)
            preds[t] = trust * raw[t]
        sd = math.sqrt(var or 0.01)
        mid = s[-1] * math.exp(preds[T - 1])
        out["horizons"][h] = dict(mid=mid, lo=mid * math.exp(-1.28 * sd), hi=mid * math.exp(1.28 * sd), sd=sd,
                                  mape=sum(errs) / len(errs), naive_mape=sum(naive) / len(naive), trust=trust,
                                  weights=list(zip(FEATURES, model.w)), backtest=bt[-78:])
    rets = [math.log(s[i] / s[i - 1]) for i in range(T - 8, T)]
    mu = sum(rets) / len(rets)
    out["vol_annual"] = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1) * 52)
    out["last"] = s[-1]
    out["series"] = s
    return out


class Market:
    def __init__(self, path=DATA):
        self.m = load_market(path)
        src = path.with_name("market_sources.json")
        self.sources = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
        self.models = {k: train_index(self.m, k) for k in INDEX_NAMES}
        self.last_date = dt.date.fromisoformat(self.m["date"][-1])

    def scenario(self, shocks):
        """What-if copy: the latest reading of each shocked series is moved (fraction, or PMI points) and
        every index is re-forecast with the weights already learned. No retraining: the question is how
        today's model reacts, not what it would learn from a world that didn't happen."""
        if not shocks:
            return self
        sc = copy.copy(self)
        sc.m = {k: (list(v) if k in shocks else v) for k, v in self.m.items()}
        for k, x in shocks.items():
            sc.m[k][-1] = sc.m[k][-1] + x if k == "china_pmi" else sc.m[k][-1] * (1 + x)
        sc.models = {}
        for key, mod in self.models.items():
            s, _ = index_series(sc.m, key)
            x = features(sc.m, s, len(s) - 1)
            hz = {}
            for h, v in mod["horizons"].items():
                raw = max(-0.6, min(0.6, sum(w * xi for (_, w), xi in zip(v["weights"], x))))
                mid = s[-1] * math.exp(v["trust"] * raw)
                hz[h] = dict(v, mid=mid, lo=mid * math.exp(-1.28 * v["sd"]), hi=mid * math.exp(1.28 * v["sd"]))
            sc.models[key] = dict(mod, horizons=hz, last=s[-1], series=s)
        return sc

    def export(self, path):
        """Write every learned weight, plus each model's backtest score, to JSON for inspection."""
        out = {"trained_to": self.m["date"][-1], "features": FEATURES, "models": {}}
        for k, mod in self.models.items():
            out["models"][INDEX_NAMES[k]] = {
                f"{h}w_ahead": dict(weights={n: round(w, 6) for n, w in v["weights"]},
                                    model_trust=round(v["trust"], 3), mape=round(v["mape"], 4),
                                    naive_mape=round(v["naive_mape"], 4))
                for h, v in mod["horizons"].items()}
        Path(path).write_text(json.dumps(out, indent=1), encoding="utf-8")

    def index_at(self, key, days):
        """Forecast index at +days (interpolating the 0/1/4/9-week anchors)."""
        mod = self.models[key]
        pts = [(0, mod["last"], mod["last"], mod["last"])] + [
            (h * 7, v["mid"], v["lo"], v["hi"]) for h, v in sorted(mod["horizons"].items())]
        for (d0, m0, l0, h0), (d1, m1, l1, h1) in zip(pts, pts[1:]):
            if days <= d1:
                f = (days - d0) / (d1 - d0)
                return dict(mid=m0 + (m1 - m0) * f, lo=l0 + (l1 - l0) * f, hi=h0 + (h1 - h0) * f)
        d, mi, lo, hi = pts[-1]
        return dict(mid=mi, lo=lo, hi=hi)


# ---------------------------------------------------------------- permission
def permission(cls, dwt, origin, dest):
    v = vessel_dims(cls, dwt)
    o, d = ORIGINS[origin], DESTINATIONS[dest]
    checks, hard_fail = [], False
    for port in (o, d):
        for item, unit in (("loa", "m"), ("beam", "m")):
            ok = v[item] <= port[item]
            hard_fail |= not ok
            checks.append(dict(port=port["name"], item=item.upper(), vessel=v[item], limit=port[item], unit=unit, ok=ok))
    limits = [(o["draft"], o["name"]), (d["draft"], d["name"])]
    names = {p[0] for p in route_points(origin, dest)}
    limits += [(dr, n) for n, dr in CHOKEPOINTS.items() if n in names]
    draft_limit, limiting = min(limits)
    for dr, n in limits:
        checks.append(dict(port=n, item="Draft", vessel=v["draft"], limit=dr, unit="m", ok=v["draft"] <= dr))
    intake = dwt * 0.95  # less bunkers, stores, constant
    excess_cm = max(0.0, (v["draft"] - draft_limit) * 100)
    cargo = max(0.0, intake - excess_cm * v["tpc"])
    if hard_fail:
        status = "forbidden"
    elif cargo < 0.4 * intake:
        status = "not_viable"
    elif excess_cm > 0:
        status = "part_cargo"
    else:
        status = "permitted"
    fails = [c for c in checks if not c["ok"] and c["item"] != "Draft"]
    reason = ("; ".join(f"{c['item']} {c['vessel']} {c['unit']} > {c['limit']} {c['unit']} at {c['port']}" for c in fails)
              if fails else f"draft {v['draft']} m vs {draft_limit} m limit at {limiting}")
    return dict(status=status, checks=checks, cargo=round(cargo), intake=round(intake), draft_limit=draft_limit,
                limiting=limiting, reason=reason, vessel=v,
                ukc_margin=round(draft_limit - min(v["draft"], draft_limit), 2) if excess_cm == 0 else 0.0)


def max_full_dwt(cls, origin, dest):
    """Largest DWT in class that still loads full cargo within draft limits (slider tick)."""
    lo, hi = VESSELS[cls]["dwt"]
    for dwt in range(hi, lo - 1, -500):
        p = permission(cls, dwt, origin, dest)
        if p["status"] == "permitted":
            return dwt
    return None


# ---------------------------------------------------------------- voyage
def congestion_days(port, month, india):
    k = 1.4 if india and month in (6, 7, 8, 9) else 1.0
    if port["name"] == "Ust-Luga" and month in (1, 2, 3):
        k = 1.3
    return port["congestion"] * k


def sail(origin, dest, cls, dwt, start, wx, ballast=False):
    """Legs with distance, heading, wind, speed loss and days. start = departure date."""
    pts = route_points(origin, dest)
    if ballast:
        pts = pts[::-1]
    v = vessel_dims(cls, dwt)
    base_speed = v["ballast_speed"] if ballast else v["speed"]
    legs, elapsed = [], 0.0
    today = dt.date.today()
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        A, B = a[1:], b[1:]
        nm = haversine_nm(A, B)
        hdg = bearing(A, B)
        mid = gc_points(A, B, 2)[1]
        when = start + dt.timedelta(days=elapsed + nm / (base_speed * 24) / 2)
        src, wave, cur_along = "seasonal norm", None, 0.0
        bn, wfrom = climate_wind(mid[0], mid[1], when.month)
        wind_kn = BN_KN[bn]
        hour = int(((when - today).days) * 24 + 12)
        if wx and not ballast and 0 <= hour < len(wx[i]["time"]):
            w = wx[i]
            if _at(w["wind"], hour) is not None:
                wind_kn, wfrom, src = w["wind"][hour], w["dir"][hour], "live forecast"
                bn = beaufort(wind_kn)
                wave = _at(w["wave"], hour)
                cv, cd = _at(w["cur"], hour), _at(w["cur_dir"], hour)
                if cv is not None and cd is not None:
                    cur_along = cv / 1.852 * math.cos(math.radians(cd - hdg))  # km/h -> kn
        rel = abs((wfrom - hdg + 180) % 360 - 180)
        loss = kwon_speed_loss(bn, rel, base_speed, v["loa"], dwt * (0.6 if ballast else 1))
        sog = max(5.0, base_speed * (1 - loss / 100) + cur_along)
        days = nm / (sog * 24)
        elapsed += days
        zs = {z["id"]: z["name"] for p in gc_points(A, B, 6) for z in zones_at(p[0], p[1], when.month)}
        legs.append(dict(frm=a[0], to=b[0], mid=[round(mid[0], 2), round(mid[1], 2)], nm=round(nm), heading=round(hdg), bn=bn, wind_kn=round(wind_kn, 1),
                         wind_from=round(wfrom), rel=round(rel), wave_m=wave, current_kn=round(cur_along, 2),
                         loss_pct=round(loss, 1), speed_kn=round(sog, 2), days=round(days, 2), source=src,
                         eta=(start + dt.timedelta(days=elapsed)).isoformat(), zones=list(zs.values())))
    return legs


def voyage_cost(market, origin, dest, cls, dwt, offset_days, wx, perm=None):
    o, d = ORIGINS[origin], DESTINATIONS[dest]
    vs = VESSELS[cls]
    perm = perm or permission(cls, dwt, origin, dest)
    fix = dt.date.today() + dt.timedelta(days=offset_days)
    idx = market.index_at(vs["index"], offset_days)
    size = (dwt / vs["std_dwt"]) ** 0.5
    hire = {k: idx[k] * vs["tce_factor"] * size for k in ("mid", "lo", "hi")}
    cargo = perm["cargo"] or 1
    load_days = cargo / o["rate"] + 0.5
    legs = sail(origin, dest, cls, dwt, fix + dt.timedelta(days=load_days), wx)
    sea = sum(l["days"] for l in legs)
    port = load_days + cargo / d["rate"] + 0.5
    v = perm["vessel"]
    tidal = sum(0.25 for p in (o, d) if min(v["draft"], perm["draft_limit"]) > p["draft"] - p["tidal_range"])
    wait = congestion_days(o, fix.month, False) + congestion_days(d, (fix + dt.timedelta(days=sea)).month, True) + tidal
    vlsfo = market.m["vlsfo_usd_t"][-1]

    def cost(h):
        c = dict(hire_sea=h * sea, hire_port=h * port, hire_wait=h * wait,
                 bunkers=sea * vs["fuel_sea"] * vlsfo + (port + wait) * vs["fuel_port"] * vlsfo * 1.3,
                 port_dues=(o["dues"] + d["dues"]) * dwt)
        c["commission"] = 0.0375 * (c["hire_sea"] + c["hire_port"] + c["hire_wait"])
        return c

    c = cost(hire["mid"])
    total = sum(c.values())
    tot_lo, tot_hi = sum(cost(hire["lo"]).values()), sum(cost(hire["hi"]).values())
    return dict(fix_date=fix.isoformat(), index=idx, hire_day=hire, days=dict(sea=sea, port=port, wait=wait, tidal=tidal,
                total=sea + port + wait), cost=c, total=total, total_lo=tot_lo, total_hi=tot_hi,
                per_t=total / cargo, cargo=cargo, legs=legs, vlsfo=vlsfo,
                weather_source="live forecast" if any(l["source"] == "live forecast" for l in legs) else "seasonal norm",
                arrival=legs[-1]["eta"] if legs else fix.isoformat())


def leg_midpoints(origin, dest):
    pts = route_points(origin, dest)
    return [gc_points(a[1:], b[1:], 2)[1] for a, b in zip(pts, pts[1:])]


def forecast(market, origin, dest, cls, dwt, use_live=True):
    if origin not in ORIGINS or dest not in DESTINATIONS or cls not in VESSELS:
        raise ValueError("unknown origin, destination or vessel class")
    lo, hi = VESSELS[cls]["dwt"]
    dwt = int(min(hi, max(lo, dwt)))
    vs = VESSELS[cls]
    perm = permission(cls, dwt, origin, dest)
    wx = live_weather(leg_midpoints(origin, dest)) if use_live else None
    hz = []
    for key, days in HORIZONS:
        r = voyage_cost(market, origin, dest, cls, dwt, days, wx, perm)
        r["key"], r["offset"] = key, days
        hz.append(r)
    best = min(hz, key=lambda r: r["total"])
    now = hz[0]
    # continuous fixing curve: one voyage priced per fixing day, today to +60
    # accuracy = 1 − backtest MAPE of this index's forecast, interpolated between the 1/4/9-week models
    mh = market.models[vs["index"]]["horizons"]
    anchors = [(0, 0.0)] + [(h * 7, mh[h]["mape"]) for h in sorted(mh)]

    def accuracy(d):
        if d == 0:
            return None  # today's rate is observed, not forecast
        for (d0, e0), (d1, e1) in zip(anchors, anchors[1:]):
            if d <= d1:
                return 1 - (e0 + (e1 - e0) * (d - d0) / (d1 - d0))
        return 1 - anchors[-1][1]

    curve = []
    for d in range(0, 61):
        r = hz[[o for _, o in HORIZONS].index(d)] if d in (0, 7, 30, 60) else voyage_cost(market, origin, dest, cls, dwt, d, wx, perm)
        curve.append(dict(day=d, date=r["fix_date"], total=r["total"], lo=r["total_lo"], hi=r["total_hi"], per_t=r["per_t"],
                          hire=r["hire_day"]["mid"], cost=r["cost"], days=r["days"], weather=r["weather_source"],
                          accuracy=accuracy(d)))
    best_day = min(curve, key=lambda c: c["total"])
    mod = market.models[vs["index"]]

    # vessel type optimisation: every class at its typical size on this route
    fit = []
    for k, spec in VESSELS.items():
        p = permission(k, spec["typical"], origin, dest)
        row = dict(cls=k, name=spec["name"], dwt=spec["typical"], status=p["status"], reason=p["reason"], cargo=p["cargo"])
        if p["status"] in ("permitted", "part_cargo"):
            r = voyage_cost(market, origin, dest, k, spec["typical"], 0, wx, p)
            row.update(total=r["total"], per_t=r["per_t"], days=r["days"]["total"])
        fit.append(row)
    viable = [f for f in fit if "per_t" in f]
    best_fit = min(viable, key=lambda f: f["per_t"])["cls"] if viable else None

    # idle / deadheading
    hire_now = now["hire_day"]["mid"]
    back = sail(origin, dest, cls, dwt, dt.date.fromisoformat(now["arrival"]), None, ballast=True)
    back_days = sum(l["days"] for l in back)
    repos = []
    for ok, op in ORIGINS.items():
        bl = sail(ok, dest, cls, dwt, dt.date.fromisoformat(now["arrival"]), None, ballast=True)
        repos.append(dict(port=op["name"], code=ok, country=op["country"], days=round(sum(l["days"] for l in bl), 1)))
    repos.sort(key=lambda r: r["days"])
    fuel_ballast = vs["fuel_sea"] * 0.85 * now["vlsfo"]
    nearest = repos[0]
    idle = dict(back_days=round(back_days, 1), back_cost=back_days * (hire_now + fuel_ballast), reposition=repos[:4])
    soft = hz[2]["index"]["mid"] < now["index"]["mid"] * 0.92
    advice = []
    if nearest["code"] != origin:
        saved = back_days - nearest["days"]
        advice.append(f"Seek backhaul or next cargo at {nearest['port']} ({nearest['country']}): "
                      f"{nearest['days']} days ballast vs {back_days:.1f} back to {ORIGINS[origin]['name']}, "
                      f"saving about {saved:.1f} days (${saved * (hire_now + fuel_ballast):,.0f}).")
    else:
        advice.append(f"{ORIGINS[origin]['name']} is already the nearest loading area from {DESTINATIONS[dest]['name']}; "
                      f"line up the next cargo here to keep the ballast leg to {back_days:.1f} days.")
    if soft:
        advice.append("Demand softens over the next 30 days: fix the onward cargo before discharge, or "
                      "relet the ship on a short trip charter rather than waiting idle.")
    else:
        advice.append("Market holds or firms over 30 days: idle risk is low; a 2–3 voyage contract keeps the ship employed.")
    idle["advice"] = advice

    # contract recommendation (spot vs short/mid-term multi-voyage)
    trend = hz[3]["index"]["mid"] / now["index"]["mid"] - 1
    round_trip = now["days"]["total"] + back_days
    per_90 = max(1, int(90 // round_trip))
    if trend > 0.05:
        contract = dict(kind="Short-term contract now",
                        text=f"Rates are forecast to firm {trend:.0%} over 60 days. Lock a 3-month, {per_90}-voyage "
                             f"contract at today's level instead of fixing each voyage on the spot market.")
    elif trend < -0.05:
        contract = dict(kind="Single voyages, then term",
                        text=f"Rates are forecast to soften {-trend:.0%} over 60 days. Fix single voyages for now and "
                             f"lock a 6-month contract near the forecast low.")
    else:
        contract = dict(kind="Mid-term contract",
                        text=f"A flat outlook ({trend:+.0%} over 60 days). A 6-month multi-voyage contract "
                             f"(about {per_90 * 2} voyages) secures tonnage without paying for timing.")

    # risks
    risks = []
    vol = mod["vol_annual"]
    if vol > 0.6:
        risks.append(dict(level="high", title="Volatile market", detail=f"{mod['name']} 8-week volatility is {vol:.0%} annualised."))
    elif vol > 0.4:
        risks.append(dict(level="watch", title="Elevated volatility", detail=f"{mod['name']} 8-week volatility is {vol:.0%} annualised."))
    spread = (hz[3]["total_hi"] - hz[3]["total_lo"]) / hz[3]["total"]
    if spread > 0.3:
        risks.append(dict(level="watch", title="Wide 60-day range",
                          detail=f"The 60-day cost could land anywhere in a ±{spread / 2:.0%} band; don't commit on the midpoint alone."))
    for p, india in ((ORIGINS[origin], False), (DESTINATIONS[dest], True)):
        cd = congestion_days(p, dt.date.fromisoformat(now["arrival"]).month if india else dt.date.today().month, india)
        if cd >= 2.5:
            risks.append(dict(level="watch", title=f"Congestion at {p['name']}",
                              detail=f"About {cd:.1f} days of waiting expected; it is already in the cost."))
    zone_hits = {}
    for r in hz:
        for l in r["legs"]:
            for z in l["zones"]:
                zone_hits.setdefault(z, []).append(r["key"])
    labels = dict(now="now", d7="+7 d", d30="+30 d", d60="+60 d")
    for z, keys in zone_hits.items():
        ks = sorted(set(keys), key=[h[0] for h in HORIZONS].index)
        risks.append(dict(level="high" if "cyclone" in z or "typhoon" in z or "hurricane" in z else "watch",
                          title=z, detail="On the route when fixing " + ", ".join(labels[k] for k in ks) + "."))
    if perm["status"] == "part_cargo":
        risks.append(dict(level="watch", title="Draft-restricted",
                          detail=f"{perm['limiting']} limits draft to {perm['draft_limit']} m: loads {perm['cargo']:,} t of {perm['intake']:,} t."))
    elif perm["status"] == "permitted" and perm["ukc_margin"] < 0.3:
        risks.append(dict(level="watch", title="Tight draft margin",
                          detail=f"Only {perm['ukc_margin']} m spare at {perm['limiting']}; a tide or trim error stops the ship."))
    if mod["fallback_rows"]:
        risks.append(dict(level="info", title="Index fallback",
                          detail=f"{mod['fallback_rows']} weeks of {mod['name']} were missing and filled from BDI."))

    h4 = mod["horizons"][4]
    n = 104
    mk = market.m
    return dict(
        inputs=dict(origin=origin, dest=dest, cls=cls, dwt=dwt),
        latest={k: mk[k][-1] for k in SHOCK_LIMITS},
        vessel=perm["vessel"] | dict(index_name=mod["name"]),
        permission={k: perm[k] for k in ("status", "checks", "cargo", "intake", "draft_limit", "limiting", "reason")},
        route=dict(legs=now["legs"], total_nm=sum(l["nm"] for l in now["legs"]), coords=route_coords(origin, dest)),
        horizons=[{k: r[k] for k in ("key", "offset", "fix_date", "arrival", "index", "hire_day", "days", "cost", "total",
                                      "total_lo", "total_hi", "per_t", "cargo", "weather_source")}
                  | dict(legs=[{k: l[k] for k in ("frm", "to", "days", "loss_pct", "bn", "source")} for l in r["legs"]])
                  for r in hz],
        best=best["key"], saving=now["total"] - best["total"],
        curve=curve, best_day=best_day["day"], best_saving=now["total"] - best_day["total"],
        contract=contract, vessel_fit=fit, best_fit=best_fit, idle=idle, risks=risks,
        market=dict(index=mod["name"], dates=mk["date"][-n:], series=[round(x) for x in mod["series"][-n:]],
                    bdi=mk["bdi"][-n:], pmi=mk["china_pmi"][-n:], iip=mk["india_iip"][-n:],
                    iiv=mk["iron_ore_usd"][-n:], trade=mk["world_trade_idx"][-n:],
                    forecast=[dict(days=d, **market.index_at(vs["index"], d)) for d in (0, 7, 30, 60)],
                    backtest=[dict(date=a, actual=round(b), predicted=round(c)) for a, b, c in h4["backtest"]],
                    weights=[dict(name=a, w=round(b, 4)) for a, b in h4["weights"][1:]],
                    mape=h4["mape"], naive_mape=h4["naive_mape"], vol=vol, last_date=mk["date"][-1]),
    )


def meta(market):
    month = dt.date.today().month
    return dict(
        origins={k: {f: v[f] for f in ("name", "country", "lat", "lon", "loa", "beam", "draft", "tidal_range", "rate",
                                       "congestion", "availability")} for k, v in ORIGINS.items()},
        destinations={k: {f: v[f] for f in ("name", "country", "lat", "lon", "loa", "beam", "draft", "tidal_range", "rate",
                                            "congestion", "availability")} for k, v in DESTINATIONS.items()},
        vessels={k: dict(name=v["name"], dwt=v["dwt"], typical=v["typical"], index=INDEX_NAMES[v["index"]])
                 for k, v in VESSELS.items()},
        corridors=[dict(origin=o, dest=d, coords=route_coords(o, d)) for o in ORIGINS for d in DESTINATIONS],
        zones=[dict(id=z["id"], name=z["name"], months=z["months"], poly=z["poly"], active=month in z["months"],
                    ice=z.get("ice", False)) for z in ZONES],
        data=dict(last_date=market.m["date"][-1], weeks=len(market.m["date"]), sources=market.sources,
                  label="Live market data" if market.sources.get("live") else "Saved market data"),
    )


def slider_limits(origin, dest):
    return {k: max_full_dwt(k, origin, dest) for k in VESSELS}
