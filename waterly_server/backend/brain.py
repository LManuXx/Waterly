import numpy as np
import joblib
import os
from sklearn.cross_decomposition import PLSRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score

MODEL_FILE = "waterly_model.pkl"

class SpectralBrain:
    def __init__(self):
        # --- ESTADO FÍSICO ---
        self.baseline = None  # I0 (Agua limpia)
        
        # --- ESTADO IA ---
        self.dataset_X = []   # Muestras de entrenamiento (18 canales SNV)
        self.dataset_y = []   # Valores numéricos (Desviación/Concentración)
        
        # Pipeline de IA
        self.scaler = None
        self.model = None
        self.model_type = "DEVIATION" # "DEVIATION" o "PLSR"
        
        self.is_trained = False
        
        # Índices de bandas UV e IR en self._get_wavelengths()
        # A_410(0), J_705(9), K_900(10), L_940(11), T_730(14), U_760(15), V_810(16), W_860(17)
        self.uv_ir_indices = [0, 9, 10, 11, 14, 15, 16, 17]
        
        self.load_brain()

    def reset_model(self):
        """ Limpia todas las muestras y el modelo actual """
        self.dataset_X = []
        self.dataset_y = []
        self.scaler = None
        self.model = None
        self.is_trained = False
        self.save_brain()
        print("[BRAIN] Modelo reiniciado. Memoria de IA limpia.")

    # ==========================================
    # 1. FÍSICA Y ÓPTICA (Calibración)
    # ==========================================
    def calibrate(self, raw_buffer):
        """ Paso 1: Establecer el Cero (I0) """
        print(f"[BRAIN] Calibrando con {len(raw_buffer)} muestras...")
        
        # Filtramos basura
        valid = [s for s in raw_buffer if self._is_valid_sample(s)]
        if not valid: return False
        
        # Promediamos
        matrix = [self._extract_spectrum(s) for s in valid]
        self.baseline = np.mean(matrix, axis=0)
        
        print(f"[BRAIN] Baseline fijada. Media: {np.mean(self.baseline):.1f}")
        self.save_brain()
        return True

    def get_absorbance(self, raw_data):
        """ Calcula Absorbancia y SNV """
        if self.baseline is None: return {}, False, None

        spectrum = self._extract_spectrum(raw_data)
        if spectrum is None: return {}, False, None

        # Evitar división por cero
        safe_base = np.where(self.baseline == 0, 1, self.baseline)
        
        # Transmitancia
        trans = np.array(spectrum) / safe_base
        trans = np.clip(trans, 1e-6, 10.0)
        
        # Absorbancia
        abs_raw = -np.log10(trans)
        
        # PREPROCESADO SNV 
        mean = np.mean(abs_raw)
        std = np.std(abs_raw)
        if std == 0: std = 1
        abs_snv = (abs_raw - mean) / std
        
        # Empaquetamos
        abs_dict = {}
        wavelengths = self._get_wavelengths()
        for i, wl in enumerate(wavelengths):
            # Formato: NombreDeBanda_abs para claridad en ThingsBoard
            abs_dict[f"{wl}_abs"] = round(float(abs_raw[i]), 4)
            
        return abs_dict, True, abs_snv

    # ==========================================
    # 2. INTELIGENCIA ARTIFICIAL (Regresión / Desviación)
    # ==========================================
    
    def add_training_sample(self, snv_data, numeric_value):
        """ Acumula datos para entrenamiento (NO guarda a disco para no bloquear MQTT) """
        self.dataset_X.append(snv_data)
        self.dataset_y.append(numeric_value)
        print(f"[BRAIN] Muestra con valor {numeric_value} guardada. Total dataset: {len(self.dataset_X)}")

    def train_model(self):
        """ Entrena el modelo según el modo seleccionado """
        # Filtrar muestras nulas que pudieron colarse
        valid_pairs = [(x, y) for x, y in zip(self.dataset_X, self.dataset_y) if x is not None]
        if len(valid_pairs) != len(self.dataset_X):
            removed = len(self.dataset_X) - len(valid_pairs)
            print(f"[BRAIN] AVISO: {removed} muestras nulas eliminadas del dataset.")
            self.dataset_X = [p[0] for p in valid_pairs]
            self.dataset_y = [p[1] for p in valid_pairs]
        
        print(f"[BRAIN] Entrenando modo {self.model_type} con {len(self.dataset_X)} muestras válidas.")
        
        if self.model_type == "DEVIATION":
            if len(self.dataset_X) < 1:
                print("[BRAIN] Se necesita al menos 1 muestra para entrenar la desviación.")
                return False
                
            try:
                X = np.array(self.dataset_X, dtype=float)
                print(f"[BRAIN] Shape del dataset: {X.shape}")
                # Extraer solo las bandas UV/IR
                X_filtered = X[:, self.uv_ir_indices]
                
                # Scaler nos da el centroide (mean_) y varianza (scale_) del patrón oro
                self.scaler = StandardScaler()
                self.scaler.fit(X_filtered)
                
                self.is_trained = True
                print(f"[BRAIN] ¡MODELO DE DESVIACIÓN ENTRENADO! Usando 8 bandas (UV/IR) con {len(X)} muestras base.")
                self.save_brain()
                return True
            except Exception as e:
                import traceback
                print(f"[BRAIN] Error entrenando desviación: {e}")
                traceback.print_exc()
                return False
                
        else: # PLSR
            if len(self.dataset_X) < 3:
                print("[BRAIN] Insuficientes datos para entrenar regresión (min 3).")
                return False
                
            try:
                X = np.array(self.dataset_X)
                y = np.array(self.dataset_y)
                
                # Scaler
                self.scaler = StandardScaler()
                X_scaled = self.scaler.fit_transform(X)
                
                # PLS Regression
                n_comp = min(2, len(self.dataset_X) - 1)
                if n_comp < 1: n_comp = 1
                
                self.model = PLSRegression(n_components=n_comp)
                self.model.fit(X_scaled, y)
                
                y_pred = self.model.predict(X_scaled)
                r2 = r2_score(y, y_pred)
                
                self.is_trained = True
                print(f"[BRAIN] ¡MODELO PLSR ENTRENADO! R2 Score: {r2:.3f}")
                self.save_brain()
                return True
            except Exception as e:
                print(f"[BRAIN] Error entrenando regresión: {e}")
                return False

    def predict(self, snv_data):
        if not self.is_trained: return None
        try:
            if self.model_type == "DEVIATION":
                # Extraemos bandas
                snv_array = np.array(snv_data)
                features_filtered = snv_array[self.uv_ir_indices].reshape(1, -1)
                # Estandarizamos respecto al centroide de la calibración
                features_scaled = self.scaler.transform(features_filtered)
                # Distancia Euclidiana desde el centroide (0,0...0)
                distance = np.linalg.norm(features_scaled)
                return round(float(distance), 3)
            else: # PLSR
                features = self.scaler.transform([snv_data])
                prediction = self.model.predict(features).flatten()[0]
                return round(float(prediction), 3)
        except Exception as e: 
            print(f"[BRAIN] Error en predicción: {e}")
            return None

    def get_coords(self, snv_data):
        return 0.0, 0.0

    # ==========================================
    # 3. UTILIDADES Y PERSISTENCIA
    # ==========================================
    def _is_valid_sample(self, raw):
        vals = self._extract_spectrum(raw)
        if not vals: return False
        arr = np.array(vals)
        return not (np.any(arr <= 0) or np.any(arr > 64000))

    def save_brain(self):
        state = {
            "baseline": self.baseline,
            "dataset_X": self.dataset_X,
            "dataset_y": self.dataset_y,
            "trained": self.is_trained,
            "scaler": self.scaler,
            "model": self.model,
            "model_type": self.model_type
        }
        joblib.dump(state, MODEL_FILE)

    def load_brain(self):
        if os.path.exists(MODEL_FILE):
            try:
                state = joblib.load(MODEL_FILE)
                self.baseline = state["baseline"]
                self.dataset_X = state.get("dataset_X", [])
                self.dataset_y = state.get("dataset_y", [])
                self.is_trained = state["trained"]
                if self.is_trained:
                    self.scaler = state["scaler"]
                    self.model = state.get("model", None)
                    self.model_type = state.get("model_type", "DEVIATION")
                print(f"[BRAIN] Cargado. {len(self.dataset_X)} muestras. Modo: {self.model_type}")
            except: pass

    def _get_wavelengths(self):
        return ["A_410nm", "B_435nm", "C_460nm", "D_485nm", "E_510nm", "F_535nm",
                "G_560nm", "H_585nm", "I_645nm", "J_705nm", "K_900nm", "L_940nm",
                "R_610nm", "S_680nm", "T_730nm", "U_760nm", "V_810nm", "W_860nm"]

    def _extract_spectrum(self, data):
        vals = []
        try:
            for wl in self._get_wavelengths():
                val = data.get(wl) if wl in data else data.get(f"raw_{wl}")
                if val is None: return None
                vals.append(float(val))
            return vals
        except: return None