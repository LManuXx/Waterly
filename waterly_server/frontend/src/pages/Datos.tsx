import { useEffect, useMemo, useState } from 'react'
import { getModelInfo, type StatusResponse, type Telemetry } from '../api/client'

type Props = {
  telemetry: Telemetry
  status: StatusResponse | null
  onToast: (msg: string, ok?: boolean) => void
}

function relativeAgo(ts: number | null | undefined): string | null {
  if (ts == null || !Number.isFinite(ts)) return null
  const sec = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (sec < 5) return 'ahora'
  if (sec < 60) return `hace ${sec}s`
  const m = Math.floor(sec / 60)
  if (m < 60) return `hace ${m} min`
  return `hace ${Math.floor(m / 60)} h`
}

function modelsForN(n: number): string[] {
  const out = ['PLSR', 'Ridge']
  if (n >= 5) out.push('SVR')
  if (n >= 8) out.push('RF')
  return out
}

export function DatosPage({ telemetry, status, onToast }: Props) {
  const [info, setInfo] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    let cancelled = false
    async function refresh() {
      try {
        const m = await getModelInfo()
        if (!cancelled) setInfo(m)
      } catch (e) {
        if (!cancelled) onToast(e instanceof Error ? e.message : String(e), false)
      }
    }
    refresh()
    const id = window.setInterval(refresh, 4000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [onToast])

  const sampleLabels = useMemo(() => {
    const fromTel = telemetry.sample_labels
    if (Array.isArray(fromTel) && fromTel.length) return fromTel.map(Number)
    const fromStatus = status?.model.sample_labels
    if (Array.isArray(fromStatus) && fromStatus.length) return fromStatus.map(Number)
    return [] as number[]
  }, [telemetry.sample_labels, status?.model.sample_labels])

  const counts = useMemo(() => {
    const raw = telemetry.concentration_counts ?? status?.model.concentration_counts
    if (raw && typeof raw === 'object' && Object.keys(raw).length) {
      return Object.entries(raw)
        .map(([k, v]) => ({ conc: Number(k), count: Number(v) }))
        .filter((r) => Number.isFinite(r.conc) && r.count > 0)
        .sort((a, b) => a.conc - b.conc)
    }
    const map = new Map<number, number>()
    for (const y of sampleLabels) {
      map.set(y, (map.get(y) ?? 0) + 1)
    }
    return Array.from(map.entries())
      .map(([conc, count]) => ({ conc, count }))
      .sort((a, b) => a.conc - b.conc)
  }, [telemetry.concentration_counts, status?.model.concentration_counts, sampleLabels])

  const nSamples = Number(telemetry.n_samples ?? status?.model.n_samples ?? sampleLabels.length)
  const totalCounts = counts.reduce((s, r) => s + r.count, 0) || nSamples
  const nDistinct = counts.length
  const yMin = counts.length ? counts[0].conc : null
  const yMax = counts.length ? counts[counts.length - 1].conc : null
  const span = yMin != null && yMax != null ? yMax - yMin : null

  const calibrated = Boolean(telemetry.calibrated || status?.model.has_baseline)
  const opticsMismatch = Boolean(telemetry.optics_mismatch || status?.model.optics_mismatch)
  const trained = Boolean(telemetry.model_ready || status?.model.is_trained || info?.is_trained)
  const mode = String(telemetry.model_mode || status?.model.model_type || info?.model_type || 'PLSR')
  const isConc = mode !== 'DEVIATION'

  const modelsTrained = useMemo(() => {
    const fromInfo = info?.models_trained
    if (Array.isArray(fromInfo) && fromInfo.length) return fromInfo.map(String)
    const fromTel = telemetry.models_trained
    if (Array.isArray(fromTel) && fromTel.length) return fromTel.map(String)
    const ens = info?.ensemble
    if (ens && typeof ens === 'object') return Object.keys(ens as object)
    return [] as string[]
  }, [info, telemetry.models_trained])

  const eligible = modelsForN(nSamples)

  const hasPrediction =
    isConc
      ? telemetry.pred_consensus != null || telemetry.pred_mg_l != null
      : telemetry.pred_deviation != null

  const consensus =
    typeof telemetry.pred_consensus === 'number'
      ? telemetry.pred_consensus
      : typeof telemetry.pred_mg_l === 'number'
        ? telemetry.pred_mg_l
        : null

  const trainYMin = (telemetry.train_y_min ?? status?.model.train_y_min ?? info?.y_min) as
    | number
    | null
    | undefined
  const trainYMax = (telemetry.train_y_max ?? status?.model.train_y_max ?? info?.y_max) as
    | number
    | null
    | undefined

  const modelRows = useMemo(() => {
    const by = telemetry.pred_by_model
    if (!by || typeof by !== 'object') return []
    return (['PLSR', 'Ridge', 'SVR', 'RF'] as const).map((name) => ({
      name,
      value: by[name] ?? null,
    }))
  }, [telemetry.pred_by_model])

  const ensembleRows = useMemo(() => {
    const ens = info?.ensemble
    if (!ens || typeof ens !== 'object') return []
    return Object.values(ens as Record<string, Record<string, unknown>>).map((m) => ({
      name: String(m.name),
      rmsecv: m.rmsecv != null ? Number(m.rmsecv) : null,
      r2: m.r2 != null ? Number(m.r2) : null,
      rpd: m.rpd != null ? Number(m.rpd) : null,
    }))
  }, [info])

  const ago = relativeAgo(typeof telemetry.spectrum_ts === 'number' ? telemetry.spectrum_ts : null)

  return (
    <>
      <p className="science-note muted">
        Vista de sesión: concentraciones en memoria y detalle del último análisis. Operación captura; Modelo
        administra.
      </p>

      <div className="panel">
        <h2>Sesión · entrenamiento</h2>
        <div className="data-stat-grid">
          <div className="data-stat">
            <label>Muestras</label>
            <strong className="mono">{nSamples}</strong>
          </div>
          <div className="data-stat">
            <label>Concentraciones</label>
            <strong className="mono">{nDistinct}</strong>
          </div>
          <div className="data-stat">
            <label>Rango labels</label>
            <strong className="mono">
              {yMin != null && yMax != null ? `${yMin} – ${yMax}` : '—'}
            </strong>
          </div>
          <div className="data-stat">
            <label>Span</label>
            <strong className="mono">{span != null ? `${span.toFixed(2)} mg/L` : '—'}</strong>
          </div>
          <div className="data-stat">
            <label>Blanco</label>
            <strong className={calibrated && !opticsMismatch ? 'ok-text' : ''}>
              {!calibrated ? 'No' : opticsMismatch ? 'Recalibrar' : 'OK'}
            </strong>
          </div>
          <div className="data-stat">
            <label>Modelo</label>
            <strong>{trained ? (isConc ? 'Ensemble listo' : 'Deviation listo') : 'Sin entrenar'}</strong>
          </div>
        </div>

        {nSamples === 0 ? (
          <p className="muted" style={{ marginTop: '0.85rem' }}>
            Aún no hay muestras. En Operación: calibrar blanco → Entrenar muestra con distintas concentraciones.
          </p>
        ) : (
          <>
            <div className="samples-table-wrap" style={{ marginTop: '0.9rem' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Concentración</th>
                    <th>Nº</th>
                    <th>%</th>
                    <th>Distribución</th>
                  </tr>
                </thead>
                <tbody>
                  {counts.map((row) => {
                    const pct = totalCounts > 0 ? Math.round((row.count / totalCounts) * 100) : 0
                    return (
                      <tr key={row.conc}>
                        <td className="mono">{row.conc.toFixed(2)} mg/L</td>
                        <td className="mono">{row.count}</td>
                        <td className="mono">{pct}%</td>
                        <td>
                          <div className="conc-bars" aria-hidden>
                            <div className="conc-bars-fill" style={{ width: `${pct}%` }} />
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            <div className="chip-row" style={{ marginTop: '0.85rem' }}>
              <span className="chip">Con n={nSamples} entrenarían: {eligible.join(' · ')}</span>
              {modelsTrained.length > 0 && (
                <span className="chip chip-accent">Entrenados: {modelsTrained.join(' · ')}</span>
              )}
              {nSamples < 5 && (
                <span className="chip chip-muted">Ideal ≥5 para SVR, ≥8 para RF</span>
              )}
            </div>
          </>
        )}

        {ensembleRows.length > 0 && (
          <>
            <h3 className="subhead">Métricas ensemble</h3>
            <div className="samples-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Modelo</th>
                    <th>RMSECV</th>
                    <th>R²</th>
                    <th>RPD</th>
                  </tr>
                </thead>
                <tbody>
                  {ensembleRows.map((m) => (
                    <tr key={m.name}>
                      <td>{m.name}</td>
                      <td className="mono">{m.rmsecv != null ? m.rmsecv.toFixed(2) : '—'}</td>
                      <td className="mono">{m.r2 != null ? m.r2.toFixed(3) : '—'}</td>
                      <td className="mono">{m.rpd != null ? m.rpd.toFixed(2) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {Array.isArray(info?.outliers) && (info!.outliers as number[]).length > 0 && (
          <p className="alert-banner warn" style={{ marginTop: '0.75rem' }}>
            Outliers de entrenamiento (índices): {(info!.outliers as number[]).join(', ')}
          </p>
        )}
      </div>

      <div className="panel">
        <h2>Último resultado</h2>
        {!hasPrediction ? (
          <p className="muted">
            Aún no hay análisis. Captura muestras en Operación o pulsa Analizar cuando el modelo esté listo.
          </p>
        ) : (
          <>
            <div className="data-stat-grid">
              <div className="data-stat data-stat--wide">
                <label>{isConc ? 'Consenso' : 'Desviación'}</label>
                <strong className="mono result-big">
                  {isConc && consensus != null
                    ? `${consensus.toFixed(2)} mg/L`
                    : telemetry.pred_deviation != null
                      ? `${Number(telemetry.pred_deviation).toFixed(3)} índice`
                      : '—'}
                </strong>
                {telemetry.pred_consensus_label && (
                  <span className="muted tiny">{String(telemetry.pred_consensus_label)}</span>
                )}
              </div>
              <div className="data-stat">
                <label>Acuerdo</label>
                <strong>{String(telemetry.pred_agreement ?? '—')}</strong>
              </div>
              <div className="data-stat">
                <label>Spread</label>
                <strong className="mono">
                  {telemetry.pred_spread != null ? `${Number(telemetry.pred_spread).toFixed(2)}` : '—'}
                </strong>
              </div>
              <div className="data-stat">
                <label>Confianza</label>
                <strong>{String(telemetry.pred_confidence ?? '—')}</strong>
              </div>
              <div className="data-stat">
                <label>En modelo</label>
                <strong>
                  {telemetry.pred_in_model == null ? '—' : telemetry.pred_in_model ? 'Sí' : 'No'}
                </strong>
              </div>
              <div className="data-stat">
                <label>T² / Q</label>
                <strong className="mono">
                  {telemetry.pred_t2 != null ? Number(telemetry.pred_t2).toFixed(2) : '—'}
                  {' / '}
                  {telemetry.pred_q != null ? Number(telemetry.pred_q).toFixed(4) : '—'}
                </strong>
              </div>
              <div className="data-stat">
                <label>Lectura</label>
                <strong>{ago ?? '—'}</strong>
              </div>
              <div className="data-stat">
                <label>QC espectro</label>
                <strong>{String(telemetry.spectrum_qc ?? '—')}</strong>
              </div>
            </div>

            {telemetry.pred_confidence_label && (
              <p className="muted" style={{ marginTop: '0.55rem' }}>
                {String(telemetry.pred_confidence_label)}
              </p>
            )}

            {isConc && modelRows.some((r) => r.value != null) && (
              <>
                <h3 className="subhead">Por modelo</h3>
                <div className="pred-models-row pred-models-row--datos">
                  {modelRows.map((r) => (
                    <span key={r.name} className={r.value == null ? 'muted' : ''}>
                      <strong>{r.name}</strong>{' '}
                      {r.value == null ? '—' : `${Number(r.value).toFixed(2)}`}
                    </span>
                  ))}
                </div>
              </>
            )}

            {isConc && trainYMin != null && trainYMax != null && consensus != null && (
              <p className="muted" style={{ marginTop: '0.65rem' }}>
                Rango entrenamiento: {Number(trainYMin)}–{Number(trainYMax)} mg/L ·{' '}
                {consensus >= trainYMin && consensus <= trainYMax ? 'dentro' : 'fuera'} del rango
              </p>
            )}

            {(telemetry.pred_agreement === 'warn' || telemetry.pred_agreement === 'disagree') && (
              <p
                className={`alert-banner ${
                  telemetry.pred_agreement === 'disagree' ? 'error' : 'warn'
                }`}
              >
                Los modelos no coinciden: limpia cubeta, más muestras o recalibra.
              </p>
            )}

            {telemetry.blank_check_label && (
              <p
                className={`alert-banner ${
                  telemetry.blank_check === 'fail'
                    ? 'error'
                    : telemetry.blank_check === 'warn'
                      ? 'warn'
                      : 'ok'
                }`}
              >
                {String(telemetry.blank_check_label)}
                {telemetry.blank_check_ratio != null
                  ? ` (ratio ${Number(telemetry.blank_check_ratio).toFixed(2)})`
                  : ''}
              </p>
            )}
          </>
        )}
      </div>
    </>
  )
}
