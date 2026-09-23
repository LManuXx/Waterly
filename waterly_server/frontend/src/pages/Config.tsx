import { useEffect, useState } from 'react'
import { factoryReset, getCurrentConfig, postCmd, uploadConfig } from '../api/client'

type Props = { onToast: (msg: string, ok?: boolean) => void }

export function ConfigPage({ onToast }: Props) {
  const [form, setForm] = useState({
    wifi_ssid: '',
    wifi_pass: '',
    mqtt_broker: '',
    mqtt_topic_cmd: 'waterly/comandos',
    mqtt_topic_dat: 'waterly/datos',
    sensor_gain: '3',
    sensor_integration: '50',
    sensor_led_current: '0',
    ble_pop: 'waterly123',
    ota_url: 'http://waterly.local:8000/firmware/version.json',
  })

  useEffect(() => {
    getCurrentConfig()
      .then((cfg) => {
        if (!cfg) return
        setForm((f) => ({
          ...f,
          wifi_ssid: cfg.wifi_ssid ?? '',
          mqtt_broker: cfg.mqtt_broker_ip ?? cfg.mqtt_broker ?? '',
          mqtt_topic_cmd: cfg.mqtt_topic_cmd ?? f.mqtt_topic_cmd,
          mqtt_topic_dat: cfg.mqtt_topic_dat ?? f.mqtt_topic_dat,
          sensor_gain: String(cfg.sensor_gain ?? f.sensor_gain),
          sensor_integration: String(cfg.sensor_integration ?? f.sensor_integration),
          sensor_led_current: String(cfg.sensor_led_current ?? f.sensor_led_current),
          ble_pop: cfg.ble_pop ?? f.ble_pop,
          ota_url: cfg.ota_url ?? f.ota_url,
        }))
      })
      .catch(() => {})
  }, [])

  function set(key: string, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function save() {
    const config: Record<string, unknown> = {}
    if (form.wifi_ssid.trim()) config.wifi_ssid = form.wifi_ssid.trim()
    if (form.wifi_pass) config.wifi_pass = form.wifi_pass
    if (form.mqtt_broker.trim()) config.mqtt_broker = form.mqtt_broker.trim()
    if (form.mqtt_topic_cmd !== 'waterly/comandos') config.mqtt_topic_cmd = form.mqtt_topic_cmd
    if (form.mqtt_topic_dat !== 'waterly/datos') config.mqtt_topic_dat = form.mqtt_topic_dat
    if (form.sensor_gain !== '') config.sensor_gain = parseInt(form.sensor_gain, 10)
    if (form.sensor_integration) config.sensor_integration = parseInt(form.sensor_integration, 10)
    if (form.sensor_led_current !== '') config.sensor_led_current = parseInt(form.sensor_led_current, 10)
    if (form.ble_pop.trim()) config.ble_pop = form.ble_pop.trim()
    if (form.ota_url.trim() && form.ota_url !== 'http://waterly.local:8000/firmware/version.json') {
      config.ota_url = form.ota_url.trim()
    }
    if (Object.keys(config).length === 0) {
      onToast('Rellena al menos un campo', false)
      return
    }
    try {
      await postCmd('saveConfig', config)
      onToast('Config enviada al ESP32 (reiniciará)', true)
    } catch (e) {
      onToast(e instanceof Error ? e.message : String(e), false)
    }
  }

  return (
    <div className="panel">
      <h2>Configuración ESP32</h2>
      <p className="muted">Solo se envían los campos que rellenes. El ESP reinicia al guardar.</p>
      <div className="form-grid" style={{ marginTop: '1rem' }}>
        {(
          [
            ['wifi_ssid', 'WiFi SSID'],
            ['wifi_pass', 'WiFi password'],
            ['mqtt_broker', 'MQTT broker IP'],
            ['mqtt_topic_cmd', 'Topic comandos'],
            ['mqtt_topic_dat', 'Topic datos'],
            ['ble_pop', 'BLE POP'],
            ['ota_url', 'OTA URL'],
          ] as const
        ).map(([k, label]) => (
          <div className="field" key={k}>
            <label>{label}</label>
            <input
              type={k === 'wifi_pass' ? 'password' : 'text'}
              value={form[k]}
              onChange={(e) => set(k, e.target.value)}
            />
          </div>
        ))}
        <div className="field">
          <label>Ganancia del sensor</label>
          <select value={form.sensor_gain} onChange={(e) => set('sensor_gain', e.target.value)}>
            <option value="0">1x (baja)</option>
            <option value="1">3.7x</option>
            <option value="2">16x</option>
            <option value="3">64x (alta)</option>
          </select>
        </div>
        <div className="field">
          <label>Tiempo de integración (×2.8 ms)</label>
          <input value={form.sensor_integration} onChange={(e) => set('sensor_integration', e.target.value)} />
        </div>
        <div className="field">
          <label>Corriente LED</label>
          <select value={form.sensor_led_current} onChange={(e) => set('sensor_led_current', e.target.value)}>
            <option value="0">12.5 mA (bajo)</option>
            <option value="1">25 mA</option>
            <option value="2">50 mA</option>
            <option value="3">100 mA (máx)</option>
          </select>
        </div>
      </div>
      <div className="btn-groups" style={{ marginTop: '1rem' }}>
        <div className="btn-group">
          <span className="btn-group-label">Acciones</span>
          <div className="btn-row">
            <button type="button" className="action primary" onClick={save}>Guardar y reiniciar ESP</button>
            <a className="action" href="/api/config/download">Descargar JSON</a>
            <label className="action">
              Subir JSON
              <input
                type="file"
                accept=".json"
                hidden
                onChange={async (e) => {
                  const f = e.target.files?.[0]
                  if (!f) return
                  try {
                    await uploadConfig(f)
                    onToast('Config subida', true)
                  } catch (err) {
                    onToast(err instanceof Error ? err.message : String(err), false)
                  }
                }}
              />
            </label>
            <button
              type="button"
              className="action danger"
              onClick={async () => {
                if (
                  !confirm(
                    '¿Restablecer de fábrica el ESP32?\n\nSe borrará toda la configuración guardada en el dispositivo (WiFi, MQTT, sensor). Tendrás que reconfigurarlo.',
                  )
                ) {
                  return
                }
                try {
                  await factoryReset()
                  onToast('Factory reset enviado al ESP32', true)
                } catch (e) {
                  onToast(e instanceof Error ? e.message : String(e), false)
                }
              }}
            >
              Restablecer de fábrica
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
