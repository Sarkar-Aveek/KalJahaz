"""Run: py test_model.py  (no live weather; asserts only)"""
import model

# adaptive RLS recovers a known linear rule, then re-learns after a regime change
rls = model.AdaptiveRLS(2, lam=0.95, delta=100)
for i in range(200):
    x = [1.0, (i % 17) / 17]
    rls.update(x, 0.5 + 2 * x[1] if i < 100 else -1 + 0.5 * x[1])
assert abs(rls.w[0] + 1) < 0.05 and abs(rls.w[1] - 0.5) < 0.05, rls.w

# geometry: Chennai to Paradip is ~565 nm great-circle
assert 540 < model.haversine_nm((13.10, 80.31), (20.26, 86.69)) < 590

# port rules: Capesize can't pass Haldia's lock; Panamax is draft-limited there; Paradip takes Panamax full
assert model.permission("capesize", 180000, "NEW", "HAL")["status"] == "forbidden"
p = model.permission("panamax", 82000, "NEW", "HAL")
assert p["status"] == "part_cargo" and p["cargo"] < p["intake"]
assert model.permission("panamax", 82000, "NEW", "PRT")["status"] == "permitted"
# Baltic Capesize is capped by the Great Belt, not the load port
assert model.permission("capesize", 180000, "ULU", "PRT")["limiting"] == "Great Belt"

# Kwon: head seas cost more speed than following seas, calm costs nothing
assert model.kwon_speed_loss(6, 0, 13, 225, 82000) > model.kwon_speed_loss(6, 180, 13, 225, 82000) >= 0
assert model.kwon_speed_loss(0, 0, 13, 225, 82000) == 0

# end to end: four horizons, the recommended one is the cheapest, costs add up
M = model.Market()
r = model.forecast(M, "TAB", "PRT", "supramax", 58000, use_live=False)
assert [h["key"] for h in r["horizons"]] == ["now", "d7", "d30", "d60"]
assert r["best"] == min(r["horizons"], key=lambda h: h["total"])["key"]
for h in r["horizons"]:
    assert abs(sum(h["cost"].values()) - h["total"]) < 1 and h["total_lo"] <= h["total"] <= h["total_hi"]
print("ok")
