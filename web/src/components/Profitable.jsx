import { useMemo, useState } from 'react'
import Icon from './Icon.jsx'
import { QUALITY_CLASS, money, createModel } from '../model.js'

const MODES = [
  { id: 'listed', label: 'Buy what is priced', hint: 'buy every material the last scan priced, farm the rest' },
  { id: 'buy', label: 'Buy everything', hint: 'strict buy → craft → sell; needs a scanned price for every material' },
  { id: 'mine', label: 'My farm list', hint: 'uses the materials you have ticked' },
]

const SCOPES = [
  { id: 'all', label: 'Everything' },
  { id: 'products', label: 'Finished products' },
  { id: 'steps', label: 'Intermediate steps' },
]

/** What kind of craft a row is, for the type column. */
function rowKind(item) {
  if (item.seed) return item.family
  return item.craft?.kind === 'conversion' ? 'convert' : 'craft'
}

/**
 * Ranks every craftable step by profit, not just the finished products.
 *
 * An intermediate is a trade in its own right: Hell Effigy converts to Hell
 * Dust, and if the Effigy is cheap and the Dust sells well that is worth doing
 * on its own, without ever crafting the enchant it feeds.
 */
export default function Profitable({ data, farmed, onOpen, onHover }) {
  const [mode, setMode] = useState('listed')
  const [scope, setScope] = useState('all')

  // Materials the last scan carried no price for. That means "not seen by that
  // scan", not "never sold" -- a scan is a snapshot, and thin custom materials
  // often sell out or get listed between scans.
  const unbuyable = useMemo(() => {
    const set = new Set()
    for (const item of data.items) {
      if (item.kind !== 'item') continue
      const priced = item.price?.buy !== null && item.price?.buy !== undefined
      if (!priced && item.farmable) set.add(item.id)
    }
    return set
  }, [data])

  const model = useMemo(
    () => createModel(
      data.items,
      mode === 'buy' ? new Set() : mode === 'listed' ? unbuyable : farmed,
      data.meta.cut,
    ),
    [data, farmed, mode, unbuyable],
  )

  const { rows, excluded } = useMemo(() => {
    const rows = []
    const excluded = []
    for (const item of data.items) {
      // Any step you can buy into, make, and sell out of -- finished products
      // and the intermediates that feed them alike.
      if (!item.craft?.reagents?.length) continue
      if (scope === 'products' && !item.seed) continue
      if (scope === 'steps' && item.seed) continue
      const result = model.evaluate(item)
      if (!result || result.salePrice === null) continue
      if (result.profit === null) {
        excluded.push({ item, result })
      } else {
        rows.push({ item, result })
      }
    }
    rows.sort((a, b) => b.result.profit - a.result.profit)
    return { rows, excluded }
  }, [data, model, scope])

  const blockers = useMemo(() => {
    const counts = new Map()
    for (const { result } of excluded) {
      for (const id of result.missing) counts.set(id, (counts.get(id) || 0) + 1)
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([id, n]) => `${model.byId.get(id)?.name || `item ${id}`} (${n})`)
  }, [excluded, model])

  return (
    <div className="pane" style={{ padding: '18px 22px' }}>
      <div className="chips" style={{ marginBottom: 14 }}>
        {MODES.map((m) => (
          <button
            key={m.id}
            className="chip"
            aria-pressed={mode === m.id}
            onClick={() => setMode(m.id)}
            title={m.hint}
          >
            {m.label}
          </button>
        ))}
        <span className="muted" style={{ fontSize: 13, alignSelf: 'center' }}>
          {MODES.find((m) => m.id === mode).hint}
        </span>
      </div>

      <div className="chips" style={{ marginBottom: 14 }}>
        {SCOPES.map((sc) => (
          <button
            key={sc.id}
            className="chip"
            aria-pressed={scope === sc.id}
            onClick={() => setScope(sc.id)}
          >
            {sc.label}
          </button>
        ))}
      </div>

      {rows.length === 0 && (
        <div className="note warn">
          Nothing can be costed in this mode: every recipe needs at least one material
          the last scan carried no price for. Run an auction scan in game, <code>/reload</code>,
          then <code>python sync_prices.py</code> — or switch to “Buy what is priced”.
        </div>
      )}

      {excluded.length > 0 && rows.length > 0 && (
        <div className="note">
          {excluded.length} of {rows.length + excluded.length} recipes excluded — the last
          scan carried no price for {blockers.join(', ')}. They may well be listed now;
          re-scan in game and run <code>sync_prices.py</code> to pick them up.
        </div>
      )}

      <table className="rank">
        <thead>
          <tr>
            <th className="idx">#</th>
            <th>Product</th>
            <th>Type</th>
            <th>Profession</th>
            <th className="r">Sells for</th>
            <th className="r">Mat cost</th>
            <th className="r">Profit</th>
            <th className="r">ROI</th>
            <th className="r" title="Units currently listed on the auction house — how much you could realistically move">Listed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ item, result }, index) => (
            <tr
              key={item.key}
              onClick={() => onOpen(item.key)}
              onMouseEnter={(e) => onHover(item, e)}
              onMouseLeave={() => onHover(null)}
            >
              <td className="idx num">{index + 1}</td>
              <td>
                <div className="matname">
                  <Icon item={item} size="sm" />
                  <span className={QUALITY_CLASS(item.quality)}>{item.name}</span>
                </div>
              </td>
              <td><span className={`badge ${rowKind(item)}`}>{rowKind(item)}</span></td>
              <td className="muted">{item.craft?.profession || '—'}</td>
              <td className="r num">{money(result.salePrice)}</td>
              <td className="r num">{money(result.cost)}</td>
              <td className={`r num ${result.profit >= 0 ? 'v good' : 'v bad'}`}>
                {money(result.profit)}
              </td>
              <td className="r num muted">
                {result.roi === null ? 'all farmed' : `${Math.round(result.roi)}%`}
              </td>
              <td className="r num muted">
                {item.price?.quantity ?? item.saleQuantity ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
