from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from risk_engine import RiskEngine

app = FastAPI(title="SaferRoute AI")

# This allows your React dashboard and React Native app to call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

print("Starting SaferRoute AI...")
engine = RiskEngine()
print("All systems ready!")

# OSRM = free routing API, uses same road graph logic as Google Maps
OSRM = "http://router.project-osrm.org/route/v1/driving"

# Stores live locations in memory (like WhatsApp live location)
live_locations = {}

# ── Request body shapes ────────────────────────────────────────────────────────

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

# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "SaferRoute AI is running ✅"}


@app.get("/stats")
def get_stats():
    """Numbers shown in the dashboard top bar"""
    return engine.get_stats()


@app.get("/heatmap")
def get_heatmap():
    """Risk dots shown on the map"""
    return {"data": engine.get_heatmap()}


@app.post("/route")
def find_route(req: RouteRequest):
    """
    Main endpoint — returns fastest route AND safest route.
    OSRM uses real roads, real turns, real distances (like Google Maps).
    We then score each route using our accident data.
    """
    # Call OSRM with alternatives=true to get multiple route options
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

    # Score each route OSRM returned (max 3)
    scored_routes = []
    for i, route in enumerate(data['routes'][:3]):
        coords = route['geometry']['coordinates']
        risk_score, risky_spots = engine.get_route_risk(coords)

        # Get turn-by-turn directions from OSRM steps
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

    # Fastest = OSRM always returns shortest-time route first
    fastest = dict(scored_routes[0])
    fastest['route_type'] = 'fastest'

    # Safest = whichever of the alternatives has lowest risk score
    safest = dict(min(scored_routes, key=lambda r: r['risk_score']))
    safest['route_type'] = 'safest'

    return {
        "fastest_route": fastest,
        "safest_route":  safest,
        "comparison": {
            "time_difference_min":    round(abs(fastest['duration_min'] - safest['duration_min']), 1),
            "distance_difference_km": round(abs(fastest['distance_km']  - safest['distance_km']),  2),
            "fastest_risk":           fastest['risk_label'],
            "safest_risk":            safest['risk_label']
        }
    }


@app.get("/point/{lat}/{lon}")
def point_risk(lat: float, lon: float):
    """Risk score for any single location"""
    score, reason, level = engine.get_point_risk(lat, lon)
    return {"lat": lat, "lon": lon, "score": score, "level": level, "reason": reason}


@app.post("/location/share")
def share_location(loc: LocationUpdate):
    """Start sharing live location — like WhatsApp live location"""
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
    """Get someone's shared live location using their code"""
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