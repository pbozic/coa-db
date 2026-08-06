import Icon from './Icon.jsx'
import PriceTrend from './PriceTrend.jsx'
import { QUALITY_CLASS, money, farmRoute, isGathered } from '../model.js'

function ObtainRoute({ item, model, onOpen }) {
  const craft = item.craft
  if (craft?.expandable && craft.kind === 'conversion') {
    const input = craft.reagents[0]
    const inputItem = input ? model.byId.get(input.id) : null
    return (
      <div className="callout">
        <strong>Convert it.</strong> {craft.method || 'Converted from another item'}
        {input && (
          <>
            <br />
            Input:{' '}
            <span className="link" onClick={() => onOpen(`item:${input.id}`)}>
              {inputItem?.name || `item ${input.id}`}
            </span>
          </>
        )}
      </div>
    )
  }
  if (craft?.expandable) {
    return (
      <div className="callout recipe">
        <strong>Craft it.</strong> {craft.profession || 'Crafted'} {craft.learnedAt || ''}
        {craft.station ? ` at ${craft.station}` : ''}
      </div>
    )
  }
  const effect = (item.effect || '').replace(/^Use:\s*/, '')
  if (/^(Refined into|Feed to|Water an|Fertilize)/.test(effect)) {
    return (
      <div className="callout">
        <strong>Use this item.</strong> {effect}
        {item.sources.length > 0 && <><br />Obtained from: {item.sources.join(', ')}</>}
      </div>
    )
  }
  if (item.missing) {
    return (
      <div className="callout missing">
        Referenced by a recipe but absent from db.ascension.gg, so it has no name,
        icon or source. It shows blank in game too.
      </div>
    )
  }
  return <div className="callout world">{item.sources.join(', ') || 'No source recorded'}</div>
}

function MaterialRow({ line, farmed, onToggleFarm, onOpen, onHover }) {
  const mat = line.item
  const best = mat?.zoneSources?.[0]
  return (
    <tr className={line.source === 'farmed' ? 'farmed' : undefined}>
      <td>
        <input
          type="checkbox"
          checked={farmed}
          onChange={() => onToggleFarm(line.id)}
          title="I have this, or will farm it"
        />
      </td>
      <td className="qty">{line.qty}&times;</td>
      <td>
        <div className="matname">
          <Icon item={mat} size="sm" />
          <span
            className={`link ${QUALITY_CLASS(mat?.quality)}`}
            onClick={() => onOpen(`item:${line.id}`)}
            onMouseEnter={(e) => mat && onHover(mat, e)}
            onMouseLeave={() => onHover(null)}
          >
            {mat?.name || `Item ${line.id}`}
          </span>
          {mat?.missing && <span className="miss">(missing)</span>}
        </div>
      </td>
      <td className="zones">
        {isGathered(mat) ? (
          <span className="zone">Gather · skill {Math.min(...mat.gatheredFrom.map((g) => g.skill || 0))}</span>
        ) : best ? (
          <>
            <span className="zone">{best.zone}</span>{' '}
            <span className="muted">{best.percent?.toFixed(0)}% · lvl {best.level}</span>
          </>
        ) : (
          ''
        )}
      </td>
      <td className="r num">
        {line.source === 'farmed' ? (
          <span className="cost-none">farming</span>
        ) : line.line === null ? (
          <span className="cost-none">no price</span>
        ) : (
          <>
            {money(line.line)}
            {line.qty !== 1 && <span className="muted"> ({money(line.unit)} ea)</span>}
            {line.source === 'crafted' && <span className="muted"> crafted</span>}
          </>
        )}
      </td>
    </tr>
  )
}

