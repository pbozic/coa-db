import { useCallback, useEffect, useMemo, useState } from 'react'
import ItemList from './components/ItemList.jsx'
import ItemDetail from './components/ItemDetail.jsx'
import Profitable from './components/Profitable.jsx'
import Tooltip from './components/Tooltip.jsx'
import { createModel } from './model.js'

const FILTERS = [
  ['all', 'All'],
  ['flask', 'Flasks'],
  ['food', 'Food'],
  ['enchant', 'Enchants'],
  ['material', 'Materials'],
  ['seed', 'Sellable'],
]
const FARM_KEY = 'coa.farmed.v1'

function readFarmed() {
  try {
    return new Set(JSON.parse(localStorage.getItem(FARM_KEY) || '[]'))
  } catch {
    return new Set()
  }
}

/**
 * Overlay a freshly published price file onto the catalog.
 *
 * Falls back silently to whatever prices the catalog already carries, so the
 * site still works when the price file is missing or briefly unreachable.
 */
function applyPrices(payload, live) {
  if (!live?.items) return payload
  const lookup = live.items
  const items = payload.items.map((item) => {
    const own = lookup[String(item.id)]
    const sale = lookup[String(item.saleItemId)]
    if (!own && !sale) return item
    return {
      ...item,
      price: own ? { ...item.price, ...own } : item.price,
      salePrice: sale?.sell ?? item.salePrice,
      saleQuantity: sale?.quantity ?? item.saleQuantity,
    }
  })
  return {
    ...payload,
    meta: { ...payload.meta, scan: live.scan || payload.meta.scan },
    items,
  }
}

