import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { getHistory, getPredictionHistory, WAVELENGTHS, type PredictionRow } from '../api/client'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { CursorValuesPanel, parseBandKey, type CursorBandRow } from '../components/CursorValuesPanel'
import { bindZoomInteractions, resetZoom, zoomCursorOptions, zoomSelectOptions } from '../lib/uplotZoom'

const BAND_COLORS = [
  '#3d9a8b', '#5aaf7a', '#c4a35a', '#7aa2c4', '#c45c5c', '#9b7bc4',
  '#4db6ac', '#81c784', '#ffb74d', '#64b5f6', '#e57373', '#ba68c8',
  '#26a69a', '#66bb6a', '#ffa726', '#42a5f5', '#ef5350', '#ab47bc',
]

type Props = { onToast: (msg: string, ok?: boolean) => void }
type ViewMode = 'spectrum' | 'results'

function bandLabel(key: string) {
  const { letter, nm } = parseBandKey(key)
  return `${letter} ${nm}`
}

function formatClock(tsSec: number) {
  const d = new Date(tsSec * 1000)
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDateTime(tsSec: number) {
  const d = new Date(tsSec * 1000)
  return d.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function HistoryPage({ onToast }: Props) {
  const [view, setView] = useState<ViewMode>('results')
  const [minutes, setMinutes] = useState(60)
  const [kind, setKind] = useState<'raw' | 'abs'>('raw')
  const [loading, setLoading] = useState(false)
  const [nPoints, setNPoints] = useState(0)
  const [reloadTick, setReloadTick] = useState(0)
  const [panelOpen, setPanelOpen] = useState(true)
  const [visible, setVisible] = useState(() => WAVELENGTHS.map((_, i) => i < 6))
  const [cursor, setCursor] = useState<{ time: string; values: CursorBandRow[] } | null>(null)
  const [predRows, setPredRows] = useState<PredictionRow[]>([])

  const elRef = useRef<HTMLDivElement>(null)
  const plotRef = useRef<uPlot | null>(null)
  const visibleRef = useRef(visible)
  visibleRef.current = visible

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
    if (view !== 'results') return
    let cancelled = false
    setLoading(true)
    ;(async () => {
      try {
        const data = await getPredictionHistory(minutes)
        if (!cancelled) {
          setPredRows(data.rows || [])
          setNPoints(data.n_points || 0)
        }
      } catch (e) {
        if (!cancelled) onToast(e instanceof Error ? e.message : String(e), false)
        setPredRows([])
        setNPoints(0)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [view, minutes, reloadTick, onToast])

  useEffect(() => {
    if (view !== 'spectrum') return
    const el = elRef.current
    if (!el) return
    let cancelled = false
    let unbindZoom: (() => void) | null = null
    setLoading(true)
    setCursor(null)

    ;(async () => {
      try {
        const data = await getHistory(minutes, kind)
        if (cancelled || !elRef.current) return

        unbindZoom?.()
        unbindZoom = null
        plotRef.current?.destroy()
        plotRef.current = null
        elRef.current.replaceChildren()

        const keys = WAVELENGTHS.map((w) => (kind === 'abs' ? `${w}_abs` : `raw_${w}`))
        const first = keys.find((k) => data.series[k]?.t?.length)
        const t: number[] = first ? [...data.series[first].t] : []
        const series = keys.map((k) => {
          const s = data.series[k]
          if (!s || !t.length) return t.map(() => null as unknown as number)
          const map = new Map(s.t.map((x: number, i: number) => [x, s.v[i]]))
          return t.map((ts) => {
            const v = map.get(ts)
            return v == null ? (null as unknown as number) : v
          })
        })

        setNPoints(typeof data.n_points === 'number' ? data.n_points : t.length)

        const opts: uPlot.Options = {
          width: Math.max(elRef.current.clientWidth, 120),
          height: Math.max(elRef.current.clientHeight, 160),
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
            ...keys.map((k, i) => ({
              label: bandLabel(k),
              stroke: BAND_COLORS[i % BAND_COLORS.length],
              width: 1.5,
              show: visibleRef.current[i],
              points: { show: t.length < 80, size: 3 },
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
        plotRef.current = new uPlot(opts, [t, ...series] as uPlot.AlignedData, elRef.current)
      } catch (e) {
        if (!cancelled) onToast(e instanceof Error ? e.message : String(e), false)
        setNPoints(0)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    const ro = new ResizeObserver(() => {
      if (!plotRef.current || !elRef.current) return
      const w = elRef.current.clientWidth
      const h = elRef.current.clientHeight
      if (w > 0 && h > 0) plotRef.current.setSize({ width: w, height: h })
    })
    ro.observe(el)

    return () => {
      cancelled = true
      unbindZoom?.()
      ro.disconnect()
      plotRef.current?.destroy()
      plotRef.current = null
    }
  }, [view, minutes, kind, onToast, reloadTick, onCursor])

  useEffect(() => {
    const plot = plotRef.current
    if (!plot) return
    visible.forEach((show, i) => {
      plot.setSeries(i + 1, { show })
    })
  }, [visible])

  function handleResetZoom() {
    if (plotRef.current) resetZoom(plotRef.current)
  }

  return (
    <div className="panel">
      <h2>Histórico</h2>
      <div className="field-row">
        <div className="field">
          <label>Vista</label>
          <select value={view} onChange={(e) => setView(e.target.value as ViewMode)}>
            <option value="results">Resultados (mg/L)</option>
            <option value="spectrum">Espectro</option>
          </select>
        </div>
        <div className="field">
          <label>Minutos</label>
          <input type="number" min={5} max={1440} value={minutes} onChange={(e) => setMinutes(+e.target.value)} />
        </div>
        {view === 'spectrum' && (
          <div className="field">
            <label>Tipo</label>
            <select value={kind} onChange={(e) => setKind(e.target.value as 'raw' | 'abs')}>
              <option value="raw">Señal del sensor</option>
              <option value="abs">Absorbancia</option>
            </select>
          </div>
        )}
        <div className="field" style={{ alignSelf: 'end' }}>
          <button type="button" className="action" onClick={() => setReloadTick((n) => n + 1)} disabled={loading}>
            {loading ? 'Cargando…' : 'Actualizar'}
          </button>
        </div>
        {view === 'results' && (
          <div className="field" style={{ alignSelf: 'end' }}>
            <a className="action" href={`/api/history/predictions.csv?minutes=${minutes}`}>
              Export CSV
            </a>
          </div>
        )}
      </div>

      {view === 'results' ? (
        <>
          <p className="muted">
            {loading
              ? 'Consultando Influx…'
              : nPoints === 0
                ? 'Sin resultados aún. En Operación: calibra, entrena, Analizar (o Monitor continuo) y vuelve aquí.'
                : `${nPoints} resultados en los últimos ${minutes} min`}
          </p>
          {nPoints === 0 && !loading && (
            <p className="alert-banner warn">
              Cómo generar datos: Operación → Calibrar blanco → Entrenar → Analizar. Los resultados aparecen aquí
              automáticamente.
            </p>
          )}
          {predRows.length > 0 && (
            <div className="samples-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Hora</th>
                    <th>Valor</th>
                    <th>Unidad</th>
                    <th>Modo</th>
                    <th>Origen</th>
                    <th>Confianza</th>
                    <th>QC</th>
                  </tr>
                </thead>
                <tbody>
                  {predRows.map((r, i) => {
                    const isConc = r.kind === 'concentration' || r.pred_mg_l != null
                    const val = isConc ? r.pred_mg_l ?? r.value : r.pred_deviation ?? r.value
                    return (
                      <tr key={`${r.t}-${i}`}>
                        <td className="mono">{r.t != null ? formatDateTime(r.t) : '—'}</td>
                        <td className="mono">{val != null ? Number(val).toFixed(2) : '—'}</td>
                        <td>{isConc ? 'mg/L' : 'índice'}</td>
                        <td>{r.mode ?? '—'}</td>
                        <td>{r.source ?? '—'}</td>
                        <td>{r.confidence ?? '—'}</td>
                        <td>{r.qc ?? '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="muted">
            {loading
              ? 'Consultando Influx…'
              : nPoints === 0
                ? 'Sin datos espectrales en este rango.'
                : `${nPoints} puntos · pasa el ratón para ver valores`}
          </p>
          {nPoints === 0 && !loading && (
            <p className="alert-banner warn">
              En Operación → <strong>Monitor continuo</strong> unos segundos → vuelve aquí y pulsa{' '}
              <strong>Actualizar</strong>.
            </p>
          )}
          <div className="chart-block">
            <div className="chart-toolbar">
              <button type="button" className="chip-btn" onClick={() => setVisible(WAVELENGTHS.map((_, i) => i < 6))}>
                6 bandas
              </button>
              <button type="button" className="chip-btn" onClick={() => setVisible(WAVELENGTHS.map(() => true))}>
                Todas
              </button>
              <button type="button" className="chip-btn" onClick={handleResetZoom}>
                Reset zoom
              </button>
              <button
                type="button"
                className="chip-btn"
                style={{ marginLeft: 'auto' }}
                onClick={() => setPanelOpen((o) => !o)}
              >
                {panelOpen ? 'Ocultar valores' : 'Mostrar valores'}
              </button>
            </div>
            <p className="chart-hint muted">Arrastra para zoom · rueda · doble clic para reset</p>
            <div className="chart-wrap chart-wrap--tall" ref={elRef} />
            <CursorValuesPanel
              open={panelOpen}
              time={cursor?.time ?? null}
              values={cursor?.values ?? null}
              hint="Mueve el cursor sobre la serie para ver cada banda a esa hora."
            />
            <div className="band-legend">
              {WAVELENGTHS.map((w, i) => (
                <button
                  key={w}
                  type="button"
                  className={`band-chip ${visible[i] ? 'on' : ''}`}
                  style={{ '--band': BAND_COLORS[i % BAND_COLORS.length] } as CSSProperties}
                  onClick={() =>
                    setVisible((prev) => {
                      const next = [...prev]
                      next[i] = !next[i]
                      return next
                    })
                  }
                >
                  <span className="band-dot" />
                  {bandLabel(w)}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
