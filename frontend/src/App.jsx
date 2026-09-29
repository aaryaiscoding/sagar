import React, { useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline, Circle } from 'react-leaflet'
import L from 'leaflet'
import { runDetection, uploadImage } from './api'

// Fix default marker icon
delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

const spillIcon = new L.DivIcon({
  className: '', html: '<div style="background:#ef4444;width:14px;height:14px;border-radius:50%;box-shadow:0 0 12px #ef4444"></div>',
  iconSize: [14,14], iconAnchor: [7,7]
})
const originIcon = new L.DivIcon({
  className: '', html: '<div style="background:#f59e0b;width:12px;height:12px;border-radius:50%;border:2px solid white"></div>',
  iconSize: [12,12], iconAnchor: [6,6]
})

export default function App() {
  const [lat, setLat] = useState(18.92)
  const [lon, setLon] = useState(72.83)
  const [sensor, setSensor] = useState('SAR')
  const [hours, setHours] = useState(12)
  const [windU, setWindU] = useState(6)
  const [windV, setWindV] = useState(-2)
  const [curU, setCurU] = useState(0.4)
  const [curV, setCurV] = useState(0.15)
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const runDemo = async () => {
    setLoading(true); setError(null)
    try {
      const data = await runDetection({
        scene_id: `S1A_${Date.now()}`,
        lat, lon,
        acquired_at: new Date().toISOString(),
        sensor,
        wind_u: windU, wind_v: windV,
        current_u: curU, current_v: curV,
        hours_back: hours
      })
      setResult(data)
    } catch (e) {
      setError(e.message + ' — is the backend running?')
    } finally { setLoading(false) }
  }

  const runUpload = async () => {
    if (!file) return setError('Please select a SAR/EO image first.')
    setLoading(true); setError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('lat', lat); fd.append('lon', lon)
      fd.append('acquired_at', new Date().toISOString())
      fd.append('hours_back', hours)
      fd.append('wind_u', windU); fd.append('wind_v', windV)
      fd.append('current_u', curU); fd.append('current_v', curV)
      const data = await uploadImage(fd)
      setResult(data)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  return (
    <div className="app">
      {/* LEFT: Scene Input */}
      <div className="panel">
        <div className="header">
          <h1>S A G A R</h1>
          <div className="dot" />
        </div>
        <div className="tag">Marine Pollution Forensics Platform · NTRO SIH</div>

        <h2>Satellite Scene</h2>
        <label>Sensor</label>
        <select value={sensor} onChange={e => setSensor(e.target.value)}>
          <option value="SAR">SAR (Sentinel-1)</option>
          <option value="EO">Electro-Optical (Sentinel-2)</option>
        </select>

        <label>Scene center lat</label>
        <input type="number" step="0.001" value={lat} onChange={e => setLat(+e.target.value)} />
        <label>Scene center lon</label>
        <input type="number" step="0.001" value={lon} onChange={e => setLon(+e.target.value)} />

        <h2>Oceanographic Inputs</h2>
        <label>Wind U (m/s)</label>
        <input type="number" step="0.1" value={windU} onChange={e => setWindU(+e.target.value)} />
        <label>Wind V (m/s)</label>
        <input type="number" step="0.1" value={windV} onChange={e => setWindV(+e.target.value)} />
        <label>Current U (m/s)</label>
        <input type="number" step="0.05" value={curU} onChange={e => setCurU(+e.target.value)} />
        <label>Current V (m/s)</label>
        <input type="number" step="0.05" value={curV} onChange={e => setCurV(+e.target.value)} />
        <label>Back-trace window (hours)</label>
        <input type="number" step="1" value={hours} onChange={e => setHours(+e.target.value)} />

        <h2>Optional: Upload SAR/EO tile</h2>
        <input type="file" accept="image/*" onChange={e => setFile(e.target.files[0])} />

        <button onClick={runUpload} disabled={loading || !file}>
          {loading ? 'PROCESSING…' : '🔍 ANALYZE IMAGE'}
        </button>
        <button onClick={runDemo} disabled={loading} style={{ background: '#334155' }}>
          {loading ? '…' : '⚡ RUN DEMO (no image)'}
        </button>

        {error && <div className="action" style={{ background: '#450a0a', borderColor: '#7f1d1d', color: '#fecaca' }}>{error}</div>}
      </div>

      {/* CENTER: Map */}
      <div className="map-wrap">
        <MapContainer center={[lat, lon]} zoom={8} scrollWheelZoom>
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; OpenStreetMap &copy; CARTO'
          />
          {/* Spill slick */}
          <Marker position={[lat, lon]} icon={spillIcon}>
            <Popup>Detected slick center</Popup>
          </Marker>
          <Circle center={[lat, lon]} radius={3000} pathOptions={{ color: '#ef4444', fillOpacity: 0.15 }} />

          {result?.origin && (
            <Marker position={[result.origin.lat, result.origin.lon]} icon={originIcon}>
              <Popup>
                Back-traced origin<br/>
                {new Date(result.origin.estimated_time).toUTCString()}
              </Popup>
            </Marker>
          )}

          {result?.drift_path && (
            <Polyline
              positions={result.drift_path.map(p => [p.lat, p.lon])}
              pathOptions={{ color: '#f59e0b', dashArray: '6 6', weight: 2 }}
            />
          )}

          {result?.suspects?.map((s, i) => (
            <React.Fragment key={s.mmsi}>
              <Polyline
                positions={s.track.map(p => [p.lat, p.lon])}
                pathOptions={{ color: i === 0 ? '#ef4444' : '#38bdf8', weight: i === 0 ? 3 : 1.5, opacity: i === 0 ? 1 : 0.6 }}
              />
              <Marker position={[s.track[0].lat, s.track[0].lon]}>
                <Popup>
                  <b>{s.name}</b><br/>MMSI {s.mmsi}<br/>{s.type} · {s.flag}<br/>
                  Match score: {(s.match_score*100).toFixed(1)}%
                </Popup>
              </Marker>
            </React.Fragment>
          ))}
        </MapContainer>
      </div>

      {/* RIGHT: Investigation Report */}
      <div className="panel right">
        <h2>Investigation Report</h2>
        {!result && <div className="card">Run analysis to see results.</div>}

        {result && (
          <>
            <div className="card">
              <div className="kv"><span>Spill ID</span><b>{result.spill_id}</b></div>
              <div className="kv"><span>Scene</span><b>{result.scene_id}</b></div>
              <div className="kv"><span>Sensor</span><b>{result.sensor}</b></div>
              <div className="kv"><span>Acquired</span><b>{new Date(result.acquired_at).toUTCString().slice(5,22)}</b></div>
              {result.slick && <>
                <div className="kv"><span>Confidence</span><b>{(result.slick.confidence*100).toFixed(1)}%</b></div>
                {result.slick.area_km2 && <div className="kv"><span>Area</span><b>{result.slick.area_km2} km²</b></div>}
                {result.slick.elongation && <div className="kv"><span>Elongation</span><b>{result.slick.elongation}</b></div>}
              </>}
            </div>

            <h2>Back-traced Origin</h2>
            <div className="card">
              <div className="kv"><span>Lat / Lon</span><b>{result.origin.lat}, {result.origin.lon}</b></div>
              <div className="kv"><span>Est. release</span><b>{new Date(result.origin.estimated_time).toUTCString().slice(5,22)}</b></div>
            </div>

            <h2>Suspect Vessels (AIS Correlation)</h2>
            {result.suspects?.map((s, i) => (
              <div key={s.mmsi} className={`card suspect ${i===0?'top':''}`}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                  <b>{s.name}</b>
                  <span className={`badge ${i===0?'red':'amber'}`}>{(s.match_score*100).toFixed(0)}%</span>
                </div>
                <div className="kv"><span>MMSI</span><b>{s.mmsi}</b></div>
                <div className="kv"><span>Type / Flag</span><b>{s.type} · {s.flag}</b></div>
                <div className="kv"><span>DWT</span><b>{s.dwt?.toLocaleString()}</b></div>
                <div className="kv"><span>Dist. to origin</span><b>{s.distance_km} km</b></div>
              </div>
            ))}

            <h2>Recommended Action</h2>
            <div className="action">{result.recommended_action}</div>

            <div className="tag" style={{marginTop:10}}>
              Evidence package ready for coastal authority / MPA handoff.
            </div>
          </>
        )}
      </div>
    </div>
  )
}