/** The URL hash is the single source of truth, so Back and Forward just work. */
function readHash() {
  const raw = decodeURIComponent(window.location.hash.replace(/^#/, ''))
  if (raw.startsWith('profit')) return { tab: 'profit', key: null }
  return { tab: 'browse', key: raw || null }
}

export default function App() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [route, setRoute] = useState(readHash)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [farmed, setFarmed] = useState(readFarmed)
  const [hover, setHover] = useState({ item: null, position: null })
  const [history, setHistory] = useState(null)

  useEffect(() => {
    // The catalog is big and changes rarely; prices are tiny and change every
    // scan. Fetching them separately means refreshing prices on a static host
    // is a 5 KB file swap instead of a site rebuild. VITE_PRICES_URL lets the
    // prices live somewhere else entirely (a data branch, a blob store).
    const pricesUrl = import.meta.env.VITE_PRICES_URL || 'prices.json'
    const catalog = fetch('data.json', { cache: 'no-store' }).then((r) => {
      if (!r.ok) throw new Error(`data.json: ${r.status}`)
      return r.json()
    })
    const prices = fetch(pricesUrl, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)

    fetch(import.meta.env.VITE_HISTORY_URL || 'history.json', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((h) => setHistory(h ? { series: h.items || {}, turnover: h.turnover || {} } : null))
      .catch(() => setHistory(null))

    Promise.all([catalog, prices])
      .then(([payload, live]) => setData(live ? applyPrices(payload, live) : payload))
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    const onPop = () => setRoute(readHash())
    window.addEventListener('popstate', onPop)
    window.addEventListener('hashchange', onPop)
    return () => {
      window.removeEventListener('popstate', onPop)
      window.removeEventListener('hashchange', onPop)
    }
  }, [])

  useEffect(() => {
    localStorage.setItem(FARM_KEY, JSON.stringify([...farmed]))
  }, [farmed])

  const navigate = useCallback((hash) => {
    if (decodeURIComponent(window.location.hash.replace(/^#/, '')) === hash) return
    window.location.hash = encodeURIComponent(hash) // pushes a history entry
  }, [])

  useEffect(() => {
    setHover({ item: null, position: null })
  }, [route])

  const model = useMemo(
    () => (data ? createModel(data.items, farmed, data.meta.cut) : null),
    [data, farmed],
  )

  const toggleFarm = useCallback((id) => {
    setFarmed((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const onHover = useCallback((item, event) => {
    if (!item) setHover({ item: null, position: null })
    else setHover({ item, position: { x: event.clientX, y: event.clientY } })
  }, [])

  useEffect(() => {
    if (!hover.item) return
    const move = (e) => {
      // Also dismiss when the cursor is no longer over anything tooltip-worthy,
      // which covers elements that vanished from under it.
      if (!e.target.closest?.('[data-tip], .item-row, .link, .rank tbody tr')) {
        setHover({ item: null, position: null })
        return
      }
      setHover((h) => (h.item ? { ...h, position: { x: e.clientX, y: e.clientY } } : h))
    }
    const clear = () => setHover({ item: null, position: null })
    window.addEventListener('mousemove', move)
    window.addEventListener('click', clear)
    window.addEventListener('blur', clear)
    document.addEventListener('mouseleave', clear)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('click', clear)
      window.removeEventListener('blur', clear)
      document.removeEventListener('mouseleave', clear)
    }
  }, [hover.item])

  const visible = useMemo(() => {
    if (!data) return []
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
    return data.items.filter((item) => {
      if (filter === 'seed' && !item.seed) return false
      if (['flask', 'food', 'enchant', 'material'].includes(filter) && item.family !== filter) return false
      if (!terms.length) return true
      const hay = `${item.name} ${item.effect} ${item.sources.join(' ')} ${(item.zones || []).join(' ')}`.toLowerCase()
      return terms.every((term) => hay.includes(term))
    })
  }, [data, query, filter])

  if (error) return <div className="empty">Could not load data.json — {error}</div>
  if (!data || !model) return <div className="empty">Loading…</div>

  const selected = route.key ? data.items.find((i) => i.key === route.key) : null
  const scan = data.meta.scan
  const stale = scan?.age_days > 3

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand">
            CoA High Risk
            <small>
              {data.meta.counts.total} items · {data.meta.counts.flask} flasks,{' '}
              {data.meta.counts.food} foods, {data.meta.counts.enchant} enchants
            </small>
          </div>
          {route.tab === 'browse' && (
            <>
              <input
                className="search"
                type="search"
                placeholder="Search name, effect or zone — try 'stamina', 'winterspring'"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <div className="chips">
                {FILTERS.map(([id, label]) => (
                  <button key={id} className="chip" aria-pressed={filter === id} onClick={() => setFilter(id)}>
                    {label}
                  </button>
                ))}
              </div>
            </>
          )}
          {scan && (
            <span className={`scanline ${stale ? 'stale' : ''}`}>
              {scan.realm} · scanned {scan.scanned_at}
              {scan.age_days != null && ` (${scan.age_days.toFixed(1)}d ago)`}
            </span>
          )}
        </div>
        <nav className="tabs">
          <button className="tab" aria-selected={route.tab === 'browse'} onClick={() => navigate(route.key || '')}>
            Browse
          </button>
          <button className="tab" aria-selected={route.tab === 'profit'} onClick={() => navigate('profit')}>
            Most profitable
          </button>
          {farmed.size > 0 && (
            <button className="tab" onClick={() => setFarmed(new Set())} title="Clear every ticked material">
              <span className="muted">farming {farmed.size} — clear</span>
            </button>
          )}
        </nav>
      </header>

      {route.tab === 'profit' ? (
        <div className="single">
          <Profitable data={data} farmed={farmed} onOpen={navigate} onHover={onHover} />
        </div>
      ) : (
        <div className="split">
          <div className="pane">
            <ItemList items={visible} selected={route.key} onSelect={navigate} onHover={onHover} />
          </div>
          <div className="pane detail">
            <ItemDetail
              item={selected}
              model={model}
              farmed={farmed}
              onToggleFarm={toggleFarm}
              onOpen={navigate}
              onHover={onHover}
              meta={data.meta}
              history={history}
            />
          </div>
        </div>
      )}

      <Tooltip item={hover.item} position={hover.position} />
    </div>
  )
}
