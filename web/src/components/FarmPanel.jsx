import { useMemo, useState } from 'react'
import Icon from './Icon.jsx'
import { QUALITY_CLASS, money } from '../model.js'

/**
 * Manage the "I will farm this" list.
 *
 * Ticking materials inside a recipe only reaches the ones a recipe happens to
 * use, so the list is also editable directly: search the whole catalog, add
 * anything, and drop entries without hunting for the recipe that introduced
 * them.
 */
export default function FarmPanel({ data, farmed, onToggle, onClear, onClose, onOpen }) {
  const [query, setQuery] = useState('')

  const byId = useMemo(
    () => new Map(data.items.filter((i) => i.kind === 'item').map((i) => [i.id, i])),
    [data],
  )

  const chosen = useMemo(
    () => [...farmed].map((id) => byId.get(id)).filter(Boolean)
      .sort((a, b) => a.name.localeCompare(b.name)),
    [farmed, byId],
  )

  // Only materials are worth farming; finished products are what you sell.
  const candidates = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
    if (!terms.length) return []
    return data.items
      .filter((i) => i.kind === 'item' && !farmed.has(i.id))
      .filter((i) => {
        const hay = `${i.name} ${(i.zones || []).join(' ')} ${i.sources.join(' ')}`.toLowerCase()
        return terms.every((t) => hay.includes(t))
      })
      .slice(0, 12)
  }, [data, query, farmed])

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h2>My farm list</h2>
          <button className="chip" onClick={onClose}>Close</button>
        </div>

        <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
          Anything here is treated as costing nothing but your time, so profit is
          calculated as if you already have it.
        </p>

        <input
          className="search"
          type="search"
          placeholder="Add a material — search by name, zone or source"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ width: '100%', margin: '4px 0 10px' }}
        />

        {candidates.length > 0 && (
          <div className="candidates">
            {candidates.map((item) => (
              <button key={item.key} className="candidate" onClick={() => onToggle(item.id)}>
                <Icon item={item} size="sm" />
                <span className={QUALITY_CLASS(item.quality)}>{item.name}</span>
                <span className="muted">{(item.zones || [])[0] || item.sources[0] || ''}</span>
                <span className="plus">+</span>
              </button>
            ))}
          </div>
        )}

        {chosen.length === 0 ? (
          <div className="empty">
            Nothing on the list yet. Search above, or tick materials on a recipe.
          </div>
        ) : (
          <>
            <div className="drawer-count">
              {chosen.length} material{chosen.length === 1 ? '' : 's'}
              <button className="chip" onClick={onClear}>Clear all</button>
            </div>
            <table className="mats">
              <tbody>
                {chosen.map((item) => (
                  <tr key={item.key}>
                    <td style={{ width: 28 }}><Icon item={item} size="sm" /></td>
                    <td>
                      <span className={`link ${QUALITY_CLASS(item.quality)}`}
                            onClick={() => { onOpen(item.key); onClose() }}>
                        {item.name}
                      </span>
                      <div className="zones">
                        {(item.zones || []).join(', ') || item.sources.join(', ') || '—'}
                      </div>
                    </td>
                    <td className="r num muted">
                      {item.price?.buy != null ? money(item.price.buy) : '—'}
                      <div className="zones">saved per unit</div>
                    </td>
                    <td className="r" style={{ width: 34 }}>
                      <button className="remove" title="Remove from the list"
                              onClick={() => onToggle(item.id)}>×</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}
