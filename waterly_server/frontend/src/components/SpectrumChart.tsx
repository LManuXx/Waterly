import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { WAVELENGTHS, type Telemetry } from '../api/client'
import { CursorValuesPanel, parseBandKey, type CursorBandRow } from './CursorValuesPanel'
import { bindZoomInteractions, resetZoom, zoomCursorOptions, zoomSelectOptions } from '../lib/uplotZoom'

const MAX_POINTS = 120
const BAND_COLORS = [
  '#3d9a8b', '#5aaf7a', '#c4a35a', '#7aa2c4', '#c45c5c', '#9b7bc4',
  '#4db6ac', '#81c784', '#ffb74d', '#64b5f6', '#e57373', '#ba68c8',
  '#26a69a', '#66bb6a', '#ffa726', '#42a5f5', '#ef5350', '#ab47bc',
]

type Props = {
  telemetry: Telemetry
  mode: 'raw' | 'abs'
}

function bandLabel(w: string) {
  const { letter, nm } = parseBandKey(w)
  return `${letter} ${nm}`
}

function formatClock(tsSec: number) {
  const d = new Date(tsSec * 1000)
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function SpectrumChart({ telemetry, mode }: Props) {
  const elRef = useRef<HTMLDivElement>(null)
  const plotRef = useRef<uPlot | null>(null)
  const dataRef = useRef<{ t: number[]; series: number[][] }>({
    t: [],
    series: WAVELENGTHS.map(() => []),
  })
  const lastSpectrumTs = useRef<number | null>(null)
  const lastFp = useRef<string>('')
  const [visible, setVisible] = useState(() => WAVELENGTHS.map((_, i) => i < 6))
  const [pointCount, setPointCount] = useState(0)
  const [panelOpen, setPanelOpen] = useState(true)
  const [cursor, setCursor] = useState<{ time: string; values: CursorBandRow[] } | null>(null)

  const visibleRef = useRef(visible)
  visibleRef.current = visible

  useEffect(() => {
    dataRef.current = { t: [], series: WAVELENGTHS.map(() => []) }
    lastSpectrumTs.current = null
    lastFp.current = ''
    setPointCount(0)
    setCursor(null)
  }, [mode])

  const onCursor = useCallback((u: uPlot) => {
    const idx = u.cursor.idx
    if (idx == null || idx < 0 || !u.data[0]?.[idx]) {
      setCursor(null)
      return
    }
    const t = u.data[0][idx] as number
    const values = WAVELENGTHS.map((w, i) => {
      const v = u.data[i + 1]?.[idx]
      const { letter, nm } = parseBandKey(w)
      return {
        letter,
        nm,
        value: typeof v === 'number' && Number.isFinite(v) ? v : null,
        color: BAND_COLORS[i % BAND_COLORS.length],
        on: visibleRef.current[i],
      }
    })
    setCursor({ time: formatClock(t), values })
  }, [])

  useEffect(() => {
    if (!elRef.current) return
    const el = elRef.current
    let unbindZoom: (() => void) | null = null

    const opts: uPlot.Options = {
      width: Math.max(el.clientWidth, 120),
      height: Math.max(el.clientHeight, 160),
      legend: { show: false },
      cursor: zoomCursorOptions(),
      select: zoomSelectOptions(),
      hooks: {
        setCursor: [onCursor],
        ready: [
          (u) => {
            unbindZoom = bindZoomInteractions(u)
          },
        ],
      },
      series: [
        {},
        ...WAVELENGTHS.map((w, i) => ({
          label: bandLabel(w),
          stroke: BAND_COLORS[i % BAND_COLORS.length],
          width: 1.5,
          show: visible[i],
          points: { show: true, size: 4 },
        })),
      ],
      axes: [
        {
          stroke: '#8b9aab',
          grid: { stroke: '#2e3a48' },
          ticks: { stroke: '#2e3a48' },
          values: (_u, splits) => splits.map((s) => formatClock(s)),
        },
        {
          stroke: '#8b9aab',
          grid: { stroke: '#2e3a48' },
          ticks: { stroke: '#2e3a48' },
          size: 48,
        },
      ],
    }
    const empty: uPlot.AlignedData = [[], ...WAVELENGTHS.map(() => [])]
    plotRef.current = new uPlot(opts, empty, el)

    const d = dataRef.current
    if (d.t.length) {
      plotRef.current.setData([d.t, ...d.series] as uPlot.AlignedData)
    }

    const ro = new ResizeObserver(() => {
      if (!plotRef.current || !elRef.current) return
      const w = elRef.current.clientWidth
      const h = elRef.current.clientHeight
      if (w > 0 && h > 0) plotRef.current.setSize({ width: w, height: h })
    })
    ro.observe(el)

    return () => {
      unbindZoom?.()
      ro.disconnect()
      plotRef.current?.destroy()
      plotRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, onCursor])

  useEffect(() => {
    const plot = plotRef.current
    if (!plot) return
    visible.forEach((show, i) => {
      plot.setSeries(i + 1, { show })
    })
  }, [visible])

  useEffect(() => {
    const keys = WAVELENGTHS.map((w) => (mode === 'abs' ? `${w}_abs` : `raw_${w}`))
    const hasAny = keys.some((k) => typeof telemetry[k] === 'number')
    if (!hasAny || !plotRef.current) return

    const ts = typeof telemetry.spectrum_ts === 'number' ? telemetry.spectrum_ts : null
    if (ts != null) {
      if (lastSpectrumTs.current === ts) return
      lastSpectrumTs.current = ts
    } else {
      const fp = keys.map((k) => String(telemetry[k] ?? '')).join('|')
      if (fp === lastFp.current) return
      lastFp.current = fp
    }

    const d = dataRef.current
    d.t.push(Date.now() / 1000)
    keys.forEach((k, i) => {
      const v = telemetry[k]
      d.series[i].push(typeof v === 'number' ? v : (d.series[i].at(-1) ?? 0))
    })
    if (d.t.length > MAX_POINTS) {
      d.t.shift()
      d.series.forEach((s) => s.shift())
    }
    plotRef.current.setData([d.t, ...d.series] as uPlot.AlignedData)
    setPointCount(d.t.length)
  }, [telemetry, mode])

  function toggle(i: number) {
    setVisible((prev) => {
      const next = [...prev]
      next[i] = !next[i]
      return next
    })
  }

  function showFirst(n: number) {
    setVisible(WAVELENGTHS.map((_, i) => i < n))
  }

  function handleResetZoom() {
    if (plotRef.current) resetZoom(plotRef.current)
  }

  return (
    <div className="chart-block">
      <div className="chart-toolbar">
        <button type="button" className="chip-btn" onClick={() => showFirst(6)}>
          6 bandas
        </button>
        <button type="button" className="chip-btn" onClick={() => showFirst(WAVELENGTHS.length)}>
          Todas
        </button>
        <button type="button" className="chip-btn" onClick={() => setVisible(WAVELENGTHS.map(() => false))}>
          Ninguna
        </button>
        <button type="button" className="chip-btn" onClick={handleResetZoom} title="Doble clic en la gráfica también resetea">
          Reset zoom
        </button>
        <span className="muted" style={{ marginLeft: '0.25rem' }}>
          {pointCount === 0 ? 'Sin muestras aún' : `${pointCount} puntos`}
        </span>
        <button type="button" className="chip-btn" style={{ marginLeft: 'auto' }} onClick={() => setPanelOpen((o) => !o)}>
          {panelOpen ? 'Ocultar valores' : 'Mostrar valores'}
        </button>
      </div>
      <p className="chart-hint muted">
        Arrastra para zoom · rueda sobre el eje X · doble clic o «Reset zoom» para volver
      </p>
      <div className="chart-wrap" ref={elRef} />
      <CursorValuesPanel open={panelOpen} time={cursor?.time ?? null} values={cursor?.values ?? null} />
      <div className="band-legend" role="group" aria-label="Bandas del espectro">
        {WAVELENGTHS.map((w, i) => (
          <button
            key={w}
            type="button"
            className={`band-chip ${visible[i] ? 'on' : ''}`}
            style={{ '--band': BAND_COLORS[i % BAND_COLORS.length] } as CSSProperties}
            onClick={() => toggle(i)}
            title={visible[i] ? 'Ocultar' : 'Mostrar'}
          >
            <span className="band-dot" />
            {bandLabel(w)}
          </button>
        ))}
      </div>
    </div>
  )
}
