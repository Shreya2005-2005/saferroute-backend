from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from risk_engine import RiskEngine

app = FastAPI(title="SaferRoute AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

print("Starting SaferRoute AI...")
engine = RiskEngine()
print("All systems ready!")

OSRM = "http://router.project-osrm.org/route/v1/driving"
live_locations = {}

class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat:   float
    end_lon:   float

class LocationUpdate(BaseModel):
    user_id:    str
    lat:        float
    lon:        float
    share_code: str

@app.get("/")
def root():
    return {"message": "SaferRoute AI is running ✅"}

@app.get("/stats")
def get_stats():
    return engine.get_stats()

@app.get("/heatmap")
def get_heatmap():
    return {"data": engine.get_heatmap()}

def get_safe_waypoints(start_lat, start_lon, end_lat, end_lon, engine, n=2):
    """
    Find real low-risk waypoints from our accident data grid.
    These are actual safe zones between start and end — not random nudges.
    """
    # Bounding box between start and end with padding
    min_lat = min(start_lat, end_lat) - 0.02
    max_lat = max(start_lat, end_lat) + 0.02
    min_lon = min(start_lon, end_lon) - 0.02
    max_lon = max(start_lon, end_lon) + 0.02

    # Filter grid to area between start and end
    region = engine.grid[
        (engine.grid['lat_bin'] >= min_lat) &
        (engine.grid['lat_bin'] <= max_lat) &
        (engine.grid['lon_bin'] >= min_lon) &
        (engine.grid['lon_bin'] <= max_lon)
    ].copy()

    if region.empty:
        return []

    # Get lowest risk zones in this region
    safest_zones = region.nsmallest(50, 'risk_score')

    if safest_zones.empty:
        return []

    # We want waypoints that are actually BETWEEN start and end
    # Divide the journey into n+1 segments and pick best safe zone near each segment midpoint
    waypoints = []
    for i in range(1, n + 1):
        # Target lat/lon at this fraction of journey
        frac = i / (n + 1)
        target_lat = start_lat + frac * (end_lat - start_lat)
        target_lon = start_lon + frac * (end_lon - start_lon)

        # Find lowest-risk zone closest to this target point
        safest_zones = safest_zones.copy()
        safest_zones['dist_to_target'] = (
            (safest_zones['lat_bin'] - target_lat) ** 2 +
            (safest_zones['lon_bin'] - target_lon) ** 2
        )
        # Weight by risk_score and distance — want low risk AND near target
        safest_zones['score'] = safest_zones['risk_score'] + safest_zones['dist_to_target'] * 5

        best = safest_zones.nsmallest(1, 'score').iloc[0]
        waypoints.append((float(best['lat_bin']), float(best['lon_bin'])))

    return waypoints

@app.post("/route")
def find_route(req: RouteRequest):
    url = (
        f"{OSRM}/{req.start_lon},{req.start_lat};"
        f"{req.end_lon},{req.end_lat}"
        f"?overview=full&geometries=geojson&alternatives=true&steps=true"
    )

    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Routing service error: {e}")

    if data.get('code') != 'Ok' or not data.get('routes'):
        raise HTTPException(status_code=404, detail="No route found between these points")

    scored_routes = []
    for i, route in enumerate(data['routes'][:3]):
        coords = route['geometry']['coordinates']
        risk_score, risky_spots = engine.get_route_risk(coords)

        steps = []
        for leg in route.get('legs', []):
            for step in leg.get('steps', []):
                m = step.get('maneuver', {})
                steps.append({
                    "name":       step.get('name', ''),
                    "type":       m.get('type', ''),
                    "modifier":   m.get('modifier', ''),
                    "distance_m": round(step.get('distance', 0))
                })

        scored_routes.append({
            "route_id":    i,
            "coords":      coords,
            "distance_km": round(route['distance'] / 1000, 2),
            "duration_min":round(route['duration'] / 60, 1),
            "risk_score":  risk_score,
            "risk_label":  engine._score_to_level(risk_score),
            "risk_points": risky_spots[:15],
            "steps":       steps[:30]
        })

    fastest = dict(scored_routes[0])
    fastest['route_type'] = 'fastest'
    fastest['route_label'] = '⚡ Fastest'

    by_risk = sorted(scored_routes, key=lambda r: r['risk_score'])

    balanced = dict(by_risk[0])
    balanced['route_type'] = 'balanced'
    balanced['route_label'] = '🛡 Balanced'

    # Most Cautious: route through actual safe zones from our accident data
    cautious = None
    safe_waypoints = get_safe_waypoints(
        req.start_lat, req.start_lon,
        req.end_lat, req.end_lon,
        engine, n=2
    )

    if safe_waypoints:
        # Build OSRM URL with safe zone waypoints in between
        wp_str = ";".join([f"{lon},{lat}" for lat, lon in safe_waypoints])
        cautious_url = (
            f"{OSRM}/{req.start_lon},{req.start_lat};"
            f"{wp_str};"
            f"{req.end_lon},{req.end_lat}"
            f"?overview=full&geometries=geojson&steps=true"
        )
        try:
            cr = requests.get(cautious_url, timeout=15)
            cd = cr.json()
            if cd.get('code') == 'Ok' and cd.get('routes'):
                cr_route = cd['routes'][0]
                cr_coords = cr_route['geometry']['coordinates']
                cr_risk, cr_spots = engine.get_route_risk(cr_coords)
                cr_steps = []
                for leg in cr_route.get('legs', []):
                    for step in leg.get('steps', []):
                        m = step.get('maneuver', {})
                        cr_steps.append({
                            "name":       step.get('name', ''),
                            "type":       m.get('type', ''),
                            "modifier":   m.get('modifier', ''),
                            "distance_m": round(step.get('distance', 0))
                        })
                cautious = {
                    "route_id":    99,
                    "coords":      cr_coords,
                    "distance_km": round(cr_route['distance'] / 1000, 2),
                    "duration_min":round(cr_route['duration'] / 60, 1),
                    "risk_score":  cr_risk,
                    "risk_label":  engine._score_to_level(cr_risk),
                    "risk_points": cr_spots[:15],
                    "steps":       cr_steps[:30],
                    "route_type":  "cautious",
                    "route_label": "🔒 Most Cautious"
                }
        except Exception:
            pass

    if not cautious:
        if len(by_risk) > 1:
            cautious = dict(by_risk[1])
        else:
            cautious = dict(balanced)
        cautious['route_type'] = 'cautious'
        cautious['route_label'] = '🔒 Most Cautious'

    safest_routes = [balanced, cautious]

    return {
        "fastest_route": fastest,
        "safest_route":  balanced,
        "safest_routes": safest_routes,
        "comparison": {
            "time_difference_min":    round(abs(fastest['duration_min'] - balanced['duration_min']), 1),
            "distance_difference_km": round(abs(fastest['distance_km']  - balanced['distance_km']),  2),
            "fastest_risk":           fastest['risk_label'],
            "safest_risk":            balanced['risk_label']
        }
    }

@app.get("/point/{lat}/{lon}")
def point_risk(lat: float, lon: float):
    score, reason, level = engine.get_point_risk(lat, lon)
    return {"lat": lat, "lon": lon, "score": score, "level": level, "reason": reason}

@app.post("/location/share")
def share_location(loc: LocationUpdate):
    score, reason, level = engine.get_point_risk(loc.lat, loc.lon)
    live_locations[loc.share_code] = {
        "lat":    loc.lat,
        "lon":    loc.lon,
        "level":  level,
        "reason": reason
    }
    return {"status": "sharing", "code": loc.share_code}

@app.get("/location/track/{code}")
def track_location(code: str):
    if code not in live_locations:
        raise HTTPException(status_code=404, detail="Code not found or expired")
    return live_locations[code]

@app.get("/track/{code}")
def track_page(code: str):
    from fastapi.responses import HTMLResponse
    if code not in live_locations:
        return HTMLResponse("<h2 style='color:white;background:#07070f;padding:20px'>Location not found or expired. Ask sender to share again.</h2>")
    loc = live_locations[code]
    html = f"""<!DOCTYPE html><html><head><title>SaferRoute Live</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#07070f;color:#eaeaf5;font-family:system-ui}}
    #header{{padding:14px 16px;background:#0d0d1a;border-bottom:1px solid #2e2e48}}
    #header h2{{color:#e63946;font-size:17px}}#header p{{color:#6b6b8a;font-size:12px;margin-top:3px}}
    #map{{width:100%;height:calc(100vh - 90px)}}
    #info{{padding:10px 16px;background:#0d0d1a;font-size:12px;color:#6b6b8a;position:fixed;bottom:0;width:100%}}
    #info span{{color:#2ec4b6;font-weight:700}}</style></head>
    <body>
    <div id="header"><h2>📍 SaferRoute Live Location</h2><p>🟢 Updating every 5 seconds</p></div>
    <div id="map"></div>
    <div id="info">Risk: <span id="risk">{loc['level']}</span> — <span id="reason">{loc['reason']}</span></div>
    <script>
    var map = L.map('map').setView([{loc['lat']},{loc['lon']}],16);
    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png').addTo(map);
    var circle = L.circle([{loc['lat']},{loc['lon']}],{{radius:30,color:'#e63946',fillColor:'#e63946',fillOpacity:0.5}}).addTo(map);
    var marker = L.circleMarker([{loc['lat']},{loc['lon']}],{{radius:10,color:'#fff',fillColor:'#e63946',weight:3,fillOpacity:1}}).addTo(map).bindPopup('<b>Live Location</b><br>{loc["level"]}').openPopup();
    setInterval(function(){{
    fetch('/location/track/{code}').then(r=>r.json()).then(d=>{{
    var ll=[d.lat,d.lon];marker.setLatLng(ll);circle.setLatLng(ll);map.setView(ll,16);
    document.getElementById('risk').innerText=d.level;
    document.getElementById('reason').innerText=d.reason;
    }}).catch(()=>{{}});
    }},5000);
    </script></body></html>"""
    return HTMLResponse(html)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)