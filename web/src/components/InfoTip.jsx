/**
 * A small "what does this mean" marker.
 *
 * CSS-only on hover and focus, so it costs nothing and still reaches keyboard
 * users. Used where a number needs a caveat that would be noise inline.
 */
export default function InfoTip({ children, label = 'What this means' }) {
  return (
    <span className="infotip" tabIndex={0} role="note" aria-label={label}>
      <span className="infotip-mark" aria-hidden="true">?</span>
      <span className="infotip-body">{children}</span>
    </span>
  )
}
