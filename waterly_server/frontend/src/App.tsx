import { useCallback, useEffect, useState } from 'react'
import { connectTelemetry, getStatus, type StatusResponse, type Telemetry } from './api/client'
import { Operation } from './pages/Operation'
import { DatosPage } from './pages/Datos'
import { ConfigPage } from './pages/Config'
import { FirmwarePage } from './pages/Firmware'
import { ModelPage } from './pages/Model'
import { HistoryPage } from './pages/History'

type Tab = 'operation' | 'datos' | 'config' | 'firmware' | 'model' | 'history'

export default function App() {
  const [tab, setTab] = useState<Tab>('operation')
  const [telemetry, setTelemetry] = useState<Telemetry>({})
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null)

  const onToast = useCallback((msg: string, ok = true) => {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 4000)
  }, [])

  useEffect(() => {
    getStatus()
      .then((s) => {
        setStatus(s)
        setTelemetry(s.telemetry || {})
      })
      .catch((e) => onToast(String(e), false))
    return connectTelemetry((t) => {
      setTelemetry(t)
      setStatus((prev) =>
        prev
          ? {
              ...prev,
              state: String(t.system_state ?? prev.state),
              telemetry: t,
              model: {
                ...prev.model,
                is_trained: t.model_ready != null ? Boolean(t.model_ready) : prev.model.is_trained,
                model_type: String(t.model_mode ?? prev.model.model_type),
                has_baseline: t.calibrated != null ? Boolean(t.calibrated) : prev.model.has_baseline,
                n_samples: typeof t.n_samples === 'number' ? t.n_samples : prev.model.n_samples,
                sample_labels: Array.isArray(t.sample_labels) ? t.sample_labels : prev.model.sample_labels,
                concentration_counts:
                  t.concentration_counts && typeof t.concentration_counts === 'object'
                    ? t.concentration_counts
                    : prev.model.concentration_counts,
                optics_mismatch:
                  t.optics_mismatch != null ? Boolean(t.optics_mismatch) : prev.model.optics_mismatch,
                train_y_min: t.train_y_min !== undefined ? t.train_y_min : prev.model.train_y_min,
                train_y_max: t.train_y_max !== undefined ? t.train_y_max : prev.model.train_y_max,
              },
            }
          : prev,
      )
    })
  }, [onToast])

  return (
    <div className="app">
      <header className="app-header">
        <h1 className="brand">
          Water<span>ly</span>
        </h1>
        <p className="muted tagline">Panel de control · espectroscopía NIR · ML</p>
      </header>

      <nav className="nav" aria-label="Secciones">
        {(
          [
            ['operation', 'Operación'],
            ['datos', 'Datos'],
            ['history', 'Histórico'],
            ['config', 'Config'],
            ['firmware', 'Firmware'],
            ['model', 'Modelo'],
          ] as const
        ).map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === 'operation' && <Operation telemetry={telemetry} status={status} onToast={onToast} />}
      {tab === 'datos' && <DatosPage telemetry={telemetry} status={status} onToast={onToast} />}
      {tab === 'history' && <HistoryPage onToast={onToast} />}
      {tab === 'config' && <ConfigPage onToast={onToast} />}
      {tab === 'firmware' && <FirmwarePage onToast={onToast} />}
      {tab === 'model' && <ModelPage onToast={onToast} />}

      {toast && <div className={`toast ${toast.ok ? 'ok' : 'error'}`}>{toast.msg}</div>}
    </div>
  )
}
