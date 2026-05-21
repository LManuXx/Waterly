import numpy as np
import joblib
import os
from scipy.signal import savgol_filter
from sklearn.cross_decomposition import PLSRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_squared_error
from sklearn.model_selection import LeaveOneOut

MODEL_FILE = "/app/data/waterly_model.pkl"

class SpectralBrain:
    def __init__(self):
        self.baseline = None
        self.dataset_X = []
        self.dataset_y = []

        self.scaler = None
        self.model = None
        self.model_type = "DEVIATION"

        self.is_trained = False
        self.uv_ir_indices = [0, 9, 10, 11, 14, 15, 16, 17]

        # Preprocesamiento Savitzky-Golay
        self.sg_window = 5
        self.sg_polyorder = 2
        self.use_sg = True

        # Metricas del modelo
        self.metrics = {}

        self.load_brain()

    def reset_model(self):
        self.dataset_X = []
        self.dataset_y = []
        self.scaler = None
        self.model = None
        self.is_trained = False
        self.metrics = {}
        self.save_brain()
        print("[BRAIN] Modelo reiniciado. Memoria de IA limpia.")

    def calibrate(self, raw_buffer):
        print(f"[BRAIN] Calibrando con {len(raw_buffer)} muestras...")
        valid = [s for s in raw_buffer if self._is_valid_sample(s)]
        if not valid:
            return False

        matrix = [self._extract_spectrum(s) for s in valid]
        self.baseline = np.mean(matrix, axis=0)

        print(f"[BRAIN] Baseline fijada. Media: {np.mean(self.baseline):.1f}")
        self.save_brain()
        return True

    def get_absorbance(self, raw_data):
        if self.baseline is None:
            return {}, False, None

        spectrum = self._extract_spectrum(raw_data)
        if spectrum is None:
            return {}, False, None

        safe_base = np.where(self.baseline == 0, 1, self.baseline)
        trans = np.array(spectrum) / safe_base
        trans = np.clip(trans, 1e-6, 10.0)
        abs_raw = -np.log10(trans)

        # Savitzky-Golay: suaviza ruido preservando picos espectrales
        if self.use_sg and len(abs_raw) >= self.sg_window:
            sg_win = self.sg_window if self.sg_window % 2 == 1 else self.sg_window - 1
            abs_raw = savgol_filter(abs_raw, window_length=sg_win, polyorder=self.sg_polyorder)

        # SNV: correccion de dispersion
        mean = np.mean(abs_raw)
        std = np.std(abs_raw)
        if std == 0:
            std = 1
        abs_snv = (abs_raw - mean) / std

        abs_dict = {}
        wavelengths = self._get_wavelengths()
        for i, wl in enumerate(wavelengths):
            abs_dict[f"{wl}_abs"] = round(float(abs_raw[i]), 4)

        return abs_dict, True, abs_snv

    def add_training_sample(self, snv_data, numeric_value):
        self.dataset_X.append(snv_data)
        self.dataset_y.append(numeric_value)
        print(f"[BRAIN] Muestra con valor {numeric_value} guardada. Total dataset: {len(self.dataset_X)}")

    def train_model(self):
        valid_pairs = [(x, y) for x, y in zip(self.dataset_X, self.dataset_y) if x is not None]
        if len(valid_pairs) != len(self.dataset_X):
            removed = len(self.dataset_X) - len(valid_pairs)
            print(f"[BRAIN] AVISO: {removed} muestras nulas eliminadas del dataset.")
            self.dataset_X = [p[0] for p in valid_pairs]
            self.dataset_y = [p[1] for p in valid_pairs]

        print(f"[BRAIN] Entrenando modo {self.model_type} con {len(self.dataset_X)} muestras validas.")

        if self.model_type == "DEVIATION":
            return self._train_deviation()
        else:
            return self._train_plsr()

    def _train_deviation(self):
        if len(self.dataset_X) < 1:
            print("[BRAIN] Se necesita al menos 1 muestra para entrenar la desviacion.")
            return False

        try:
            X = np.array(self.dataset_X, dtype=float)
            print(f"[BRAIN] Shape del dataset: {X.shape}")
            X_filtered = X[:, self.uv_ir_indices]

            self.scaler = StandardScaler()
            self.scaler.fit(X_filtered)

            self.is_trained = True
            self.metrics = {
                "type": "DEVIATION",
                "n_samples": len(X),
                "n_features": len(self.uv_ir_indices),
                "bands_used": [self._get_wavelengths()[i] for i in self.uv_ir_indices]
            }
            print(f"[BRAIN] MODELO DE DESVIACION ENTRENADO! Usando {len(self.uv_ir_indices)} bandas (UV/IR) con {len(X)} muestras.")
            self.save_brain()
            return True
        except Exception as e:
            import traceback
            print(f"[BRAIN] Error entrenando desviacion: {e}")
            traceback.print_exc()
            return False

    def _train_plsr(self):
        n = len(self.dataset_X)
        if n < 3:
            print("[BRAIN] Insuficientes datos para entrenar regresion (min 3).")
            return False

        try:
            X = np.array(self.dataset_X)
            y = np.array(self.dataset_y)

            self.scaler = StandardScaler()
            X_scaled = self.scaler.fit_transform(X)

            # LOOCV para encontrar numero optimo de componentes
            max_comp = min(n - 1, X.shape[1])
            best_n_comp = 1
            best_rmsecv = float('inf')
            cv_results = {}

            loo = LeaveOneOut()
            for n_comp in range(1, max_comp + 1):
                errors = []
                for train_idx, test_idx in loo.split(X_scaled):
                    X_train, X_test = X_scaled[train_idx], X_scaled[test_idx]
                    y_train, y_test = y[train_idx], y[test_idx]

                    pls = PLSRegression(n_components=n_comp)
                    pls.fit(X_train, y_train)
                    y_pred = pls.predict(X_test).flatten()[0]
                    errors.append((y_test[0] - y_pred) ** 2)

                rmsecv = np.sqrt(np.mean(errors))
                cv_results[n_comp] = rmsecv

                if rmsecv < best_rmsecv:
                    best_rmsecv = rmsecv
                    best_n_comp = n_comp

            print(f"[BRAIN] LOOCV resultados: {cv_results}")
            print(f"[BRAIN] Componentes optimos: {best_n_comp} (RMSECV: {best_rmsecv:.3f})")

            # Entrenar modelo final
            self.model = PLSRegression(n_components=best_n_comp)
            self.model.fit(X_scaled, y)

            # Metricas en training set
            y_pred_train = self.model.predict(X_scaled).flatten()
            r2 = r2_score(y, y_pred_train)
            rmse = np.sqrt(mean_squared_error(y, y_pred_train))

            # Deteccion de outliers: Hotelling T2 y Q residuals
            t2_scores, q_residuals = self._compute_outliers(X_scaled)
            outliers = self._detect_outliers(t2_scores, q_residuals, n, best_n_comp)
            if outliers:
                print(f"[BRAIN] AVISO: Outliers detectados en muestras: {outliers}")

            self.is_trained = True
            self.metrics = {
                "type": "PLSR",
                "n_samples": n,
                "n_components": best_n_comp,
                "n_features": X.shape[1],
                "r2_train": round(float(r2), 4),
                "rmse_train": round(float(rmse), 4),
                "rmsecv": round(float(best_rmsecv), 4),
                "cv_results": {str(k): round(float(v), 4) for k, v in cv_results.items()},
                "t2_scores": [round(float(t), 4) for t in t2_scores],
                "q_residuals": [round(float(q), 6) for q in q_residuals],
                "outliers": outliers,
                "y_actual": [round(float(v), 4) for v in y],
                "y_predicted": [round(float(v), 4) for v in y_pred_train]
            }

            print(f"[BRAIN] MODELO PLSR ENTRENADO!")
            print(f"[BRAIN]   R2 = {r2:.3f} | RMSE = {rmse:.3f} mg/L | RMSECV = {best_rmsecv:.3f} mg/L")
            print(f"[BRAIN]   Componentes: {best_n_comp} | Outliers: {len(outliers)}")
            self.save_brain()
            return True
        except Exception as e:
            import traceback
            print(f"[BRAIN] Error entrenando regresion: {e}")
            traceback.print_exc()
            return False

    def _compute_outliers(self, X_scaled):
        t_scores = self.model.x_scores_
        n_comp = self.model.n_components

        variances = np.var(t_scores, axis=0, ddof=1)
        variances[variances == 0] = 1
        t2 = np.sum(t_scores ** 2 / variances, axis=1)

        X_pred = t_scores @ self.model.x_loadings_[:, :n_comp].T
        Q = np.sum((X_scaled - X_pred) ** 2, axis=1)

        return t2, Q

    def _detect_outliers(self, t2_scores, q_residuals, n, n_comp):
        from scipy.stats import f
        if n <= n_comp:
            return []

        t2_limit = (n_comp * (n - 1) / (n - n_comp)) * f.ppf(0.95, n_comp, n - n_comp)

        q_mean = np.mean(q_residuals)
        q_std = np.std(q_residuals)
        q_limit = q_mean + 1.96 * q_std if q_std > 0 else q_mean * 2

        outliers = []
        for i in range(n):
            if t2_scores[i] > t2_limit or q_residuals[i] > q_limit:
                outliers.append(i)
        return outliers

    def predict(self, snv_data):
        if not self.is_trained:
            return None
        try:
            if self.model_type == "DEVIATION":
                snv_array = np.array(snv_data)
                features_filtered = snv_array[self.uv_ir_indices].reshape(1, -1)
                features_scaled = self.scaler.transform(features_filtered)
                distance = np.linalg.norm(features_scaled)
                return round(float(distance), 3)
            else:
                features = self.scaler.transform([snv_data])
                prediction = self.model.predict(features).flatten()[0]
                if prediction < 0:
                    prediction = 0.0
                return round(float(prediction), 3)
        except Exception as e:
            print(f"[BRAIN] Error en prediccion: {e}")
            return None

    def get_model_info(self):
        if not self.is_trained:
            return {"status": "not_trained"}
        return self.metrics

    def get_coords(self, snv_data):
        return 0.0, 0.0

    def _is_valid_sample(self, raw):
        vals = self._extract_spectrum(raw)
        if not vals:
            return False
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
            "model_type": self.model_type,
            "metrics": self.metrics,
            "use_sg": self.use_sg,
            "sg_window": self.sg_window,
            "sg_polyorder": self.sg_polyorder
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
                self.metrics = state.get("metrics", {})
                self.use_sg = state.get("use_sg", True)
                self.sg_window = state.get("sg_window", 5)
                self.sg_polyorder = state.get("sg_polyorder", 2)
                print(f"[BRAIN] Cargado. {len(self.dataset_X)} muestras. Modo: {self.model_type}")
                if self.metrics:
                    print(f"[BRAIN] Metricas: {self.metrics}")
            except:
                pass

    def _get_wavelengths(self):
        return ["A_410nm", "B_435nm", "C_460nm", "D_485nm", "E_510nm", "F_535nm",
                "G_560nm", "H_585nm", "I_645nm", "J_705nm", "K_900nm", "L_940nm",
                "R_610nm", "S_680nm", "T_730nm", "U_760nm", "V_810nm", "W_860nm"]

    def _extract_spectrum(self, data):
        vals = []
        try:
            for wl in self._get_wavelengths():
                val = data.get(wl) if wl in data else data.get(f"raw_{wl}")
                if val is None:
                    return None
                vals.append(float(val))
            return vals
        except:
            return None
