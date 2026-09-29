"""
SAGAR - Marine Pollution Forensics Backend
Detects oil spills from SAR/EO imagery, back-traces drift, correlates with AIS.
"""
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import numpy as np
from PIL import Image
import io, math, random, uuid
from datetime import datetime, timedelta
from scipy import ndimage
from skimage.filters import threshold_otsu
from skimage.measure import label, regionprops

app = FastAPI(title="SAGAR API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- In-memory stores (swap for PostGIS in prod) ----------
SPILLS_DB: dict = {}
VESSELS_DB: dict = {}

# ---------- Realistic AIS vessel feed (demo dataset) ----------
DEMO_VESSELS = [
    {"mmsi": "636019825", "name": "MT NORDIC STAR",   "type": "Oil Tanker",    "flag": "LR", "dwt": 158000},
    {"mmsi": "538005432", "name": "MT PACIFIC VOYAGER","type": "Oil Tanker",    "flag": "MH", "dwt": 110000},
    {"mmsi": "354012000", "name": "MV GULF TRADER",   "type": "Cargo",         "flag": "PA", "dwt": 75000},
    {"mmsi": "244990123", "name": "MT AEGEAN SPIRIT", "type": "Chemical Tanker","flag": "NL","dwt": 49000},
    {"mmsi": "477123456", "name": "MV EASTERN DAWN",  "type": "Bulk Carrier",  "flag": "HK", "dwt": 63000},
]

def _mmsi_to_seed(mmsi: str) -> int:
    return int(mmsi) if mmsi.isdigit() else abs(hash(mmsi))

def synth_ais_track(mmsi: str, t_start: datetime, t_end: datetime,
                    start_lat: float, start_lon: float,
                    heading_deg: float, speed_knots: float) -> List[dict]:
    """Generate a plausible AIS track for demo (in prod: query MarineTraffic/Spire)."""
    rng = random.Random(_mmsi_to_seed(mmsi))
    pts = []
    lat, lon = start_lat, start_lon
    t = t_start
    dt = timedelta(minutes=10)
    while t <= t_end:
        # knots -> deg/hour approx
        step_deg = (speed_knots * 1.852 / 111.0) * (dt.total_seconds() / 3600.0)
        rad = math.radians(heading_deg + rng.uniform(-4, 4))
        lat += step_deg * math.cos(rad)
        lon += step_deg * math.sin(rad) / max(0.2, math.cos(math.radians(lat)))
        pts.append({
            "t": t.isoformat(),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "sog": round(speed_knots + rng.uniform(-0.6, 0.6), 2),
            "cog": round((heading_deg + rng.uniform(-5, 5)) % 360, 1),
        })
        t += dt
    return pts

# ---------- Drift model (backward) ----------
def drift_backtrace(origin_lat: float, origin_lon: float,
                    hours_back: float,
                    wind_u: float = 6.0, wind_v: float = -2.0,
                    current_u: float = 0.4, current_v: float = 0.15) -> List[dict]:
    """
    Simplified backward Lagrangian drift.
    Oil slick moves ≈ 3% of wind + 100% of surface current (Lehr/ADIOS rules-of-thumb).
    We integrate BACKWARD in time to estimate the release point.
    """
    # Slick displacement vector per hour (deg)
    V_lat_per_hr = (current_v + 0.03 * wind_v) / 111.0
    V_lon_per_hr = (current_u + 0.03 * wind_u) / 111.0
    path = []
    steps = int(hours_back * 2)  # 30-min steps
    for i in range(steps + 1):
        h = i * 0.5
        lat = origin_lat - V_lat_per_hr * h
        lon = origin_lon - V_lon_per_hr * h
        path.append({"hour": -h, "lat": round(lat, 5), "lon": round(lon, 5)})
    return path

# ---------- SAR/EO slick detection ----------
def detect_slick_from_image(img: np.ndarray) -> dict:
    """
    SAR oil slicks appear as DARK patches (low backscatter) on a bright sea surface.
    EO: darker + low texture.
    """
    gray = img.mean(axis=2) if img.ndim == 3 else img
    # Normalize
    gray = (gray - gray.min()) / max(1e-6, (gray.max() - gray.min()))
    # Otsu on inverse — dark blobs
    thresh = threshold_otsu(gray)
    binary = gray < thresh * 0.75  # emphasize dark
    binary = ndimage.binary_opening(binary, iterations=2)
    binary = ndimage.binary_closing(binary, iterations=3)

    lbl = label(binary)
    props = regionprops(lbl)
    if not props:
        return {"detected": False, "area_px": 0, "bbox": None, "confidence": 0.0}

    largest = max(props, key=lambda p: p.area)
    area = int(largest.area)
    total = gray.size
    frac = area / total
    # Heuristic confidence: dark & large & elongated = likely slick
    elong = largest.major_axis_length / max(1e-3, largest.minor_axis_length)
    conf = float(min(0.98, 0.35 + 1.6 * frac + 0.02 * min(elong, 15)))
    return {
        "detected": True,
        "area_px": area,
        "area_fraction": round(frac, 4),
        "bbox": [int(x) for x in largest.bbox],
        "centroid": [float(c) for c in largest.centroid],
        "elongation": round(float(elong), 2),
        "confidence": round(conf, 3),
    }

# ---------- API models ----------
class DetectRequest(BaseModel):
    scene_id: str
    lat: float
    lon: float
    acquired_at: str  # ISO
    sensor: str = "SAR"  # SAR or EO
    wind_u: float = 6.0
    wind_v: float = -2.0
    current_u: float = 0.4
    current_v: float = 0.15
    hours_back: float = 12.0

# ---------- Endpoints ----------
@app.get("/")
def root():
    return {"service": "SAGAR API", "status": "operational",
            "endpoints": ["/health", "/detect", "/detect_image", "/ais/correlate", "/spills", "/vessels"]}

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

@app.post("/detect")
def detect(req: DetectRequest):
    """
    Run detection for a satellite scene (synthetic if no image uploaded).
    Returns: slick polygon, back-traced origin, suspect vessels.
    """
    spill_id = str(uuid.uuid4())[:8]
    acquired = datetime.fromisoformat(req.acquired_at.replace("Z", ""))

    # Synthetic slick area (in km²) — demo
    rng = random.Random(hash(spill_id))
    area_km2 = round(rng.uniform(4.5, 42.0), 2)
    confidence = round(rng.uniform(0.72, 0.96), 3)

    # Back-trace
    path = drift_backtrace(req.lat, req.lon, req.hours_back,
                           req.wind_u, req.wind_v, req.current_u, req.current_v)
    origin = path[-1]

    # AIS correlation: find vessels whose track passes near origin at that time
    suspects = []
    origin_time = acquired - timedelta(hours=req.hours_back)
    for v in DEMO_VESSELS:
        # place start position near origin, slightly offset
        seed = _mmsi_to_seed(v["mmsi"])
        rng2 = random.Random(seed)
        start_lat = origin["lat"] + rng2.uniform(-0.4, 0.4)
        start_lon = origin["lon"] + rng2.uniform(-0.4, 0.4)
        heading = rng2.uniform(0, 360)
        speed = rng2.uniform(9, 14)
        track = synth_ais_track(
            v["mmsi"], origin_time - timedelta(hours=6),
            acquired + timedelta(hours=1),
            start_lat, start_lon, heading, speed
        )
        # min distance to origin
        dmin = min(
            math.hypot(p["lat"] - origin["lat"], p["lon"] - origin["lon"]) for p in track
        )
        # distance in km approx
        d_km = dmin * 111.0
        # proximity score
        score = math.exp(-d_km / 25.0)
        # bonus if tanker
        if "Tanker" in v["type"]:
            score *= 1.25
        suspects.append({
            **v,
            "distance_km": round(d_km, 2),
            "match_score": round(score, 3),
            "track": track[::3],  # downsample for UI
        })
    suspects.sort(key=lambda s: s["match_score"], reverse=True)

    spill = {
        "spill_id": spill_id,
        "scene_id": req.scene_id,
        "sensor": req.sensor,
        "acquired_at": acquired.isoformat(),
        "slick": {
            "lat": req.lat, "lon": req.lon,
            "area_km2": area_km2,
            "confidence": confidence,
        },
        "origin": {
            "lat": origin["lat"], "lon": origin["lon"],
            "estimated_time": origin_time.isoformat(),
            "hours_back": req.hours_back,
        },
        "drift_path": path,
        "suspects": suspects[:5],
        "recommended_action": (
            f"Flag {suspects[0]['name']} ({suspects[0]['mmsi']}) for inspection. "
            f"Evidence: within {suspects[0]['distance_km']} km of back-traced origin "
            f"at {origin_time.strftime('%Y-%m-%d %H:%M')} UTC."
        ),
    }
    SPILLS_DB[spill_id] = spill
    VESSELS_DB[suspects[0]["mmsi"]] = suspects[0]
    return spill

@app.post("/detect_image")
async def detect_image(file: UploadFile = File(...),
                       lat: float = Form(...), lon: float = Form(...),
                       acquired_at: str = Form(...),
                       hours_back: float = Form(12.0),
                       wind_u: float = Form(6.0), wind_v: float = Form(-2.0),
                       current_u: float = Form(0.4), current_v: float = Form(0.15)):
    """Upload an actual SAR/EO tile (PNG/JPG) and run detection."""
    raw = await file.read()
    try:
        img = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
    except Exception as e:
        raise HTTPException(400, f"Invalid image: {e}")

    det = detect_slick_from_image(img)
    spill_id = str(uuid.uuid4())[:8]
    acquired = datetime.fromisoformat(acquired_at.replace("Z", ""))

    if not det["detected"]:
        return {"spill_id": spill_id, "detected": False, "message": "No significant slick detected."}

    # Convert centroid px to lat/lon using a nominal 10km swath (demo assumption)
    h, w = img.shape[:2]
    cx, cy = det["centroid"][1], det["centroid"][0]
    lat_s = lat - (cy - h/2) / h * 0.1
    lon_s = lon + (cx - w/2) / w * 0.1

    path = drift_backtrace(lat_s, lon_s, hours_back, wind_u, wind_v, current_u, current_v)
    origin = path[-1]

    suspects = []
    origin_time = acquired - timedelta(hours=hours_back)
    for v in DEMO_VESSELS:
        seed = _mmsi_to_seed(v["mmsi"])
        rng2 = random.Random(seed)
        track = synth_ais_track(
            v["mmsi"], origin_time - timedelta(hours=6), acquired + timedelta(hours=1),
            origin["lat"] + rng2.uniform(-0.4, 0.4),
            origin["lon"] + rng2.uniform(-0.4, 0.4),
            rng2.uniform(0, 360), rng2.uniform(9, 14)
        )
        dmin = min(math.hypot(p["lat"]-origin["lat"], p["lon"]-origin["lon"]) for p in track)
        score = math.exp(-(dmin*111)/25.0) * (1.25 if "Tanker" in v["type"] else 1.0)
        suspects.append({**v, "distance_km": round(dmin*111, 2),
                         "match_score": round(score, 3), "track": track[::3]})
    suspects.sort(key=lambda s: s["match_score"], reverse=True)

    result = {
        "spill_id": spill_id, "detected": True,
        "scene_id": file.filename, "sensor": "SAR",
        "acquired_at": acquired.isoformat(),
        "slick": {"lat": lat_s, "lon": lon_s,
                  "confidence": det["confidence"],
                  "elongation": det["elongation"],
                  "bbox": det["bbox"]},
        "origin": {"lat": origin["lat"], "lon": origin["lon"],
                   "estimated_time": origin_time.isoformat()},
        "drift_path": path,
        "suspects": suspects[:5],
        "recommended_action": f"Flag {suspects[0]['name']} ({suspects[0]['mmsi']}).",
    }
    SPILLS_DB[spill_id] = result
    return result

@app.get("/spills")
def list_spills():
    return list(SPILLS_DB.values())

@app.get("/vessels")
def list_vessels():
    return DEMO_VESSELS

@app.get("/ais/correlate")
def ais_correlate(lat: float, lon: float, hours_back: float = 12.0):
    """Return vessels near a point (utility endpoint)."""
    out = []
    for v in DEMO_VESSELS:
        d = math.hypot(v.get("lat", lat)-lat, v.get("lon", lon)-lon) * 111
        out.append({**v, "distance_km": round(d, 2)})
    out.sort(key=lambda x: x["distance_km"])
    return out