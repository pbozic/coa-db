export default function Icon({ item, size = '', dir = 'assets/icons' }) {
  if (!item?.icon) return <span className={`icon ${size}`} aria-hidden="true" />
  return (
    <img
      className={`icon ${size}`}
      src={`${dir}/${item.icon}`}
      alt=""
      loading="lazy"
    />
  )
}
