import type { CSSProperties } from 'react'

export type CursorBandRow = {
  letter: string
  nm: string
  value: number | null
  color: string
  on: boolean
}

type Props = {
  open: boolean
  time: string | null
  values: CursorBandRow[] | null
  hint?: string
}

/** Extrae letra y nm de claves tipo A_410nm / raw_A_410nm / A_410nm_abs */
export function parseBandKey(key: string): { letter: string; nm: string } {
  const clean = key.replace(/^raw_/, '').replace(/_abs$/, '')
  const m = clean.match(/^([A-Za-z])_(\d+)/)
  if (m) return { letter: m[1].toUpperCase(), nm: m[2] }
  return { letter: clean.slice(0, 1) || '?', nm: clean.replace(/\D/g, '') || '—' }
}

export function CursorValuesPanel({ open, time, values, hint }: Props) {
  if (!open) return null

  return (
    <div className="cursor-panel">
      <div className="cursor-panel-head">
        <strong>Valores en cursor</strong>
        <span className="muted mono">{time ?? 'Pasa el ratón por la gráfica'}</span>
      </div>
      {values ? (
        <div className="cursor-grid">
          {values.map((row) => (
            <div
              key={`${row.letter}-${row.nm}`}
              className={`cursor-cell ${row.on ? '' : 'off'}`}
              style={{ '--band': row.color } as CSSProperties}
              title={`${row.letter} · ${row.nm} nm`}
            >
              <span className="cursor-cell-band">
                <span className="cursor-cell-letter">{row.letter}</span>
                <span className="cursor-cell-nm">{row.nm} nm</span>
              </span>
              <span className="cursor-cell-value mono">
                {row.value == null ? '—' : row.value.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          {hint ?? 'Mueve el cursor sobre un punto para ver las 18 bandas a esa hora.'}
        </p>
      )}
    </div>
  )
}
