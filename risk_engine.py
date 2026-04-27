import pandas as pd
import numpy as np

class RiskEngine:
    def __init__(self):
        print("Loading bangalore_risk_zones.csv ...")
        self.df = pd.read_csv('bangalore_risk_zones.csv')
        print(f"Loaded {len(self.df)} rows")
        self._build_grid()

    def _build_grid(self):
        # Round coordinates to 2 decimal places = ~1km grid squares
        self.df['lat_bin'] = (self.df['latitude'] * 100).round() / 100
        self.df['lon_bin'] = (self.df['longitude'] * 100).round() / 100

        # For each grid square: average risk + most common alarm type
        self.grid = self.df.groupby(['lat_bin', 'lon_bin']).agg(
            risk_score=('risk_score', 'mean'),
            alert_count=('alarm_type', 'count'),
            top_alarm=('alarm_type', lambda x: x.value_counts().index[0])
        ).reset_index()

        print(f"Grid ready: {len(self.grid)} risk zones created")

    def get_point_risk(self, lat, lon):
        # Find all grid squares within ~2km of this point
        nearby = self.grid[
            (abs(self.grid['lat_bin'] - lat) <= 0.02) &
            (abs(self.grid['lon_bin'] - lon) <= 0.02)
        ]

        if nearby.empty:
            return 0.1, "No incident data here", "Very Low Risk"

        # Pick the worst nearby square
        worst = nearby.loc[nearby['risk_score'].idxmax()]
        reason = self._alarm_to_reason(worst['top_alarm'])
        level  = self._score_to_level(float(worst['risk_score']))
        return float(worst['risk_score']), reason, level

    def get_route_risk(self, coords):
        # coords = list of [lon, lat] from OSRM
        if not coords:
            return 0.0, []

        scores = []
        risky_spots = []

        for point in coords[::5]:   # check every 5th point
            lon, lat = point[0], point[1]
            score, reason, level = self.get_point_risk(lat, lon)
            scores.append(score)

            if score > 0.45:        # only flag actually risky spots
                risky_spots.append({
                    "lat":    round(lat, 4),
                    "lon":    round(lon, 4),
                    "score":  round(score, 2),
                    "reason": reason,
                    "level":  level
                })

        avg = round(float(np.mean(scores)), 3) if scores else 0.0
        return avg, risky_spots

    def get_heatmap(self):
        # Top 2000 risky spots for the map display
        top = self.grid[self.grid['risk_score'] > 0.25].nlargest(2000, 'risk_score')
        return [
            {
                "lat":       float(r['lat_bin']),
                "lon":       float(r['lon_bin']),
                "intensity": round(float(r['risk_score']), 2),
                "reason":    self._alarm_to_reason(r['top_alarm']),
                "count":     int(r['alert_count'])
            }
            for _, r in top.iterrows()
        ]

    def get_stats(self):
        return {
            "total_alerts":     len(self.df),
            "high_risk_alerts": int((self.df['risk_score'] > 0.65).sum()),
            "blackspots":       int((self.df['risk_score'] > 0.80).sum()),
            "alarm_breakdown":  self.df['alarm_type'].value_counts().to_dict()
        }

    def _alarm_to_reason(self, alarm):
        reasons = {
            'PCW':       'Pedestrian danger — people cross suddenly here',
            'FCW':       'Frequent sudden braking — crash-prone road',
            'UFCW':      'Urban collision zone — dense traffic near-crashes',
            'HMW':       'Tailgating zone — vehicles follow too closely',
            'Overspeed': 'Speeding zone — vehicles regularly exceed safe speed',
            'LDWL':      'Lane drift zone — vehicles drift left here',
            'LDWR':      'Lane drift zone — vehicles drift right here',
        }
        return reasons.get(alarm, 'Known risk area')

    def _score_to_level(self, score):
        if score > 0.75: return "Very High Risk"
        if score > 0.55: return "High Risk"
        if score > 0.35: return "Medium Risk"
        if score > 0.15: return "Low Risk"
        return "Very Low Risk"