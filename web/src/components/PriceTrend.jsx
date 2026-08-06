import { useMemo, useState } from 'react'
import InfoTip from './InfoTip.jsx'
import { money } from '../model.js'

const WIDTH = 640
const HEIGHT = 170
const PAD = { top: 12, right: 12, bottom: 22, left: 60 }

const RANGES = [
  { id: 7, label: '7d' },
  { id: 14, label: '14d' },
  { id: 30, label: '30d' },
]

/**
 * One formatter for the whole tick set.
 *
 * Deciding per value gives an axis reading 15g / 10g / 5.0g, where the odd one
 * out looks like a different unit. The decimals are chosen once, from the
 * smallest gap any two ticks need to stay distinct.
 */
function tickFormatter(ticks) {
  const top = Math.max(...ticks)
  if (top < 10000) return (c) => `${Math.round(c / 100)}s`
  const step = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : top
  const decimals = step / 10000 >= 1 ? 0 : 1
  return (c) => `${(c / 10000).toFixed(decimals)}g`
}

function niceTicks(min, max, count = 4) {
  if (!(max > min)) return [min]
  const raw = (max - min) / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) || magnitude * 10
  const start = Math.ceil(min / step) * step
  const ticks = []
  for (let v = start; v <= max + step * 0.001; v += step) ticks.push(v)
  return ticks
}

/**
 * Price over time for one item.
 *
 * Two series: TSM's 14-day market value, which is smoothed and shows the trend,
 * and the lowest buyout at each scan, which is spikier and shows what you could
 * actually have paid. The lowest buyout only exists on our own snapshots, so it
 * starts partway along — drawn as a separate path rather than interpolated
 * across the gap, which would invent readings that were never taken.
 */
