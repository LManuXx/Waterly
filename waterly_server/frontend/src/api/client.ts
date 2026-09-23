export type Telemetry = Record<string, unknown> & {
  system_state?: string
  prediction_status?: string
  calibrated?: boolean
  blank_ok?: boolean
  optics_mismatch?: boolean
  pred_deviation?: number | null
  pred_mg_l?: number | null
  pred_unit?: string | null
  pred_kind?: string | null
  pred_confidence?: string | null
  pred_confidence_label?: string | null
  pred_in_model?: boolean | null
  pred_by_model?: Record<string, number | null> | null
  pred_consensus?: number | null
  pred_consensus_method?: string | null
  pred_consensus_weighted?: number | null
  pred_consensus_label?: string | null
  pred_agreement?: string | null
  pred_spread?: number | null
  pred_t2?: number | null
  pred_q?: number | null
  model_ready?: boolean
  model_mode?: string
  models_trained?: string[]
  n_samples?: number
  sample_labels?: number[]
  concentration_counts?: Record<string, number>
  train_y_min?: number | null
  train_y_max?: number | null
  has_baseline?: boolean
  baseline_ts?: number | null
  blank_check?: string | null
  blank_check_label?: string | null
  blank_check_ratio?: number | null
  burst_count?: number
  burst_target?: number
  spectrum_qc?: string | null
  spectrum_qc_label?: string | null
  spectrum_qc_reasons?: string[] | null
  spectrum_ts?: number
}

export type StatusResponse = {
  status: string
  state: string
  tb_enabled: boolean
  telemetry: Telemetry
  model: {
    is_trained: boolean
    model_type: string
    has_baseline: boolean
    n_samples: number
    sample_labels?: number[]
    concentration_counts?: Record<string, number>
    train_y_min?: number | null
    train_y_max?: number | null
    optics_mismatch?: boolean
    baseline_optics?: Record<string, unknown> | null
    baseline_ts?: number | null
    rmsecv?: number | null
    r2_train?: number | null
    rpd?: number | null
    models_trained?: string[]
    ensemble?: Record<string, unknown>
  }
}

export type PredictionRow = {
  t: number | null
  iso: string | null
  value: number | null
  pred_mg_l: number | null
  pred_deviation: number | null
  kind: string | null
  source: string | null
  confidence: string | null
  qc: string | null
  mode: string | null
  t2?: number | null
  q?: number | null
}

async function parseError(res: Response): Promise<string> {
  try {
    const j = await res.json()
    return j.detail || j.error || res.statusText
  } catch {
    return res.statusText
  }
}

export async function getStatus(): Promise<StatusResponse> {
  const res = await fetch('/api/status')
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function postCmd(method: string, body?: Record<string, unknown>) {
  const res = await fetch(`/api/cmd/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function getModelInfo() {
  const res = await fetch('/api/model/info')
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function getCurrentConfig() {
  const res = await fetch('/api/config/current')
  if (res.status === 404) return null
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function uploadFirmware(file: File, version: number) {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('version', String(version))
  const res = await fetch('/api/firmware/upload', { method: 'POST', body: fd })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function uploadModel(file: File) {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch('/api/model/upload', { method: 'POST', body: fd })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function uploadConfig(file: File) {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch('/api/config/upload', { method: 'POST', body: fd })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function factoryReset() {
  const res = await fetch('/api/config/factory_reset', { method: 'POST' })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function getHistory(minutes = 30, kind: 'raw' | 'abs' = 'raw') {
  const res = await fetch(`/api/history/spectrum?minutes=${minutes}&kind=${kind}`)
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function getPredictionHistory(minutes = 60): Promise<{ minutes: number; n_points: number; rows: PredictionRow[] }> {
  const res = await fetch(`/api/history/predictions?minutes=${minutes}`)
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export function connectTelemetry(onMsg: (t: Telemetry) => void): () => void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/telemetry`)
  ws.onmessage = (ev) => {
    try {
      onMsg(JSON.parse(ev.data))
    } catch { /* ignore */ }
  }
  ws.onopen = () => {
    const id = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, 25000)
    ;(ws as unknown as { _ping?: number })._ping = id as unknown as number
  }
  return () => {
    const id = (ws as unknown as { _ping?: number })._ping
    if (id) clearInterval(id)
    ws.close()
  }
}

export const WAVELENGTHS = [
  'A_410nm', 'B_435nm', 'C_460nm', 'D_485nm', 'E_510nm', 'F_535nm',
  'G_560nm', 'H_585nm', 'I_645nm', 'J_705nm', 'K_900nm', 'L_940nm',
  'R_610nm', 'S_680nm', 'T_730nm', 'U_760nm', 'V_810nm', 'W_860nm',
]
