import type uPlot from 'uplot'

/** Zoom por caja (arrastre), rueda y doble clic para resetear. */
export function zoomCursorOptions(): uPlot.Cursor {
  return {
    show: true,
    points: { size: 6 },
    drag: {
      x: true,
      y: true,
      setScale: true,
    },
  }
}

export function zoomSelectOptions(): uPlot.Select {
  return {
    show: true,
    left: 0,
    top: 0,
    width: 0,
    height: 0,
  }
}

/** Enlaza rueda (zoom al cursor) y doble clic (reset) al área de la gráfica. */
export function bindZoomInteractions(u: uPlot): () => void {
  const over = u.over

  const onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const xs = u.data[0]
    if (!xs?.length) return

    const dataMin = xs[0] as number
    const dataMax = xs[xs.length - 1] as number
    if (!Number.isFinite(dataMin) || !Number.isFinite(dataMax) || dataMax <= dataMin) return

    const rect = over.getBoundingClientRect()
    const px = e.clientX - rect.left
    const xVal = u.posToVal(px, 'x')
    const scale = u.scales.x
    if (scale.min == null || scale.max == null) return

    const factor = e.deltaY < 0 ? 0.75 : 1.35
    let min = xVal - (xVal - scale.min) * factor
    let max = xVal + (scale.max - xVal) * factor
    const full = dataMax - dataMin

    if (max - min >= full * 0.98) {
      min = dataMin
      max = dataMax
    } else {
      if (min < dataMin) {
        max += dataMin - min
        min = dataMin
      }
      if (max > dataMax) {
        min -= max - dataMax
        max = dataMax
      }
      min = Math.max(min, dataMin)
      max = Math.min(max, dataMax)
    }

    u.setScale('x', { min, max })
  }

  const onDblClick = () => {
    resetZoom(u)
  }

  over.addEventListener('wheel', onWheel, { passive: false })
  over.addEventListener('dblclick', onDblClick)

  return () => {
    over.removeEventListener('wheel', onWheel)
    over.removeEventListener('dblclick', onDblClick)
  }
}

export function resetZoom(u: uPlot) {
  const xs = u.data[0]
  if (!xs?.length) {
    u.batch(() => {
      u.setScale('x', { min: null as unknown as number, max: null as unknown as number })
      u.setScale('y', { min: null as unknown as number, max: null as unknown as number })
    })
    return
  }
  const dataMin = xs[0] as number
  const dataMax = xs[xs.length - 1] as number
  u.batch(() => {
    u.setScale('x', { min: dataMin, max: dataMax })
    u.setScale('y', { min: null as unknown as number, max: null as unknown as number })
  })
}
