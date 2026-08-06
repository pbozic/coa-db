import Icon from './Icon.jsx'
import { QUALITY_CLASS } from '../model.js'

export default function ItemList({ items, selected, onSelect, onHover }) {
  if (!items.length) return <div className="empty">No matches.</div>
  return (
    <div role="listbox">
      {items.map((item) => (
        <div
          key={item.key}
          className="item-row"
          role="option"
          aria-selected={selected === item.key}
          onClick={() => onSelect(item.key)}
          onMouseEnter={(e) => onHover(item, e)}
          onMouseLeave={() => onHover(null)}
        >
          <Icon item={item} />
          <span className={`name ${QUALITY_CLASS(item.quality)}`}>
            {item.name}
            {item.missing && <span className="miss"> (missing)</span>}
          </span>
          <span className={`badge ${item.family}`}>{item.family}</span>
        </div>
      ))}
    </div>
  )
}
