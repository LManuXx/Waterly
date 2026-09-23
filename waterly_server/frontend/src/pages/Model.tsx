import { useEffect, useState } from 'react'
import { getModelInfo, postCmd, uploadModel } from '../api/client'

type Props = { onToast: (msg: string, ok?: boolean) => void }

type SampleRow = { index: number; label_mg_l: number }

export function ModelPage({ onToast }: Props) {
  const [info, setInfo] = useState<Record<string, unknown> | null>(null)
  const [showRaw, setShowRaw] = useState(false)

  async function refresh() {
    try {
      setInfo(await getModelInfo())
    } catch (e) {
      onToast(e instanceof Error ? e.message : String(e), false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const trained = Boolean(info?.is_trained)
  const mode = String(info?.model_type ?? 'PLSR')
  const nSamples = Number(info?.n_samples ?? 0)
  const samples = (info?.samples as SampleRow[] | undefined) ?? []
  const hasBaseline = Boolean(info?.has_baseline)
  const readyLabel = !hasBaseline
    ? 'Falta blanco'
    : !trained
      ? nSamples < (mode === 'PLSR' ? 3 : 1)
        ? 'Datos insuficientes'
        : 'Listo para entrenar'
      : 'Modelo listo'

  async function removeSample(index: number) {
    if (!window.confirm(`¿Eliminar muestra #${index}? Habrá que reentrenar.`)) return
    try {
      await postCmd('removeTrainingSample', { index })
      onToast('Muestra eliminada', true)
      refresh()
    } catch (e) {
      onToast(e instanceof Error ? e.message : String(e), false)
    }
  }

  return (
    <div className="panel">
      <h2>Modelo ML</h2>

      <div className={`model-semaforo ${trained ? 'ok' : nSamples > 0 ? 'warn' : 'idle'}`}>
        <strong>{readyLabel}</strong>
        <span className="muted">
          {mode === 'PLSR' || mode === 'ENSEMBLE' || Boolean(info?.ensemble)
            ? 'Concentración (ensemble)'
            : 'Alerta (Deviation)'}{' '}
          · {nSamples} muestras · Blanco: {hasBaseline ? 'sí' : 'no'}
          {Array.isArray(info?.models_trained) && (info!.models_trained as string[]).length > 0
            ? ` · [${(info!.models_trained as string[]).join('+')}]`
            : ''}
        </span>
      </div>

      {trained && (mode === 'PLSR' || mode === 'ENSEMBLE' || Boolean(info?.ensemble)) && (
        <div className="metrics-grid">
          <div className="metric-card">
            <label>R² PLSR (train)</label>
            <strong className="mono">{info?.r2_train != null ? Number(info.r2_train).toFixed(3) : '—'}</strong>
          </div>
          <div className="metric-card">
            <label>RMSECV PLSR</label>
            <strong className="mono">
              {info?.rmsecv != null ? `${Number(info.rmsecv).toFixed(2)} mg/L` : '—'}
            </strong>
          </div>
          <div className="metric-card">
            <label>RPD PLSR</label>
            <strong className="mono">{info?.rpd != null ? Number(info.rpd).toFixed(2) : '—'}</strong>
          </div>
          <div className="metric-card">
            <label>Componentes</label>
            <strong className="mono">{info?.n_components != null ? String(info.n_components) : '—'}</strong>
          </div>
          <div className="metric-card">
            <label>Rango labels</label>
            <strong className="mono">
              {info?.y_min != null && info?.y_max != null
                ? `${Number(info.y_min).toFixed(1)} – ${Number(info.y_max).toFixed(1)} mg/L`
                : '—'}
            </strong>
          </div>
        </div>
      )}

      {trained && info?.ensemble && typeof info.ensemble === 'object' && (
        <>
          <h3 style={{ marginTop: '1rem', fontSize: '1rem' }}>Ensemble (RMSECV / RPD)</h3>
          <div className="samples-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Modelo</th>
                  <th>RMSECV</th>
                  <th>R²</th>
                  <th>RPD</th>
                  <th>n</th>
                </tr>
              </thead>
              <tbody>
                {Object.values(info.ensemble as Record<string, Record<string, unknown>>).map((m) => (
                  <tr key={String(m.name)}>
                    <td>{String(m.name)}</td>
                    <td className="mono">{m.rmsecv != null ? Number(m.rmsecv).toFixed(2) : '—'}</td>
                    <td className="mono">{m.r2 != null ? Number(m.r2).toFixed(3) : '—'}</td>
                    <td className="mono">{m.rpd != null ? Number(m.rpd).toFixed(2) : '—'}</td>
                    <td className="mono">{m.n_samples_used != null ? String(m.n_samples_used) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {nSamples < 5 && (
            <p className="muted" style={{ marginTop: '0.5rem' }}>
              Consejo: con &lt;5 muestras solo PLSR+Ridge. Añade concentraciones para SVR (≥5) y RF (≥8).
            </p>
          )}
          {nSamples >= 5 && nSamples < 8 && (
            <p className="muted" style={{ marginTop: '0.5rem' }}>
              Consejo: SVR activo. Con ≥8 muestras también entrenará Random Forest.
            </p>
          )}
          {typeof info.rpd === 'number' && info.rpd < 2 && nSamples >= 8 && (
            <p className="alert-banner warn">
              RPD PLSR &lt; 2: el ajuste es flojo. Añade puntos en el rango que te importa o estabiliza el blanco.
            </p>
          )}
        </>
      )}

      {trained && mode === 'DEVIATION' && (
        <p className="muted">
          Modelo de desviación sobre {(info?.bands_used as string[] | undefined)?.length ?? '—'} bandas UV/IR.
          El resultado es un índice, no mg/L (no entra en el consenso de concentración).
        </p>
      )}

      {Array.isArray(info?.outliers) && (info!.outliers as number[]).length > 0 && (
        <p className="alert-banner warn">
          Outliers en entrenamiento: índices {(info!.outliers as number[]).join(', ')}
        </p>
      )}

      <h3 style={{ marginTop: '1.25rem', fontSize: '1rem' }}>Muestras en memoria</h3>
      {samples.length === 0 ? (
        <p className="muted">Ninguna. En Operación: calibrar → entrenar muestra con label mg/L.</p>
      ) : (
        <div className="samples-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Label (mg/L)</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {samples.map((s) => (
                <tr key={s.index}>
                  <td className="mono">{s.index}</td>
                  <td className="mono">{s.label_mg_l}</td>
                  <td>
                    <button type="button" className="chip-btn" onClick={() => removeSample(s.index)}>
                      Borrar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="btn-row" style={{ marginTop: '0.85rem' }}>
        <button type="button" className="action" onClick={refresh}>
          Actualizar
        </button>
        <a className="action" href="/api/model/download">
          Descargar .pkl
        </a>
        <label className="action">
          Subir .pkl
          <input
            type="file"
            accept=".pkl"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0]
              if (!f) return
              try {
                await uploadModel(f)
                onToast('Modelo cargado', true)
                refresh()
              } catch (err) {
                onToast(err instanceof Error ? err.message : String(err), false)
              }
            }}
          />
        </label>
        <button type="button" className="chip-btn" onClick={() => setShowRaw((v) => !v)}>
          {showRaw ? 'Ocultar JSON' : 'Ver JSON'}
        </button>
      </div>

      {showRaw && (
        <pre
          className="mono"
          style={{
            whiteSpace: 'pre-wrap',
            fontSize: '0.8rem',
            background: 'var(--bg)',
            padding: '0.75rem',
            borderRadius: 8,
            marginTop: '0.75rem',
          }}
        >
          {info ? JSON.stringify(info, null, 2) : '—'}
        </pre>
      )}
    </div>
  )
}
