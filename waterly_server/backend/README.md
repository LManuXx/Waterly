# Modelos de Predicción — Waterly

## Contexto

El sensor AS7265x mide **18 longitudes de onda** (410–940 nm). El pico de absorción del nitrato está en ~220 nm (fuera de nuestro rango), por lo que **no medimos nitrato directamente**. Lo que hacemos es detectar **cambios indirectos** en el espectro causados por la presencia de nitratos: dispersión de luz, cambios en el índice de refracción, e interacciones con otros compuestos disueltos.

---

## Pipeline de Preprocesamiento

Antes de alimentar cualquier modelo, el espectro crudo pasa por estas transformaciones:

```
Luz cruda (I) → Absorbancia A = -log₁₀(I/I₀) → Savitzky-Golay → SNV → Modelo
```

### 1. Absorbancia (Ley de Beer-Lambert)

**Qué hace**: Convierte la intensidad de luz medida en absorbancia usando la calibración como referencia (I₀).

**Por qué**: La absorbancia es proporcional a la concentración del analito (Beer-Lambert). Es la magnitud física fundamental en espectroscopía.

```
A(λ) = -log₁₀( I_muestra(λ) / I_calibración(λ) )
```

### 2. Filtro Savitzky-Golay

**Qué hace**: Suaviza el espectro ajustando un polinomio local a cada ventana de datos. Preserva la forma de los picos mientras elimina ruido de alta frecuencia.

**Parámetros**: ventana=5 puntos, polinomio de grado 2.

**Visualización**:
```
Antes:  ▁▃▂▅▄▇▆█▇▅▄▃▂  (ruidoso, picos irregulares)
Después: ▁▃▂▅▄▇▆█▇▅▄▃▂  (suave, picos bien definidos)
```

**Por qué**: El ruido del sensor (fluctuaciones electrónicas, vibraciones) añade variación aleatoria que confunde al modelo. SG elimina este ruido sin distorsionar la señal real.

### 3. SNV (Standard Normal Variate)

**Qué hace**: Para cada espectro individual, resta la media y divide por la desviación estándar de sus 18 canales.

```
SNV(xᵢ) = (xᵢ - mean(x)) / std(x)
```

**Visualización**:
```
Antes:  [100, 200, 150, 300, 250, ...]  (valores absolutos, depende de turbidez)
Después: [-1.2, -0.3, -0.8, 1.1, 0.5, ...]  (forma relativa del espectro)
```

**Por qué**: Elimina efectos de dispersión de luz (turbidez, burbujas, path length) que no tienen relación con la concentración de nitratos. Solo importa la **forma** del espectro, no su magnitud absoluta.

---

## Modelo 1: DEVIATION (Distancia Euclidiana)

### ¿Qué es?

Mide **cuánto se aleja** un espectro desconocido del "centroide" (promedio) de los espectros de entrenamiento en las bandas UV/IR.

### ¿Cómo funciona?

```
1. Entrenamiento:
   - Se calcula el centroide (media) de todas las muestras en 8 bandas UV/IR
   - Se estandariza cada muestra respecto a ese centroide

2. Predicción:
   - Se proyecta la nueva muestra en el mismo espacio
   - Se calcula la distancia euclidiana al centroide (0,0,...,0)
   - Mayor distancia = mayor desviación del patrón de calibración
```

```
        │
   ●    │      ← Muestra con alta concentración (lejos del centro)
        │
        │   ●   ← Muestra con media concentración
        │
        │ ●     ← Muestra con baja concentración
   ─────┼──────
        │  ★    ← Centroide (agua calibrada)
```

### ¿Cuándo usarlo?

- **Pocas muestras** (1-2): es el único que funciona
- **Detección cualitativa**: "¿hay algo diferente en el agua?"
- **No predice concentración** en mg/L, solo da un número de "desviación"

### Limitaciones

- No da concentración directa
- Asume que mayor desviación = mayor concentración (no siempre lineal)
- Solo usa 8 de 18 bandas

---

## Modelo 2: PLSR (Partial Least Squares Regression)

### ¿Qué es?

