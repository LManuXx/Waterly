import { useEffect, useMemo, useRef, useState } from 'react'
import { postCmd, type Telemetry, type StatusResponse } from '../api/client'
import { SpectrumChart } from '../components/SpectrumChart'

type Props = {
  telemetry: Telemetry
  status: StatusResponse | null
  onToast: (msg: string, ok?: boolean) => void
}

type StepId = 1 | 2 | 3 | 4

const DONE_OK =
  /blanco ok|calibraci[oó]n exitosa|muestra .+guardada|an[aá]lisis finalizado|entrenamiento completado|blanco parece coherente|sesi[oó]n reiniciada|blanco borrado/i
const DONE_ERR = /error|timeout|cancelad|muy distinta|inv[aá]lid/i

function relativeAgo(ts: number | null | undefined): string | null {
  if (ts == null || !Number.isFinite(ts)) return null
  const sec = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (sec < 5) return 'ahora'
  if (sec < 60) return `hace ${sec}s`
  const m = Math.floor(sec / 60)
  if (m < 60) return `hace ${m} min`
  return `hace ${Math.floor(m / 60)} h`
}

export function Operation({ telemetry, status, onToast }: Props) {
  const [samples, setSamples] = useState(5)
  const [label, setLabel] = useState('10.0')
  const [chartMode, setChartMode] = useState<'raw' | 'abs'>('raw')
  const [busy, setBusy] = useState(false)
  const prevMsg = useRef<string>('')

  const calibrated = Boolean(telemetry.calibrated || status?.model.has_baseline)
  const trained = Boolean(telemetry.model_ready || status?.model.is_trained)
  const nSamples = Number(telemetry.n_samples ?? status?.model.n_samples ?? 0)
  const mode = String(telemetry.model_mode || status?.model.model_type || 'PLSR')
  const isPlsr = mode === 'PLSR'
  const opticsMismatch = Boolean(telemetry.optics_mismatch || status?.model.optics_mismatch)
  const state = String(telemetry.system_state ?? status?.state ?? 'IDLE')
  const bursting = ['CALIBRATION', 'TRAINING', 'ANALYSIS', 'BLANK_CHECK'].includes(state)
  const qcLevel = String(telemetry.spectrum_qc ?? 'ok')
  const msg = String(telemetry.prediction_status ?? '—')

  const sampleLabels = useMemo(() => {
    const fromTel = telemetry.sample_labels
    if (Array.isArray(fromTel) && fromTel.length) return fromTel.map(Number)
    const fromStatus = status?.model.sample_labels
    if (Array.isArray(fromStatus) && fromStatus.length) return fromStatus.map(Number)
    return [] as number[]
  }, [telemetry.sample_labels, status?.model.sample_labels])

  const trainYMin = (telemetry.train_y_min ?? status?.model.train_y_min) as number | null | undefined
  const trainYMax = (telemetry.train_y_max ?? status?.model.train_y_max) as number | null | undefined

  const burstCount = Number(telemetry.burst_count ?? 0)
  const burstTarget = Number(telemetry.burst_target ?? 0)
  const showBurst = bursting && burstTarget > 0
  const burstPct = showBurst ? Math.min(100, Math.round((burstCount / burstTarget) * 100)) : 0

  const predDisplay = useMemo(() => {
    if (isPlsr) {
      const v = telemetry.pred_consensus ?? telemetry.pred_mg_l
      if (v == null || typeof v !== 'number') return null
      return { value: v, unit: 'mg/L', title: 'Concentración probable (consenso)' }
    }
    const v = telemetry.pred_deviation
    if (v == null || typeof v !== 'number') return null
    return { value: v, unit: 'índice', title: 'Desviación (no es mg/L)' }
  }, [isPlsr, telemetry.pred_mg_l, telemetry.pred_consensus, telemetry.pred_deviation])

  const modelPredLines = useMemo(() => {
    const by = telemetry.pred_by_model
    if (!by || typeof by !== 'object') return null
    const order = ['PLSR', 'Ridge', 'SVR', 'RF'] as const
    return order.map((name) => {
      const v = by[name]
      if (v == null || typeof v !== 'number') {
        return { name, text: '—', active: false }
      }
      return { name, text: v.toFixed(2), active: true }
    })
  }, [telemetry.pred_by_model])

  const adviceNotes = useMemo(() => {
    const notes: string[] = []
    if (isPlsr) {
      if (nSamples > 0 && nSamples < 5) {
        notes.push('Con <5 muestras solo PLSR+Ridge son fiables. Añade más concentraciones.')
      } else if (nSamples >= 5 && nSamples < 8) {
        notes.push('Bien para SVR. Ideal ≥8 muestras para activar Random Forest.')
      }
      if (samples < 5) {
        notes.push('5 lecturas ~0,5 s suelen bastar; si el QC falla a menudo, sube a 7–8.')
      }
    }
    if (telemetry.pred_agreement === 'warn' || telemetry.pred_agreement === 'disagree') {
      notes.push('Repite Analizar 2–3 veces; si el consenso salta mucho, la cubeta o el blanco fallan.')
    }
    return notes
  }, [isPlsr, nSamples, samples, telemetry.pred_agreement])

  const currentStep: StepId = !calibrated ? 1 : !trained ? (nSamples < (isPlsr ? 3 : 1) ? 2 : 3) : 4

  const nextTip = useMemo(() => {
    if (telemetry.pred_agreement === 'disagree') {
      return 'Los modelos no coinciden: limpia la cubeta, añade muestras o recalibra.'
    }
    if (telemetry.pred_agreement === 'warn' && predDisplay) {
      return 'Discrepancia entre modelos: limpia cubeta o añade más puntos de entrenamiento.'
    }
    if (telemetry.pred_confidence === 'warn' && predDisplay) {
      return 'Muestra rara: limpia la cubeta o recalibra / añade más puntos de entrenamiento.'
    }
    if (!calibrated) return 'Pon agua limpia y pulsa Calibrar blanco.'
    if (opticsMismatch) return 'Cambiaste gain/LED: recalibra o borra el blanco.'
    if (isPlsr && nSamples < 3) {
      return `Captura muestras conocidas (faltan ${3 - nSamples}).`
    }
    if (isPlsr && nSamples >= 3 && nSamples < 5 && !trained) {
      return 'Pulsa Entrenar modelo (con 3–4 muestras: PLSR+Ridge). Ideal ≥5 para SVR.'
    }
    if (!isPlsr && nSamples < 1) return 'Captura al menos 1 muestra y entrena el modelo de alerta.'
    if (nSamples >= (isPlsr ? 3 : 1) && !trained) return 'Pulsa Entrenar modelo.'
    if (trained) return 'Pon la muestra desconocida y Analizar. Repite 2–3 veces si quieres comprobar estabilidad.'
    return 'Sigue la guía de pasos.'
  }, [
    calibrated,
    opticsMismatch,
    isPlsr,
    nSamples,
    trained,
    telemetry.pred_confidence,
    telemetry.pred_agreement,
    predDisplay,
  ])

  const resultHint = useMemo(() => {
    if (!predDisplay) return null
    const parts: string[] = []
    const ago = relativeAgo(
      typeof telemetry.spectrum_ts === 'number' ? telemetry.spectrum_ts : null,
    )
    if (ago) parts.push(`Última lectura: ${ago}`)

    if (isPlsr && telemetry.pred_consensus_label) {
      parts.push(`Consenso: ${String(telemetry.pred_consensus_label)}.`)
    }
    if (isPlsr && telemetry.pred_spread != null) {
      parts.push(`Dispersión entre modelos: ${Number(telemetry.pred_spread).toFixed(2)} mg/L.`)
    }

    if (isPlsr && trainYMin != null && trainYMax != null && typeof predDisplay.value === 'number') {
      const v = predDisplay.value
      if (v >= trainYMin && v <= trainYMax) {
        parts.push(`Dentro del rango de entrenamiento (${trainYMin}–${trainYMax} mg/L).`)
      } else {
        parts.push(
          `Fuera del rango de entrenamiento (${trainYMin}–${trainYMax} mg/L). Añade muestras en ese rango o desconfía del número.`,
        )
      }
    }
    if (telemetry.pred_agreement === 'disagree') {
      parts.push('Los modelos discrepan mucho: no uses un solo número; limpia cubeta o reentrena.')
    } else if (telemetry.pred_confidence === 'warn') {
      parts.push('Confianza baja: limpia la cubeta, recalibra el blanco o amplía el entrenamiento.')
    }
    return parts.length ? parts : null
  }, [
    predDisplay,
    isPlsr,
    trainYMin,
    trainYMax,
    telemetry.spectrum_ts,
    telemetry.pred_confidence,
    telemetry.pred_agreement,
    telemetry.pred_consensus_label,
    telemetry.pred_spread,
  ])

  const modelLine = `${isPlsr ? 'Concentración (ensemble)' : 'Alerta (Deviation)'}${
    trained ? ` · ${nSamples} muestras` : ' · sin entrenar'
  }`
  useEffect(() => {
    const clean = msg.replace(/^\[PROGRESS\]\s*/i, '').trim()
    if (!clean || clean === prevMsg.current) return
    const prev = prevMsg.current
    prevMsg.current = clean

    if (/\(\d+\/\d+\)/.test(clean) || clean.startsWith('Entrenando modelo') || clean === 'Comprobando…')
      return
    if (!prev) return

    if (DONE_OK.test(clean)) {
      onToast(clean, true)
    } else if (DONE_ERR.test(clean) && state === 'IDLE') {
      if (/error|timeout|cancelad|muy distinta/i.test(clean)) onToast(clean, false)
    }
  }, [msg, onToast, state])

  async function run(method: string, body?: Record<string, unknown>, quiet = false) {
    setBusy(true)
    try {
      await postCmd(method, body)
      if (!quiet) {
        const instant: Record<string, string> = {
          setIdle: 'En reposo',
          startFreeMeasure: 'Monitor continuo activo',
          setModePLSR: 'Modo concentración mg/L',
          setModeDeviation: 'Modo alerta',
          trainModel: 'Entrenamiento del modelo iniciado…',
          clearBaseline: 'Blanco borrado',
          resetModel: 'Modelo borrado',
          resetAll: 'Sesión reiniciada',
          removeTrainingSample: 'Última muestra eliminada',
        }
        if (instant[method]) onToast(instant[method], true)
      }
    } catch (e) {
      onToast(e instanceof Error ? e.message : String(e), false)
    } finally {
      setBusy(false)
    }
  }

  function disableReason(...checks: (string | false | null | undefined)[]) {
    return checks.find((c) => typeof c === 'string') as string | undefined
  }

  const reasonCalibrate = disableReason(busy && 'Espera…', bursting && 'Operación en curso')
  const reasonCheckBlank = disableReason(
    busy && 'Espera…',
    bursting && 'Operación en curso',
    !calibrated && 'Primero calibra el blanco',
  )
  const reasonTrainSample = disableReason(
    busy && 'Espera…',
    bursting && 'Operación en curso',
    !calibrated && 'Primero calibra con agua limpia',
    opticsMismatch && 'Óptica distinta al blanco — recalibra',
    isPlsr && (label.trim() === '' || Number.isNaN(Number(label))) && 'Indica concentración mg/L numérica',
    isPlsr && Number(label) < 0 && 'mg/L no puede ser negativo',
  )
  const reasonTrainModel = disableReason(
    busy && 'Espera…',
    bursting && 'Operación en curso',
    nSamples < 1 && 'Aún no hay muestras de entrenamiento',
    isPlsr && nSamples < 3 && `PLSR necesita ≥3 muestras (tienes ${nSamples})`,
  )
  const reasonAnalyze = disableReason(
    busy && 'Espera…',
    bursting && 'Operación en curso',
    !calibrated && 'Falta calibrar el blanco',
    !trained && 'Falta entrenar el modelo',
    opticsMismatch && 'Óptica distinta al blanco — recalibra',
  )

  const stateLabel: Record<string, string> = {
    IDLE: 'Reposo',
    CALIBRATION: 'Calibrando blanco',
    TRAINING: 'Capturando muestra',
    ANALYSIS: 'Analizando',
    FREE_MEASURE: 'Monitor continuo',
    BLANK_CHECK: 'Comprobando blanco',
  }

  return (
    <>
      <p className="science-note muted">
        Estimación por espectro visible/NIR; no mide nitrato a 220 nm directamente.
      </p>

      <div className="next-tip" role="status">
        <strong>Qué hacer ahora</strong>
        <span>{nextTip}</span>
      </div>

      <div className="panel">
        <h2>Guía rápida</h2>
        <ol className="flow-steps">
          <li className={currentStep === 1 ? 'active' : calibrated ? 'done' : ''}>
            <strong>1. Blanco</strong>
            <span>Pon agua limpia en la cubeta → Calibrar</span>
          </li>
          <li className={currentStep === 2 ? 'active' : nSamples >= (isPlsr ? 3 : 1) ? 'done' : ''}>
            <strong>2. Muestras conocidas</strong>
            <span>Indica mg/L → Entrenar muestra (repite varias concentraciones)</span>
          </li>
          <li className={currentStep === 3 ? 'active' : trained ? 'done' : ''}>
            <strong>3. Entrenar modelo</strong>
            <span>
              {isPlsr
                ? '≥3 muestras → Entrenar (ensemble PLSR+Ridge; SVR≥5, RF≥8)'
                : 'Al menos 1 muestra → Entrenar modelo'}
            </span>
          </li>
          <li className={currentStep === 4 ? 'active' : ''}>
            <strong>4. Medir</strong>
            <span>Pon la muestra desconocida → Analizar</span>
          </li>
        </ol>
      </div>

      <div className="panel">
        <h2>Resultado</h2>
        <div className={`pred-hero ${telemetry.pred_confidence === 'warn' ? 'warn' : ''} ${qcLevel === 'fail' ? 'fail' : ''}`}>
          <div className="pred-hero-label">{predDisplay?.title ?? 'Sin predicción aún'}</div>
          <div className="pred-hero-value">
            {predDisplay ? (
              <>
                <span className="mono">{predDisplay.value.toFixed(2)}</span>
                <span className="pred-hero-unit">{predDisplay.unit}</span>
              </>
            ) : (
              <span className="muted">—</span>
            )}
          </div>
          {telemetry.pred_confidence_label && (
            <div className="pred-hero-conf">{String(telemetry.pred_confidence_label)}</div>
          )}
        </div>

        {isPlsr && modelPredLines && predDisplay && (
          <div className="pred-models">
            <div className="pred-models-label">Por modelo</div>
            <div className="pred-models-row">
              {modelPredLines.map((m) => (
                <span key={m.name} className={m.active ? '' : 'muted'}>
                  <strong>{m.name}</strong> {m.text}
                </span>
              ))}
            </div>
            {telemetry.pred_consensus != null && (
              <p className="pred-consensus">
                Probable:{' '}
                <strong className="mono">{Number(telemetry.pred_consensus).toFixed(2)} mg/L</strong>
                {telemetry.pred_consensus_label ? ` (${String(telemetry.pred_consensus_label)})` : ''}
              </p>
            )}
            {(telemetry.pred_agreement === 'warn' || telemetry.pred_agreement === 'disagree') && (
              <p className={`alert-banner ${telemetry.pred_agreement === 'disagree' ? 'error' : 'warn'}`}>
                Los modelos no coinciden: limpia cubeta, más muestras de entrenamiento o recalibra.
              </p>
            )}
          </div>
        )}

        {resultHint && (
          <div className="result-hint">
            {resultHint.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
        )}

        {adviceNotes.length > 0 && (
          <ul className="advice-notes">
            {adviceNotes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}

        {telemetry.blank_check_label && (
          <p
            className={`alert-banner ${
              telemetry.blank_check === 'fail' ? 'error' : telemetry.blank_check === 'warn' ? 'warn' : 'ok'
            }`}
          >
            {String(telemetry.blank_check_label)}
            {telemetry.blank_check_ratio != null ? ` (ratio ${Number(telemetry.blank_check_ratio).toFixed(2)})` : ''}
          </p>
        )}

        {showBurst && (
          <div className="burst-progress" aria-live="polite">
            <div className="burst-progress-head">
              <strong>Lectura {burstCount}/{burstTarget}</strong>
              <span className="muted">para al llegar a {burstTarget} · ~0,5 s entre lecturas</span>
            </div>
            <div className="burst-progress-track">
              <div className="burst-progress-fill" style={{ width: `${burstPct}%` }} />
            </div>
          </div>
        )}

        <div className="status-grid" style={{ marginTop: '0.85rem' }}>
          <div className="status-item">
            <label>Estado</label>
            <strong>{stateLabel[state] ?? state}</strong>
          </div>
          <div className="status-item status-item--wide">
            <label>Mensaje</label>
            <strong className="status-msg" title={msg}>{msg}</strong>
          </div>
          <div className="status-item">
            <label>Blanco</label>
            <strong className={calibrated ? 'ok-text' : ''}>
              {calibrated ? (opticsMismatch ? 'Recalibrar' : 'OK') : 'No'}
            </strong>
          </div>
          <div className="status-item">
            <label>Calidad espectro</label>
            <strong title={String(telemetry.spectrum_qc_label ?? '')}>
              {qcLevel === 'fail' ? 'Inválido' : qcLevel === 'warn' ? 'Aviso' : 'OK'}
            </strong>
          </div>
          <div className="status-item status-item--wide">
            <label>Modelo</label>
            <strong title={modelLine}>{modelLine}</strong>
          </div>
        </div>
        {opticsMismatch && (
          <p className="alert-banner warn">
            Gain / integración / LED distintos a cuando calibraste. Recalibra con agua limpia antes de medir.
          </p>
        )}
        {qcLevel === 'fail' && (
          <p className="alert-banner error">{String(telemetry.spectrum_qc_label ?? 'Espectro inválido')}</p>
        )}
      </div>

      <div className="panel">
        <h2>Parámetros</h2>
        <div className="field-row">
          <div className="field">
            <label>Lecturas a promediar</label>
            <input
              type="number"
              min={1}
              max={50}
              value={samples}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10)
                if (Number.isNaN(n)) setSamples(5)
                else setSamples(Math.max(1, Math.min(50, n)))
              }}
            />
          </div>
          <div className="field">
            <label>Concentración conocida (mg/L)</label>
            <input value={label} onChange={(e) => setLabel(e.target.value)} inputMode="decimal" />
          </div>
          <div className="field">
            <label>Gráfico</label>
            <select value={chartMode} onChange={(e) => setChartMode(e.target.value as 'raw' | 'abs')}>
              <option value="raw">Señal del sensor</option>
              <option value="abs">Absorbancia</option>
            </select>
          </div>
        </div>
        {sampleLabels.length > 0 && (
          <p className="muted" style={{ marginTop: '0.65rem' }}>
            Muestras en memoria: {sampleLabels.map((v) => v.toFixed(1)).join(', ')} mg/L
          </p>
        )}
      </div>

      <div className="panel">
        <h2>Operación</h2>
        <div className="btn-groups">
          <div className="btn-group">
            <span className="btn-group-label">Control</span>
            <div className="btn-row">
              <button type="button" className="action" disabled={busy} onClick={() => run('setIdle')}>
                Reposo
              </button>
              <button type="button" className="action" disabled={busy} onClick={() => run('startFreeMeasure')}>
                Monitor continuo
              </button>
            </div>
          </div>
          <div className="btn-group">
            <span className="btn-group-label">Blanco</span>
            <div className="btn-row">
              <button
                type="button"
                className="action primary"
                disabled={Boolean(reasonCalibrate)}
                title={reasonCalibrate}
                onClick={() => run('calibrate', { samples }, true)}
              >
                Calibrar blanco
              </button>
              <button
                type="button"
                className="action"
                disabled={Boolean(reasonCheckBlank)}
                title={reasonCheckBlank}
                onClick={() => run('checkBlank', {}, true)}
              >
                Comprobar blanco
              </button>
              <button
                type="button"
                className="action danger"
                disabled={busy || bursting || !calibrated}
                onClick={() => {
                  if (
                    window.confirm(
                      '¿Borrar el agua de referencia (blanco)?\n\nHabrá que calibrar otra vez antes de medir. Las muestras y el modelo se conservan.',
                    )
                  ) {
                    run('clearBaseline')
                  }
                }}
              >
                Borrar blanco
              </button>
            </div>
          </div>
          <div className="btn-group">
            <span className="btn-group-label">Medición</span>
            <div className="btn-row">
              <button
                type="button"
                className="action primary"
                disabled={Boolean(reasonAnalyze)}
                title={reasonAnalyze}
                onClick={() => run('startAnalysis', { samples }, true)}
              >
                Analizar
              </button>
            </div>
            {reasonAnalyze && <p className="btn-hint muted">{reasonAnalyze}</p>}
          </div>
          <div className="btn-group">
            <span className="btn-group-label">Entrenamiento</span>
            <div className="btn-row">
              <button
                type="button"
                className="action primary"
                disabled={Boolean(reasonTrainSample)}
                title={reasonTrainSample}
                onClick={() => run('startTraining', { label, samples }, true)}
              >
                Entrenar muestra
              </button>
              <button
                type="button"
                className="action primary"
                disabled={Boolean(reasonTrainModel)}
                title={reasonTrainModel}
                onClick={() => run('trainModel')}
              >
                Entrenar modelo
              </button>
              <button
                type="button"
                className="action"
                disabled={busy || bursting || nSamples < 1}
                onClick={() => {
                  if (window.confirm('¿Quitar la última muestra de entrenamiento? Habrá que reentrenar el modelo.')) {
                    run('removeTrainingSample', {})
                  }
                }}
              >
                Deshacer última muestra
              </button>
            </div>
            {(reasonTrainSample || reasonTrainModel) && (
              <p className="btn-hint muted">{reasonTrainSample || reasonTrainModel}</p>
            )}
          </div>
          <div className="btn-group">
            <span className="btn-group-label">Modo de resultado</span>
            <div className="btn-row">
              <button
                type="button"
                className={`action ${isPlsr ? 'primary' : ''}`}
                disabled={busy}
                onClick={() => run('setModePLSR')}
              >
                Concentración mg/L
              </button>
              <button
                type="button"
                className={`action ${!isPlsr ? 'primary' : ''}`}
                disabled={busy}
                title="Índice cualitativo — no es concentración"
                onClick={() => run('setModeDeviation')}
              >
                Alerta (no mg/L)
              </button>
              <button
                type="button"
                className="action danger"
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(
                      '¿Borrar todas las muestras de entrenamiento y el modelo?\n\nTendrás que volver a entrenar (el blanco se conserva).',
                    )
                  ) {
                    run('resetModel')
                  }
                }}
              >
                Borrar modelo
              </button>
              <button
                type="button"
                className="action danger"
                disabled={busy || bursting}
                onClick={() => {
                  if (
                    window.confirm(
                      '¿Empezar de cero?\n\nSe borra el blanco, todas las muestras y el modelo. Ideal al cambiar de fertilizante o de día.',
                    )
                  ) {
                    run('resetAll')
                  }
                }}
              >
                Empezar de cero
              </button>
            </div>
            <p className="btn-hint muted">
              {isPlsr
                ? 'Modo concentración: ensemble PLSR+Ridge (+SVR/RF si hay más muestras). El hero muestra el consenso.'
                : 'Modo alerta: distancia espectral — útil como alarma, no como concentración.'}
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>Espectro en vivo</h2>
        <p className="muted">Pulsa una banda para mostrar u ocultar. Arrastra o usa la rueda para zoom.</p>
        <SpectrumChart telemetry={telemetry} mode={chartMode} />
      </div>
    </>
  )
}