export default function PriceTrend({ points, turnover, name }) {
  const [days, setDays] = useState(14)
  const [hover, setHover] = useState(null)

  const view = useMemo(() => {
    if (!points?.length) return null
    const cutoff = Date.now() / 1000 - days * 86400
    const rows = points.filter((p) => p[0] >= cutoff && p[1] != null)
    if (rows.length < 2) return null

    const xs = rows.map((r) => r[0])
    const values = rows.flatMap((r) => [r[1], r[2]].filter((v) => v != null))
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...values)
    const maxY = Math.max(...values)
    // A flat series would otherwise collapse onto the axis.
    const padY = (maxY - minY) * 0.12 || maxY * 0.1 || 1
    const lo = Math.max(0, minY - padY)
    const hi = maxY + padY

    const x = (t) => PAD.left + ((t - minX) / (maxX - minX || 1)) * (WIDTH - PAD.left - PAD.right)
    const y = (v) => HEIGHT - PAD.bottom - ((v - lo) / (hi - lo || 1)) * (HEIGHT - PAD.top - PAD.bottom)

    const path = (index) => {
      let d = ''
      let open = false
      for (const row of rows) {
        const value = row[index]
        if (value == null) { open = false; continue }
        d += `${open ? 'L' : 'M'}${x(row[0]).toFixed(1)} ${y(value).toFixed(1)} `
        open = true
      }
      return d.trim()
    }

    return { rows, x, y, lo, hi, minX, maxX, market: path(1), buyout: path(2) }
  }, [points, days])

  if (!points?.length) return null

  const onMove = (event) => {
    if (!view) return
    const svg = event.currentTarget
    const box = svg.getBoundingClientRect()
    const px = ((event.clientX - box.left) / box.width) * WIDTH
    let best = null
    let bestDistance = Infinity
    for (const row of view.rows) {
      const distance = Math.abs(view.x(row[0]) - px)
      if (distance < bestDistance) { bestDistance = distance; best = row }
    }
    setHover(best)
  }

  // A reading only counts towards demand once it carries a listing depth.
  const depthReadings = (points || []).filter((p) => p.length > 3 && p[3] != null).length
  const ticks = view ? niceTicks(view.lo, view.hi) : []
  const fmtTick = tickFormatter(ticks.length ? ticks : [0])
  const first = view?.rows[0]
  const last = view?.rows[view.rows.length - 1]
  const change = first && last && first[1] ? (last[1] - first[1]) / first[1] : null

  return (
    <div className="section">
      <h3>
        Price trend
        {change != null && (
          <span className={`trend ${change >= 0 ? 'up' : 'down'}`}>
            {change >= 0 ? '▲' : '▼'} {Math.abs(change * 100).toFixed(0)}% over {days}d
          </span>
        )}
      </h3>

      <div className="chips" style={{ marginBottom: 10 }}>
        {RANGES.map((r) => (
          <button key={r.id} className="chip" aria-pressed={days === r.id} onClick={() => setDays(r.id)}>
            {r.label}
          </button>
        ))}
        <span className="legend">
          <span className="key market" /> market value
          <span className="key buyout" /> lowest buyout
        </span>
      </div>

      {!view ? (
        <div className="muted" style={{ fontSize: 13 }}>
          Not enough readings in this window yet — the history fills in as scans land.
        </div>
      ) : (
        <svg
          className="trend-chart"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`Price of ${name} over the last ${days} days`}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={view.y(t)} y2={view.y(t)} />
              <text className="axis" x={PAD.left - 8} y={view.y(t) + 4} textAnchor="end">
                {fmtTick(t)}
              </text>
            </g>
          ))}

          <text className="axis" x={PAD.left} y={HEIGHT - 6}>
            {new Date(view.minX * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          </text>
          <text className="axis" x={WIDTH - PAD.right} y={HEIGHT - 6} textAnchor="end">
            {new Date(view.maxX * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          </text>

          {view.buyout && <path className="line buyout" d={view.buyout} />}
          <path className="line market" d={view.market} />

          {hover && (
            <g className="crosshair">
              <line x1={view.x(hover[0])} x2={view.x(hover[0])} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
              {hover[1] != null && <circle className="dot market" cx={view.x(hover[0])} cy={view.y(hover[1])} r={4} />}
              {hover[2] != null && <circle className="dot buyout" cx={view.x(hover[0])} cy={view.y(hover[2])} r={4} />}
            </g>
          )}
        </svg>
      )}

      {!turnover && depthReadings > 0 && (
        <div className="turnover collecting">
          <span className="k">
            Stock leaving the AH
            <InfoTip label="Why there is no figure yet">
              This is measured by watching how many units are listed and counting the
              falls. That needs at least three readings spanning an hour, and a reading
              only lands when a fresh auction scan reaches the shared database — roughly
              a few times a day, not once per publish.
              <br /><br />
              {depthReadings} recorded so far. Scanning the auction house yourself and
              reloading adds one immediately.
            </InfoTip>
          </span>
          <strong className="muted">collecting</strong>
          <span className="muted">
            {depthReadings} depth reading{depthReadings === 1 ? '' : 's'} so far · needs 3 over an hour
          </span>
        </div>
      )}

      {turnover && (
        <div className="turnover">
          <span className="k">
            Stock leaving the AH
            <InfoTip label="How stock leaving is measured">
              <strong>Counted, not reported.</strong> Every scan records how many units are
              listed. When that number falls, units left the auction house — so this is the
              total that disappeared per day.
              <br /><br />
              Those departures are <em>sales and expiries together</em>. No scan data
              separates them, and Ascension has no crowdsourced sale rate the way retail TSM
              does, so treat it as an <strong>upper bound on demand</strong> rather than a
              sale figure. On a briskly traded item expiries are the smaller part; on a
              stagnant one most of it may be listings timing out.
              <br /><br />
              Compare it against the relisted figure: if roughly as much comes back as
              leaves, supply is replacing itself and the price should hold.
            </InfoTip>
          </span>
          <strong>~{turnover.perDay}/day</strong>
          <span className="muted">
            {turnover.addedPerDay}/day relisted · typically {turnover.medianDepth} listed ·
            from {turnover.samples} readings over {turnover.hours}h
          </span>
        </div>
      )}

      {hover && (
        <div className="trend-readout">
          <strong>{new Date(hover[0] * 1000).toLocaleString(undefined,
            { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</strong>
          <span><span className="key market" /> {money(hover[1])}</span>
          {hover[2] != null && <span><span className="key buyout" /> {money(hover[2])}</span>}
        </div>
      )}
    </div>
  )
}