El **estándar de la industria** en espectroscopía NIR. Encuentra las direcciones en los datos espectrales que **mejor correlacionan** con la concentración conocida.

### ¿Cómo funciona?

```
1. Encuentra "componentes latentes" — combinaciones de las 18 bandas
   que maximizan la correlación con la concentración

2. Cada componente captura un patrón espectral diferente:
   - Componente 1: el patrón que más correlaciona con nitratos
   - Componente 2: el segundo patrón más importante
   - etc.

3. La predicción es una combinación lineal de estos componentes
```

```
Espectro (18 bandas)  →  [PLSR]  →  Componente 1 (60% varianza)
                              →  Componente 2 (25% varianza)
                              →  Componente 3 (10% varianza)
                                       ↓
                              Concentración (mg/L)
```

### Validación Cruzada Leave-One-Out (LOOCV)

Con pocas muestras, no podemos separar en train/test. LOOCV resuelve esto:

```
Muestras: [5mg/L, 10mg/L, 20mg/L, 30mg/L]

Iteración 1: Entrena con [10, 20, 30] → Predice 5  → Error₁
Iteración 2: Entrena con [5, 20, 30]  → Predice 10 → Error₂
Iteración 3: Entrena con [5, 10, 30]  → Predice 20 → Error₃
Iteración 4: Entrena con [5, 10, 20]  → Predice 30 → Error₄

RMSECV = √(mean(Error₁², Error₂², Error₃², Error₄²))
```

El número de componentes que da el **menor RMSECV** se elige automáticamente.

### Métricas que reporta

| Métrica | Qué mide | Valor ideal |
|---------|----------|-------------|
| **R²** | % de varianza explicada | > 0.90 |
| **RMSE** | Error medio en entrenamiento (mg/L) | Lo menor posible |
| **RMSECV** | Error esperado en muestras nuevas (mg/L) | Lo menor posible |

**Interpretación**:
- R² = 0.95 → El modelo explica el 95% de la variación en concentración
- RMSECV = 1.5 mg/L → En una muestra nueva, esperamos ±1.5 mg/L de error

### Detección de Outliers

**Hotelling's T²**: Detecta muestras que están lejos del "centro" del espacio de componentes latentes. Una muestra con T² alto es inusual respecto al resto del dataset.

**Q Residuals**: Mide la variación del espectro que el modelo **no captura**. Un Q residual alto significa que la muestra tiene patrones espectrales que el modelo no entiende (posible contaminación, error de medición, etc.).

```
         T² alto (muestra atípica)
            ●
            │
   ● ● ● ●  │
   ─────────┼────── Límite 95%
   ● ● ●    │
            │
            Q alto (patrón no capturado)
```

### ¿Cuándo usarlo?

- **≥3 muestras** con concentraciones conocidas
- **Predicción cuantitativa**: quieres mg/L reales
- **Mejor precisión** que DEVIATION cuando hay suficientes datos

### Limitaciones

- Necesita al menos 3 muestras
- Con muy pocas muestras puede overfittear (por eso usamos LOOCV)
- Asume relación lineal entre espectro y concentración

---

## Comparación Rápida

| | DEVIATION | PLSR |
|---|-----------|------|
| **Mínimo muestras** | 1 | 3 |
| **Da concentración** | ❌ Solo desviación | ✅ mg/L |
| **Usa todas las bandas** | ❌ 8 bandas | ✅ 18 bandas |
| **Validación cruzada** | ❌ | ✅ LOOCV |
| **Detección outliers** | ❌ | ✅ T² + Q |
| **Robusto con pocas muestras** | ✅✅✅ | ✅✅ |
| **Precisión** | Baja | Alta |

---

## Recomendación de Uso

1. **Empieza con DEVIATION** si tienes 1-2 muestras
2. **Pasa a PLSR** cuando tengas ≥3 muestras con diferentes concentraciones
3. **Añade más muestras** con concentraciones variadas para mejorar PLSR
4. **Revisa RMSECV** para saber el error esperado de tu modelo
5. **Revisa outliers** para detectar muestras problemáticas
