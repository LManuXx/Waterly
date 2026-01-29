import numpy as np
import joblib
import os
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import KNeighborsClassifier

MODEL_FILE = "waterly_model.pkl"

class SpectralBrain:
    def __init__(self):
        # --- MEMORIA (Estado) ---
        self.baseline = None  # La "Línea Base" de agua limpia
        
        # --- IA (Para el futuro) ---
        self.pca = PCA(n_components=2)
        self.scaler = StandardScaler()
        self.classifier = KNeighborsClassifier(n_neighbors=3)
        
        self.X_train = [] 
        self.y_train = [] 
        self.is_trained = False
        
        # Cargar memoria al iniciar
        self.load_brain()

    def calibrate(self, raw_buffer):
        """ 
        Toma una lista de muestras crudas, descarta las malas y 
        crea una nueva Línea Base (Sustituye a la anterior).
        """
        print(f"[BRAIN] Procesando calibración con {len(raw_buffer)} muestras...")
        
        # 1. FILTRADO: Descartar muestras defectuosas
        clean_samples = self._filter_calibration_samples(raw_buffer)
        
        if not clean_samples:
            print("[BRAIN] ERROR: Ninguna muestra válida para calibrar.")
            return False

        # 2. PROMEDIO: Calcular la media de las muestras buenas
        # Convertimos lista de dicts a matriz numpy para promediar fácil
        matrix = []
        for sample in clean_samples:
            matrix.append(self._extract_spectrum(sample))
            
        avg_spectrum = np.mean(matrix, axis=0)
        
        # 3. SUSTITUCIÓN: Reemplazamos el modelo anterior
        self.baseline = np.array(avg_spectrum)
        print(f"[BRAIN] ¡Calibración Exitosa! Nueva Baseline (Media): {np.mean(self.baseline):.1f}")
        
        # 4. PERSISTENCIA: Guardamos en disco inmediatamente
        self.save_brain()
        return True

    def get_absorbance(self, raw_data):
        """ Calcula Absorbancia usando la Baseline actual """
        if self.baseline is None:
            print("[BRAIN] Aviso: Intentando medir sin calibración previa.")
            return {}, False

        spectrum = self._extract_spectrum(raw_data)
        if spectrum is None:
            return {}, False

        # Evitar división por cero
        safe_baseline = np.where(self.baseline == 0, 1, self.baseline)
        
        # Transmitancia y Absorbancia
        transmission = np.array(spectrum) / safe_baseline
        transmission = np.clip(transmission, 1e-6, 2.0) # Evita infinitos
        absorbance = -np.log10(transmission)
        
        # Limpieza visual (Negativos a cero)
        absorbance = np.maximum(absorbance, 0)
        
        # Empaquetar con nombres de canales
        abs_dict = {}
        wavelengths = self._get_wavelengths()
        for i, wl in enumerate(wavelengths):
            abs_dict[wl] = round(float(absorbance[i]), 4)
            
        return abs_dict, True

    def _filter_calibration_samples(self, buffer):
        """ Elimina muestras con valores negativos, ceros o saturación """
        valid_buffer = []
        for sample in buffer:
            vals = self._extract_spectrum(sample)
            if vals is None: continue
            
            # Criterios de descarte:
            # 1. Algún canal negativo (Error sensor)
            # 2. Algún canal es 0 (Led apagado/Error)
            # 3. Algún canal > 60000 (Saturación de luz)
            if np.any(np.array(vals) <= 0) or np.any(np.array(vals) > 60000):
                print("[BRAIN] Muestra descartada (Valores fuera de rango)")
                continue
            
            valid_buffer.append(sample)
            
        print(f"[BRAIN] Muestras válidas: {len(valid_buffer)} de {len(buffer)}")
        return valid_buffer

    # --- FUNCIONES AUXILIARES Y PERSISTENCIA (IA) ---
    
    # Mantenemos las funciones de IA (teach, predict, get_coords) igual que antes
    # pero simplificadas aquí para centrarme en tu petición de calibración.
    # Cuando implementemos la predicción luego, las usaremos.
    
    def get_coords(self, abs_data):
        # Retorna 0,0 si no hay IA entrenada, o las coords si la hay
        if not self.is_trained: return 0.0, 0.0
        try:
            feats = self.scaler.transform([list(abs_data.values())])
            coords = self.pca.transform(feats)
            return float(coords[0,0]), float(coords[0,1])
        except: return 0.0, 0.0
        
    def predict(self, abs_data):
        if not self.is_trained: return "Unknown"
        try:
            feats = self.scaler.transform([list(abs_data.values())])
            return self.classifier.predict(feats)[0]
        except: return "Error"

    def teach(self, abs_data, label):
        # Lo usaremos en la siguiente fase
        self.X_train.append(list(abs_data.values()))
        self.y_train.append(label)
        self._retrain()
        
    def _retrain(self):
        if len(self.X_train) < 3: return
        X = np.array(self.X_train)
        self.scaler.fit(X)
        X_scaled = self.scaler.transform(X)
        self.pca.fit(X_scaled)
        self.classifier.fit(X_scaled, self.y_train)
        self.is_trained = True
        self.save_brain()

    def save_brain(self):
        state = {
            "baseline": self.baseline,
            "X": self.X_train,
            "y": self.y_train,
            "trained": self.is_trained,
            # Guardamos los objetos de sklearn si están entrenados
            "pca": self.pca if self.is_trained else None,
            "scaler": self.scaler if self.is_trained else None,
            "knn": self.classifier if self.is_trained else None
        }
        joblib.dump(state, MODEL_FILE)
        print("[BRAIN] Memoria guardada en disco.")

    def load_brain(self):
        if os.path.exists(MODEL_FILE):
            try:
                state = joblib.load(MODEL_FILE)
                self.baseline = state["baseline"]
                self.X_train = state["X"]
                self.y_train = state["y"]
                self.is_trained = state["trained"]
                if self.is_trained:
                    self.pca = state["pca"]
                    self.scaler = state["scaler"]
                    self.classifier = state["knn"]
                print("[BRAIN] Memoria cargada correctamente.")
            except:
                print("[BRAIN] Error al cargar memoria. Iniciando de cero.")

    def _get_wavelengths(self):
        return [
            "A_410nm", "B_435nm", "C_460nm", "D_485nm", "E_510nm", "F_535nm",
            "G_560nm", "H_585nm", "I_645nm", "J_705nm", "K_900nm", "L_940nm",
            "R_610nm", "S_680nm", "T_730nm", "U_760nm", "V_810nm", "W_860nm"
        ]

    def _extract_spectrum(self, data):
        values = []
        try:
            for wl in self._get_wavelengths():
                val = data.get(wl)
                if val is None: val = data.get(f"raw_{wl}") # Compatibilidad
                if val is None: return None
                values.append(float(val))
            return values
        except: return None