export default function ItemDetail({ item, model, farmed, onToggleFarm, onOpen, onHover, meta, history }) {
  if (!item) return <div className="empty">Select an item, or search above.</div>

  const result = model.evaluate(item)
  const route = result ? farmRoute(item, model) : { stops: [], gathered: [] }
  const usedIn = item.usedIn.map((id) => model.byId.get(id)).filter(Boolean)

  return (
    <>
      <div className="detail-head">
        <Icon item={item} size="lg" />
        <h2 className={QUALITY_CLASS(item.quality)}>{item.name}</h2>
      </div>
      <div className="submeta">
        {item.kind} {item.id} ·{' '}
        <a href={`https://db.ascension.gg/?${item.kind}=${item.id}`} target="_blank" rel="noreferrer">
          db.ascension.gg
        </a>
        {item.custom && ' · custom'}
        {item.seed && ' · sellable product'}
      </div>

      {item.price && (item.price.buy != null || item.price.sell != null) && (
        <div className="section">
          <h3>Auction house</h3>
          <div className="stats">
            <div className="stat"><div className="k">Buy</div><div className="v">{money(item.price.buy)}</div></div>
            <div className="stat"><div className="k">Sell</div><div className="v">{money(item.price.sell)}</div></div>
            <div className="stat">
              <div className="k">Market (14d)</div>
              <div className="v">{money(item.price.market)}</div>
            </div>
            <div className="stat">
              <div className="k">Lowest listing</div>
              <div className="v">{money(item.price.minBuyout)}</div>
              {item.price.quantity != null && (
                <div className="k">{item.price.quantity} available</div>
              )}
            </div>
          </div>
          {item.price.minBuyout != null && item.price.market != null &&
            item.price.minBuyout < item.price.market * 0.5 && (
            <div className="note" style={{ marginTop: 10 }}>
              The lowest listing is under half the 14-day market value, so it is
              treated as an outlier and {money(item.price.buy)} is used instead.
            </div>
          )}
        </div>
      )}

      <PriceTrend
        points={history?.series?.[String(item.saleItemId ?? item.id)]}
        turnover={history?.turnover?.[String(item.saleItemId ?? item.id)]}
        name={item.name}
      />

      {item.effect && (
        <div className="section">
          <h3>Effect</h3>
          <div className="effect">{item.effect}</div>
        </div>
      )}

      <div className="section">
        <h3>How to get it</h3>
        <ObtainRoute item={item} model={model} onOpen={onOpen} />
      </div>

      {result && (
        <div className="section">
          <h3>
            Recipe — makes {result.yieldCount}
            {item.craft.profession && ` · ${item.craft.profession} ${item.craft.learnedAt || ''}`}
          </h3>
          <table className="mats">
            <thead>
              <tr>
                <th title="Tick what you will farm or already have">Farm</th>
                <th className="r">Qty</th>
                <th>Material</th>
                <th>Where to farm</th>
                <th className="r">Cost</th>
              </tr>
            </thead>
            <tbody>
              {result.lines.map((line) => (
                <MaterialRow
                  key={line.id}
                  line={line}
                  farmed={farmed.has(line.id)}
                  onToggleFarm={onToggleFarm}
                  onOpen={onOpen}
                  onHover={onHover}
                />
              ))}
              <tr className="totals">
                <td colSpan={4}>
                  Material cost{result.missing.length > 0 && ' (incomplete)'}
                </td>
                <td className="r num">{money(result.cost)}</td>
              </tr>
            </tbody>
          </table>
          {item.craft.recipeItem && (
            <div className="submeta" style={{ marginTop: 8 }}>
              Learned from: {item.craft.recipeItem}
            </div>
          )}
          {(route.stops.length > 0 || route.gathered.length > 0) && (
            <div className="note" style={{ marginTop: 12 }}>
              <strong>Farm route</strong> — one stop per material, best rate against mob level:
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {route.stops.map((stop) => (
                  <li key={stop.zone}>
                    <span className="zone">{stop.zone}</span>{' '}
                    <span className="muted">
                      {stop.items
                        .map((i) => `${i.name} ${i.percent?.toFixed(0)}% off ${i.npc} (lvl ${i.level})`)
                        .join(', ')}
                    </span>
                  </li>
                ))}
                {route.gathered.length > 0 && (
                  <li>
                    <span className="zone">Gathered</span>{' '}
                    <span className="muted">
                      {route.gathered.map((g) => `${g.name} (skill ${g.skill})`).join(', ')}
                    </span>
                  </li>
                )}
              </ul>
            </div>
          )}
        </div>
      )}

      {result && (
        <div className="section">
          <h3>Profit per craft — sells for {money(result.salePrice)}</h3>
          <div className="stats">
            <div className="stat">
              <div className="k">Profit</div>
              <div className={`v ${result.profit === null ? '' : result.profit >= 0 ? 'good' : 'bad'}`}>
                {money(result.profit)}
              </div>
            </div>
            <div className="stat">
              <div className="k">Material cost</div>
              <div className="v">{money(result.cost)}</div>
            </div>
            <div className="stat">
              <div className="k">Revenue after {Math.round(meta.cut * 100)}% cut</div>
              <div className="v">{money(result.revenue)}</div>
            </div>
            <div className="stat">
              <div className="k">Listed on AH</div>
              <div className="v">{item.price?.quantity ?? item.saleQuantity ?? '—'}</div>
            </div>
            <div className="stat">
              <div className="k">ROI</div>
              <div className="v">{result.roi === null ? (result.profit === null ? '—' : 'all farmed') : `${Math.round(result.roi)}%`}</div>
            </div>
          </div>
          {result.missing.length > 0 && (
            <div className="note warn" style={{ marginTop: 12 }}>
              The last scan carried no price for{' '}
              {result.missing.map((id) => model.byId.get(id)?.name || `item ${id}`).join(', ')}
              . That is this snapshot, not a verdict — they may be listed now. Re-scan, or
              tick them above if you farm them yourself.
            </div>
          )}
        </div>
      )}

      {item.gatheredFrom?.length > 0 && (
        <div className="section">
          <h3>Gathered from</h3>
          <div className="callout world">
            {item.gatheredFrom[0].percent}% chance from these nodes, lowest skill first:
            <div className="badge-row">
              {item.gatheredFrom.map((g) => (
                <span key={g.name} className="badge">
                  {g.name} · {g.skill}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {item.containedIn?.length > 0 && (
        <div className="section">
          <h3>Found in</h3>
          <div className="callout world">
            {item.containedIn.map((c) => `${c.name} (${c.percent?.toFixed(1)}%)`).join(', ')}
          </div>
        </div>
      )}

      {item.drops?.length > 0 && (
        <div className="section">
          <h3>Dropped by</h3>
          <table className="mats">
            <thead>
              <tr>
                <th className="r">Rate</th><th>Creature</th><th>Level</th><th>Zone</th>
              </tr>
            </thead>
            <tbody>
              {item.drops.map((drop) => (
                <tr key={`${drop.npc_id}-${drop.min_level}-${(drop.zones || []).join()}`}>
                  <td className="r num">{drop.percent?.toFixed(1)}%</td>
                  <td>
                    <a
                      className="link"
                      href={`https://db.ascension.gg/?npc=${drop.npc_id}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {drop.npc}
                    </a>
                    {drop.elite && <span className="muted"> elite</span>}
                  </td>
                  <td className="num muted">
                    {drop.min_level === drop.max_level ? drop.min_level : `${drop.min_level}-${drop.max_level}`}
                  </td>
                  <td className="zones">{(drop.zones || []).join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {usedIn.length > 0 && (
        <div className="section">
          <h3>Used in ({usedIn.length})</h3>
          <table className="mats">
            <tbody>
              {usedIn.map((used) => (
                <tr key={used.key}>
                  <td style={{ width: 30 }}><Icon item={used} size="sm" /></td>
                  <td>
                    <span className="link" onClick={() => onOpen(used.key)}>{used.name}</span>
                  </td>
                  <td className="r"><span className={`badge ${used.family}`}>{used.family}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
