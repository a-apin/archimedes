import { useState, useRef, useEffect, useCallback, useId } from 'react'
import { createPortal } from 'react-dom'

export default function CustomSelect({
  value,
  onChange,
  options = [],
  placeholder = 'Select…',
  disabled = false,
  className = '',
  style,
  // Without this the trigger is announced as its current value alone ("All
  // Categories, button") with no indication of what it selects (4.1.2).
  ariaLabel,
  ariaLabelledBy,
}) {
  const [open, setOpen] = useState(false)
  const listboxId = `cs-listbox-${useId()}`
  const optionId = (i) => `${listboxId}-opt-${i}`
  const [dropdownStyle, setDropdownStyle] = useState({})
  const triggerRef = useRef(null)
  const dropdownRef = useRef(null)

  const selectedIndex = options.findIndex(o => o.value === value)
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined

  const close = useCallback(() => setOpen(false), [])

  const positionDropdown = useCallback(() => {
    if (!triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    const spaceBelow = window.innerHeight - rect.bottom
    const dropUp = spaceBelow < 220 && rect.top > 220
    setDropdownStyle({
      position: 'fixed',
      left: rect.left,
      width: rect.width,
      minWidth: Math.max(rect.width, 160),
      ...(dropUp
        ? { bottom: window.innerHeight - rect.top + 6 }
        : { top: rect.bottom + 6 }),
      zIndex: 9999,
    })
  }, [])

  const toggle = () => {
    if (disabled) return
    if (!open) positionDropdown()
    setOpen(o => !o)
  }

  // Reposition on scroll or resize while open
  useEffect(() => {
    if (!open) return
    const update = () => positionDropdown()
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [open, positionDropdown])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (
        triggerRef.current?.contains(e.target) ||
        dropdownRef.current?.contains(e.target)
      ) return
      close()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, close])

  // Keyboard navigation
  const handleKeyDown = (e) => {
    if (disabled) return
    const idx = options.findIndex(o => o.value === value)
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (!open) { positionDropdown(); setOpen(true) } else close()
    }
    if (e.key === 'Escape') close()
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) { positionDropdown(); setOpen(true); return }
      const next = options.slice(idx + 1).find(o => !o.disabled)
      if (next) onChange(next.value)
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      const prev = [...options].slice(0, idx).reverse().find(o => !o.disabled)
      if (prev) onChange(prev.value)
    }
  }

  const select = (opt) => {
    if (opt.disabled) return
    onChange(opt.value)
    close()
  }

  return (
    <div
      className={`cs-wrap ${open ? 'cs-open' : ''} ${disabled ? 'cs-disabled' : ''} ${className}`}
      style={style}
    >
      <button
        ref={triggerRef}
        type="button"
        className="cs-trigger"
        onClick={toggle}
        onKeyDown={handleKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        // ArrowUp/ArrowDown mutate the selection while focus stays on the
        // trigger; without activedescendant the value changed silently.
        aria-activedescendant={
          open && selectedIndex >= 0 ? optionId(selectedIndex) : undefined
        }
        disabled={disabled}
      >
        <span className="cs-value">
          {selected ? (
            <>
              {selected.icon && <span className={selected.icon} style={{ width: 16, height: 16, flexShrink: 0 }} />}
              <span>{selected.label}</span>
            </>
          ) : (
            <span className="cs-placeholder">{placeholder}</span>
          )}
        </span>
        <span
          className="cs-chevron i-lucide-chevron-down"
          style={{ width: 14, height: 14, flexShrink: 0, transition: 'transform 0.15s', transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }}
        />
      </button>

      {open && createPortal(
        <div
          ref={dropdownRef}
          className="cs-dropdown"
          style={dropdownStyle}
          role="listbox"
          id={listboxId}
          aria-label={ariaLabel}
          aria-labelledby={ariaLabel ? undefined : ariaLabelledBy}
        >
          {options.map((opt, i) => (
            <div
              key={opt.value}
              id={optionId(i)}
              className={`cs-option ${opt.value === value ? 'cs-selected' : ''} ${opt.disabled ? 'cs-option-disabled' : ''}`}
              role="option"
              aria-selected={opt.value === value}
              onMouseDown={(e) => { e.preventDefault(); select(opt) }}
            >
              {opt.icon && <span className={opt.icon} style={{ width: 16, height: 16, flexShrink: 0 }} />}
              <span className="cs-option-label">{opt.label}</span>
              {opt.value === value && (
                <span className="i-lucide-check cs-check" style={{ width: 13, height: 13, marginLeft: 'auto', flexShrink: 0 }} />
              )}
            </div>
          ))}
        </div>,
        document.body
      )}
    </div>
  )
}
