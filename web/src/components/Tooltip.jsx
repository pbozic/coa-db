import { useEffect, useState } from 'react'
import Icon from './Icon.jsx'
import { QUALITY_CLASS, money } from '../model.js'

/** Follows the cursor, flipping near the viewport edge so it never clips. */
export default function Tooltip({ item, position }) {
  const [size, setSize] = useState({ w: 320, h: 120 })
  const [node, setNode] = useState(null)

  useEffect(() => {
    if (node) setSize({ w: node.offsetWidth, h: node.offsetHeight })
  }, [node, item])

  if (!item || !position) return null

  const pad = 16
  let left = position.x + pad
  let top = position.y + pad
  if (left + size.w > window.innerWidth - 8) left = position.x - size.w - pad
  if (top + size.h > window.innerHeight - 8) top = position.y - size.h - pad

  const meta = []
  if (item.sources?.length) meta.push(item.sources.join(', '))
  if (item.price?.buy != null) meta.push(`AH ${money(item.price.buy)}`)

  return (
    <div className="tip" ref={setNode} style={{ left: Math.max(8, left), top: Math.max(8, top) }}>
      <div className={`tname ${QUALITY_CLASS(item.quality)}`}>
        <Icon item={item} size="sm" />
        {item.name}
      </div>
      {item.effect && <div>{item.effect}</div>}
      {item.zones?.length > 0 && (
        <div className="tmeta">Farm in: {item.zones.join(', ')}</div>
      )}
      {meta.length > 0 && <div className="tmeta">{meta.join(' · ')}</div>}
    </div>
  )
}
