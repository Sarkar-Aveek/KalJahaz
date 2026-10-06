# KalJahaz

**Freight forecasting and chartering decisions for dry bulk imports to India's East Coast.**
Smart India Hackathon 2026 · Problem statement SIH-26006 · Team BcB Encoders

**Live demo:** https://kaljahaz.pages.dev

## What makes it different

- **A model that adapts to the market.** Freight markets change regime fast: a Chinese stimulus, a strait closure or a monsoon can change the rules within weeks. A model trained once and frozen goes stale. KalJahaz's 15 adaptive models update their weights with every new weekly close and gradually forget old regimes. There is no retraining job, no GPU and no stored model file to go stale. In a walk-forward backtest on real Baltic data it matches or beats XGBoost on every index tested.
- **The right index for the right ship.** Each vessel class is forecast on its own Baltic index: Capesize on the BCI, Panamax on the BPI, Supramax on the BSI and Handysize on the BHSI. The headline BDI only fills gaps.
- **Honest about uncertainty.** Every price carries its measured forecast accuracy and an 80% range, and every data source is labelled live or saved. Monthly macro figures are used only after they are published, so the backtest never sees the future.
- **A decision, not just a forecast.** KalJahaz prices the whole voyage for every fixing day up to 60 days ahead and names the cheapest date. It checks whether the ship fits both ports, and costs each leg using live wind, waves and currents.
- **Light enough to run anywhere.** It needs only Python's standard library, with no packages to install, and the models train in under a second when the server starts. It runs on an ordinary laptop or inside a locked-down government network.

## Overview

KalJahaz helps a chartering manager decide **when to fix a charter, which vessel to use, and what the voyage will really cost**. Pick a load port, a discharge port, a vessel class and a deadweight; KalJahaz forecasts the freight market, prices the whole voyage for every fixing day over the next 60 days, and recommends the cheapest date, with an honest accuracy figure next to every price.

## What it does

- **Forecasts each vessel's own index.** Capesize uses the BCI, Panamax the BPI, Supramax the BSI and Handysize the BHSI; the BDI only fills gaps. Inputs also include China PMI, India IIP, iron-ore price (IIV) and world trade volume.
- **Learns continuously.** 15 adaptive recursive-least-squares models (5 indices × 1, 4 and 9 weeks ahead) relearn every week and are blended with a "no change" forecast according to their recent accuracy.
- **Recommends a fixing date.** The voyage is priced for every day from today to +60; the dashboard shows today, +7, +30, +60 and the cheapest date, each with forecast accuracy and estimated voyage time.
- **Checks port fit.** LOA, beam and draft limits at both ports and on every strait; draft-limited voyages are priced as part cargo, and a keel chart compares the ship with all five discharge ports.
- **Prices the voyage leg by leg.** Sea-lane waypoints, live wind, waves and currents (Kwon speed loss), tides, port time and congestion → days at sea and total cost (hire, bunkers, port dues, commissions) in US$ or ₹.
- **Plans ahead.** Spot vs term contract advice, backhaul and repositioning to cut idle ballast time, early warnings (volatility, congestion, cyclones and other seasonal hazards), and what-if sliders for every market and macro input.

Ports covered: Visakhapatnam, Chennai, Kamarajar, Paradip and Syama Prasad Mookerjee (Haldia), with load ports in Australia, Indonesia, the USA, Mozambique and Russia.

## Run it locally

Needs Python 3.10+ and nothing else. There are no packages to install.

```
python server.py        # Windows: py server.py
```

Open http://localhost:8000. On start the server pulls live market data (falling back to the saved copy in `data/` when offline) and trains the models in under a second.

Self-checks:

```
python test_model.py    # prints "ok"
```

Deep link to a scenario: `http://localhost:8000/?origin=NEW&dest=PRT&cls=panamax&dwt=82000`

## Project layout

| Path | What it is |
|---|---|
| `model.py` | Adaptive RLS models, ports, vessels, sea lanes, weather zones, Kwon speed loss, voyage cost, recommendations |
| `live_data.py` | Rebuilds `data/market.csv` from live sources; records each series' source and freshness |
| `server.py` | Standard-library HTTP server: static files plus `/api/forecast`, `/api/meta`, `/api/limits`, `/api/fx`, `/api/status` |
| `web/` | The dashboard (HTML, CSS, JavaScript, Chart.js, Leaflet) |
| `web/config.js` | Backend address used when the site is hosted on Cloudflare Pages |
| `data/` | Weekly market data, data-source record and the latest learned model weights |
| `deploy/go-live.ps1` | Publishes the site to Cloudflare Pages with this machine as the backend |
| `test_model.py` | Assertion checks for the model, port rules and voyage maths |

## Deployment

The public site is static `web/` on Cloudflare Pages; forecasts come from the Python server through a Cloudflare quick tunnel. After any restart:

```
py server.py
deploy\go-live.ps1
```

The script starts a tunnel, waits until it is reachable, writes its address into `web/config.js` and redeploys the site. Needs `cloudflared` and `wrangler` logged in to the Cloudflare account that owns the `kaljahaz` project.

## Data sources

| Series | Source | Status |
|---|---|---|
| BDI, BCI, BPI, BSI, BHSI | Baltic Exchange closes via [yieldchaser/Shipping](https://github.com/yieldchaser/Shipping) | Live, daily |
| China PMI | NBS via [DBnomics](https://db.nomics.world/NBS/M_A0B01) | Live, monthly |
| Iron-ore price (IIV) | IMF via [FRED](https://fred.stlouisfed.org/series/PIORECRUSDM) | Live, monthly |
| Weather, waves, currents | [Open-Meteo](https://open-meteo.com/) | Live, 16-day forecast |
| USD/INR | ECB reference rate via Frankfurter | Live, daily |
| Vessel traffic | VesselFinder AIS map | Live |
| India IIP, world trade volume, bunker price | Saved series | No free current feed |

The Baltic index data is a public scrape, not a licensed Baltic Exchange feed, and port limits are compiled from public sources; a production deployment would use licensed and official feeds. Monthly figures enter the model only after the month they cover, so backtests never see data before it was published.

## Model performance

Walk-forward backtest on real Baltic data (every forecast made with only the data available at the time), 4-week horizon, average error:

| Index | KalJahaz | XGBoost | No change |
|---|---|---|---|
| Capesize (BCI) | **26.3%** | 26.9% | 28.4% |
| Supramax (BSI) | **9.8%** | 10.7% | 10.2% |
| BDI | **16.1%** | 16.3% | 16.2% |
| Panamax (BPI) | 12.8% | 12.8% | **12.6%** |

Freight a few weeks ahead is close to a random walk, so the gains are modest. The dashboard shows a forecast range and an accuracy figure next to every price rather than claiming more precision than the data supports.

## Versioning

Releases follow [semantic versioning](https://semver.org/) and are marked with git tags (`git tag -l`).

### v1.0.0 (2026-10-06)

First release: adaptive forecasting on live data, fixing-date recommendation with accuracy and voyage time, port fit and keel chart, leg-by-leg voyage cost, what-if scenarios, maritime map with live AIS traffic, USD/INR display, and Cloudflare Pages deployment.
