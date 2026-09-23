import { useState } from 'react'
import { postCmd, uploadFirmware } from '../api/client'

type Props = { onToast: (msg: string, ok?: boolean) => void }

export function FirmwarePage({ onToast }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [version, setVersion] = useState('2')
  const [busy, setBusy] = useState(false)

  async function upload() {
    if (!file) {
      onToast('Selecciona un .bin', false)
      return
    }
    const v = parseInt(version, 10)
    if (!v || v < 1) {
      onToast('Versión inválida', false)
      return
    }
    setBusy(true)
    try {
      const r = await uploadFirmware(file, v)
      onToast(`Firmware v${r.version} subido. Enviando OTA…`, true)
      await postCmd('updateFirmware', {})
      onToast('OTA disparada. El ESP descargará e instalará.', true)
    } catch (e) {
      onToast(e instanceof Error ? e.message : String(e), false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <h2>Firmware OTA</h2>
      <p className="muted">Sube <code className="mono">waterly.bin</code> (tras <code className="mono">./compile.sh</code>) y una versión mayor que la actual del ESP.</p>
      <div className="field-row">
        <div className="field">
          <label>Archivo .bin</label>
          <input
            type="file"
            accept=".bin"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <div className="field">
          <label>Versión</label>
          <input type="number" min={1} value={version} onChange={(e) => setVersion(e.target.value)} />
        </div>
      </div>
      {file && <p className="muted mono">{file.name} ({(file.size / 1024 / 1024).toFixed(2)} MB)</p>}
      <div className="btn-row">
        <button className="action primary" disabled={busy} onClick={upload}>
          Subir y actualizar
        </button>
      </div>
    </div>
  )
}
