// Cost and profit model, mirroring profit.py so the "I'll farm this" checkboxes
// can recompute live in the browser.

export const QUALITY_CLASS = (q) => (q === null || q === undefined ? '' : `q${q}`)

export function money(copper) {
  if (copper === null || copper === undefined) return '—'
  const negative = copper < 0
  const total = Math.abs(Math.round(copper))
  const gold = Math.floor(total / 10000)
  const silver = Math.floor((total % 10000) / 100)
  const rest = total % 100
  const parts = []
  if (gold) parts.push(`${gold.toLocaleString()}g`)
  if (silver || gold) parts.push(`${silver}s`)
  if (rest || !parts.length) parts.push(`${rest}c`)
  return (negative ? '-' : '') + parts.join(' ')
}

/**
 * Build a costing function over the item graph.
 *
 * `farmed` is the set of item ids the user has ticked as "I have or will farm
 * this"; those cost nothing but time. Everything else is bought at its auction
 * price, or made from its own reagents when that is cheaper — and is simply
 * unknown when neither is possible, rather than being treated as free.
 */
export function createModel(items, farmed, cut = 0.05) {
  const byId = new Map()
  for (const item of items) if (item.kind === 'item') byId.set(item.id, item)

  const memo = new Map()

  function unitCost(id, path = new Set()) {
    if (memo.has(id)) return memo.get(id)
    if (path.has(id)) return { cost: null, source: 'cycle' } // cyclic recipe guard

    const item = byId.get(id)
    let best = null

    if (farmed.has(id)) {
      best = { cost: 0, source: 'farmed' }
    } else {
      const buy = item?.price?.buy
      if (buy !== null && buy !== undefined) best = { cost: buy, source: 'bought' }

      if (item?.craft?.expandable) {
        const deeper = new Set(path)
        deeper.add(id)
        let total = 0
        let complete = true
        for (const reagent of item.craft.reagents) {
          const sub = unitCost(reagent.id, deeper)
          if (sub.cost === null) { complete = false; break }
          total += sub.cost * reagent.qty
        }
        if (complete) {
          const made = total / Math.max(1, item.craft.yield)
          if (best === null || made < best.cost) best = { cost: made, source: 'crafted' }
        }
      }
    }

    if (best === null) best = { cost: null, source: 'unknown' }
    if (path.size === 0) memo.set(id, best)
    return best
  }

  function evaluate(item) {
    const craft = item?.craft
    if (!craft || !craft.reagents.length) return null

    let total = 0
    const missing = []
    const lines = craft.reagents.map((reagent) => {
      const sub = unitCost(reagent.id)
      const line = sub.cost === null ? null : sub.cost * reagent.qty
      if (line === null) missing.push(reagent.id)
      else total += line
      return { ...reagent, unit: sub.cost, line, source: sub.source, item: byId.get(reagent.id) }
    })

    // salePrice is exported alongside the item because an enchant's scroll is
    // not a node in the graph and so cannot be looked up by id here.
    const salePrice = item.salePrice ?? byId.get(item.saleItemId)?.price?.sell ?? null
    const yieldCount = Math.max(1, craft.yield)
    const cost = missing.length ? null : total
    const revenue = salePrice === null ? null : salePrice * yieldCount * (1 - cut)
    const profit = cost === null || revenue === null ? null : revenue - cost
    const roi = profit !== null && cost ? (profit / cost) * 100 : null

    return { lines, missing, cost, revenue, profit, roi, salePrice, yieldCount }
  }

  return { byId, unitCost, evaluate }
}

/**
 * Every material a recipe consumes, at any depth.
 *
 * "What can I make with these three things" needs the whole tree, not the top
 * level: a flask lists Blightroot Extract, but what you actually farmed is the
 * Blightroot two steps down.
 */
export function allMaterials(item, model, depth = 0, seen = new Set()) {
  for (const reagent of item?.craft?.reagents || []) {
    if (seen.has(reagent.id) || depth > 6) continue
    seen.add(reagent.id)
    const child = model.byId.get(reagent.id)
    if (child?.craft?.expandable) allMaterials(child, model, depth + 1, seen)
  }
  return seen
}

/**
 * True when an item is picked from a node rather than farmed off mobs.
 *
 * Herbs carry incidental mob drops at a couple of percent, so ranking their
 * zones by drop rate points you at a level 19 mob in Wailing Caverns when the
 * real answer is "pick it with Herbalism 220".
 */
export function isGathered(item) {
  return Boolean(item?.sources?.includes('gathering') && item?.gatheredFrom?.length)
}

/**
 * Where to actually go to gather a recipe's materials.
 *
 * Each material needs exactly one zone, not all of the zones it can drop in —
 * listing every zone reads as a route when it is really a set of alternatives.
 * A material is assigned to a zone already on the route when that zone is a
 * reasonable option for it, so one stop can cover several materials; otherwise
 * it takes its own best-scoring zone.
 */
export function farmRoute(item, model) {
  const needed = []
  const gathered = []
  const seen = new Set()

  const walk = (id, depth = 0) => {
    if (depth > 6 || seen.has(id)) return
    seen.add(id)
    const node = model.byId.get(id)
    if (!node) return
    if (node.craft?.expandable) {
      for (const reagent of node.craft.reagents) walk(reagent.id, depth + 1)
      return
    }
    if (isGathered(node)) gathered.push(node)
    else if (node.zoneSources?.length) needed.push(node)
  }
  for (const reagent of item.craft?.reagents || []) walk(reagent.id)

  // Materials with the fewest options are placed first, so the zones they force
  // onto the route are available to cover the more flexible ones.
  needed.sort((a, b) => a.zoneSources.length - b.zoneSources.length)

  const stops = new Map()
  for (const node of needed) {
    const best = node.zoneSources[0]
    const shared = node.zoneSources.find(
      (option) => stops.has(option.zone) && option.score >= best.score * 0.6,
    )
    const chosen = shared || best
    if (!stops.has(chosen.zone)) stops.set(chosen.zone, { zone: chosen.zone, items: [] })
    stops.get(chosen.zone).items.push({ name: node.name, ...chosen })
  }
  return {
    stops: [...stops.values()].sort((a, b) => b.items.length - a.items.length),
    gathered: gathered.map((node) => ({
      name: node.name,
      skill: Math.min(...node.gatheredFrom.map((g) => g.skill || 0)),
    })),
  }
}
