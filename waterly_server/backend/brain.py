import numpy as np
import joblib
import os
import re
import time
from scipy.signal import savgol_filter
from sklearn.cross_decomposition import PLSRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_squared_error
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.linear_model import Ridge
from sklearn.svm import SVR
from sklearn.ensemble import RandomForestRegressor

MODEL_FILE = "/app/data/waterly_model.pkl"

# Umbrales de muestras para el ensemble de concentración
ENSEMBLE_MIN = {"PLSR": 3, "Ridge": 3, "SVR": 5, "RF": 8}

# Umbrales QC espectro (cuentas AS7265x calibradas)
QC_SATURATION = 64000.0
QC_NEAR_SAT = 55000.0
QC_DARK_MAX = 1.0       # fail solo si casi todo a oscuras
QC_LOW_WARN = 50.0      # aviso, no bloquea el burst


class SpectralBrain:
    def __init__(self):
        self.baseline = None
        self.baseline_optics = None  # {sensor_gain, sensor_integration, sensor_led_current}
        self.baseline_ts = None
        self.dataset_X = []
        self.dataset_y = []

        self.scaler = None
        self.model = None  # alias PLSR (T²/Q)
        self.models = {}  # ensemble: name -> estimator
        self.model_type = "PLSR"  # modo por defecto: concentración mg/L

        self.is_trained = False
        self.uv_ir_indices = [0, 9, 10, 11, 14, 15, 16, 17]

        self.sg_window = 5
        self.sg_polyorder = 2
        self.use_sg = True

        self.metrics = {}
        self._outlier_limits = {}  # t2_limit, q_limit, variances, loadings for predict-time QC

        self.load_brain()

    def reset_model(self):
        self.dataset_X = []
        self.dataset_y = []
        self.scaler = None
        self.model = None
        self.models = {}
        self.is_trained = False
        self.metrics = {}
        self._outlier_limits = {}
        self.save_brain()
        print("[BRAIN] Modelo reiniciado. Memoria de IA limpia.")

    def clear_baseline(self):
        """Borra solo el blanco I₀ (no toca muestras ni modelo)."""
        self.baseline = None
        self.baseline_optics = None
        self.baseline_ts = None
        self.save_brain()
        print("[BRAIN] Blanco (baseline) borrado.")
        return True

    def compare_to_baseline(self, raw_data):
        """Compara un espectro crudo con el blanco. ratio = mean(sample)/mean(I0)."""
        if self.baseline is None:
            return {
                "ok": False,
                "level": "fail",
                "ratio": None,
                "label": "No hay blanco calibrado",
            }
        vals = self._extract_spectrum(raw_data)
        if vals is None:
            return {
                "ok": False,
                "level": "fail",
                "ratio": None,
                "label": "Espectro incompleto",
            }
        arr = np.array(vals, dtype=float)
        base_mean = float(np.mean(self.baseline))
        if base_mean <= 0:
            base_mean = 1.0
        sample_mean = float(np.mean(arr))
        ratio = sample_mean / base_mean
        # Agua limpia debería estar cerca de 1.0
        if 0.85 <= ratio <= 1.15:
            level = "ok"
            label = "Blanco parece coherente (señal similar al I₀)"
            ok = True
        elif 0.7 <= ratio <= 1.3:
            level = "warn"
            label = "Señal algo distinta al blanco — limpia la cubeta o recalibra si persiste"
            ok = True
        else:
            level = "fail"
            label = "Señal muy distinta al blanco — limpia la cubeta o recalibra"
            ok = False
        return {
            "ok": ok,
            "level": level,
            "ratio": round(ratio, 3),
            "label": label,
            "sample_mean": round(sample_mean, 1),
            "baseline_mean": round(float(np.mean(self.baseline)), 1),
        }

    def sample_labels(self):
        return [round(float(y), 4) for y in self.dataset_y]

    def concentration_counts(self):
        """Conteo de muestras por concentración (clave string del label redondeado)."""
        counts = {}
        for y in self.dataset_y:
            key = f"{round(float(y), 4):g}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def train_range(self):
        if not self.dataset_y:
            return None, None
        ys = [float(y) for y in self.dataset_y]
        return round(min(ys), 4), round(max(ys), 4)

    @staticmethod
    def optics_fingerprint(config):
        if not config or not isinstance(config, dict):
            return None
        keys = ("sensor_gain", "sensor_integration", "sensor_led_current")
        fp = {}
        for k in keys:
            if k in config and config[k] is not None:
                fp[k] = config[k]
        return fp if len(fp) == 3 else None

    def optics_mismatch(self, current_config):
        """True si la óptica actual no coincide con la del blanco."""
        if self.baseline is None:
            return False
        if not self.baseline_optics:
            return False
        cur = self.optics_fingerprint(current_config)
        if not cur:
            return False
        return any(cur.get(k) != self.baseline_optics.get(k) for k in self.baseline_optics)

    def calibrate(self, raw_buffer, optics=None):
        print(f"[BRAIN] Calibrando con {len(raw_buffer)} muestras...")
        valid = [s for s in raw_buffer if self._is_valid_sample(s)]
        if not valid:
            return False

        matrix = [self._extract_spectrum(s) for s in valid]
        self.baseline = np.mean(matrix, axis=0)
        self.baseline_optics = optics if optics else self.baseline_optics
        self.baseline_ts = time.time()

        print(f"[BRAIN] Baseline fijada. Media: {np.mean(self.baseline):.1f}")
        if self.baseline_optics:
            print(f"[BRAIN] Óptica del blanco: {self.baseline_optics}")
        self.save_brain()
        return True

    def assess_spectrum_qc(self, raw_data):
        """QC de calidad espectral. level: ok | warn | fail.

        Fail solo ante problemas graves (oscuro total, saturación, NaN).
        Señal baja es warn: no debe bloquear calibración/entrenamiento en bucle.
        """
        vals = self._extract_spectrum(raw_data)
        if vals is None:
            return {
                "ok": False,
                "level": "fail",
                "reasons": ["espectro_incompleto"],
                "label": "Espectro incompleto",
            }

        arr = np.array(vals, dtype=float)
        reasons = []
        if np.any(~np.isfinite(arr)):
            reasons.append("valores_no_finitos")
        n_nonpos = int(np.sum(arr <= 0))
        if np.any(arr < 0):
            reasons.append("canal_negativo")
        elif n_nonpos == len(arr):
            reasons.append("todos_canales_cero")
        elif n_nonpos > 0:
            reasons.append("algunos_canales_cero")
        if np.any(arr > QC_SATURATION):
            reasons.append("saturacion")
        if np.any((arr > QC_NEAR_SAT) & (arr <= QC_SATURATION)):
            reasons.append("cerca_saturacion")
        mean_v = float(np.mean(arr))
        max_v = float(np.max(arr))
        if max_v < QC_DARK_MAX:
            reasons.append("senal_muy_baja")
        elif mean_v < QC_LOW_WARN:
            reasons.append("senal_baja")

        hard = {
            "espectro_incompleto",
            "valores_no_finitos",
            "canal_negativo",
            "todos_canales_cero",
            "saturacion",
            "senal_muy_baja",
        }
        if any(r in hard for r in reasons):
            level = "fail"
            ok = False
            label = "Muestra inválida — vuelve a medir"
        elif reasons:
            level = "warn"
            ok = True
            if "senal_baja" in reasons:
                label = "Señal baja (aviso) — se acepta la muestra"
            elif "cerca_saturacion" in reasons:
                label = "Cerca de saturación (aviso)"
            else:
                label = "Aviso de calidad"
        else:
            level = "ok"
            ok = True
            label = "Espectro OK"

        return {
            "ok": ok,
            "level": level,
            "reasons": reasons,
            "label": label,
            "mean": round(mean_v, 1),
            "max": round(max_v, 1),
            "min": round(float(np.min(arr)), 1),
        }

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

        # Savitzky-Golay en orden de longitud de onda (nm), luego restaurar orden de canales
        if self.use_sg and len(abs_raw) >= self.sg_window:
            sg_win = self.sg_window if self.sg_window % 2 == 1 else self.sg_window - 1
            order, inv = self._nm_sort_indices()
            sorted_abs = abs_raw[order]
            sorted_abs = savgol_filter(sorted_abs, window_length=sg_win, polyorder=self.sg_polyorder)
            abs_raw = sorted_abs[inv]

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

    def remove_training_sample(self, index):
        if index < 0 or index >= len(self.dataset_X):
            return False
        self.dataset_X.pop(index)
        self.dataset_y.pop(index)
        self.is_trained = False
        self.scaler = None
        self.model = None
        self.models = {}
        self.metrics = {}
        self._outlier_limits = {}
        self.save_brain()
        return True

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
        return self._train_ensemble()

    def _train_deviation(self):
        if len(self.dataset_X) < 1:
            print("[BRAIN] Se necesita al menos 1 muestra para entrenar la desviacion.")
            return False

        try:
            X = np.array(self.dataset_X, dtype=float)
            X_filtered = X[:, self.uv_ir_indices]

            self.scaler = StandardScaler()
            self.scaler.fit(X_filtered)
            self.model = None
            self.models = {}

            self.is_trained = True
            self.metrics = {
                "type": "DEVIATION",
                "n_samples": len(X),
                "n_features": len(self.uv_ir_indices),
                "bands_used": [self._get_wavelengths()[i] for i in self.uv_ir_indices],
                "y_labels": [round(float(v), 4) for v in self.dataset_y],
            }
            self._outlier_limits = {}
            print(f"[BRAIN] MODELO DE DESVIACION ENTRENADO! {len(X)} muestras.")
            self.save_brain()
            return True
        except Exception as e:
            import traceback
            print(f"[BRAIN] Error entrenando desviacion: {e}")
            traceback.print_exc()
            return False

    def _train_ensemble(self):
        """Entrena PLSR (+ Ridge/SVR/RF según n) sobre el mismo dataset."""
        n = len(self.dataset_X)
        if n < ENSEMBLE_MIN["PLSR"]:
            print("[BRAIN] Insuficientes datos para entrenar regresion (min 3).")
            return False

        try:
            X = np.array(self.dataset_X, dtype=float)
            y = np.array(self.dataset_y, dtype=float)

            self.scaler = StandardScaler()
            X_scaled = self.scaler.fit_transform(X)

            self.models = {}
            ensemble_metrics = {}
            y_sd = float(np.std(y, ddof=1)) if n > 1 else 0.0

            # --- PLSR (siempre, ancla T²/Q) ---
            max_comp = min(n - 1, X.shape[1])
            best_n_comp = 1
            best_rmsecv = float("inf")
            cv_results = {}
            loo = LeaveOneOut()
            for n_comp in range(1, max_comp + 1):
                errors = []
                for train_idx, test_idx in loo.split(X_scaled):
                    pls = PLSRegression(n_components=n_comp)
                    pls.fit(X_scaled[train_idx], y[train_idx])
                    y_pred = pls.predict(X_scaled[test_idx]).flatten()[0]
                    errors.append((y[test_idx][0] - y_pred) ** 2)
                rmsecv = float(np.sqrt(np.mean(errors)))
                cv_results[n_comp] = rmsecv
                if rmsecv < best_rmsecv:
                    best_rmsecv = rmsecv
                    best_n_comp = n_comp

            pls_final = PLSRegression(n_components=best_n_comp)
            pls_final.fit(X_scaled, y)
            self.models["PLSR"] = pls_final
            self.model = pls_final

            y_pred_train = pls_final.predict(X_scaled).flatten()
            r2 = float(r2_score(y, y_pred_train))
            rmse = float(np.sqrt(mean_squared_error(y, y_pred_train)))
            rpd_plsr = (y_sd / best_rmsecv) if best_rmsecv > 1e-12 else None

            t2_scores, q_residuals = self._compute_outliers(X_scaled)
            t2_limit, q_limit = self._outlier_thresholds(n, best_n_comp, q_residuals)
            outliers = [
                i for i in range(n)
                if t2_scores[i] > t2_limit or q_residuals[i] > q_limit
            ]
            variances = np.var(pls_final.x_scores_, axis=0, ddof=1)
            variances[variances == 0] = 1
            self._outlier_limits = {
                "t2_limit": float(t2_limit),
                "q_limit": float(q_limit),
                "variances": variances.tolist(),
                "n_comp": int(best_n_comp),
            }

            ensemble_metrics["PLSR"] = {
                "name": "PLSR",
                "rmsecv": round(float(best_rmsecv), 4),
                "r2": round(r2, 4),
                "rpd": round(float(rpd_plsr), 4) if rpd_plsr is not None else None,
                "n_samples_used": n,
                "n_components": best_n_comp,
            }

            # --- Ridge ---
            if n >= ENSEMBLE_MIN["Ridge"]:
                best_alpha = 1.0
                best_ridge_rmse = float("inf")
                for alpha in np.logspace(-2, 4, 12):
                    errors = []
                    for train_idx, test_idx in loo.split(X_scaled):
                        r = Ridge(alpha=alpha)
                        r.fit(X_scaled[train_idx], y[train_idx])
                        pred = float(r.predict(X_scaled[test_idx])[0])
                        errors.append((y[test_idx][0] - pred) ** 2)
                    rmse_cv = float(np.sqrt(np.mean(errors)))
                    if rmse_cv < best_ridge_rmse:
                        best_ridge_rmse = rmse_cv
                        best_alpha = float(alpha)
                ridge = Ridge(alpha=best_alpha)
                ridge.fit(X_scaled, y)
                ridge_r2 = float(r2_score(y, ridge.predict(X_scaled)))
                ridge_rpd = (y_sd / best_ridge_rmse) if best_ridge_rmse > 1e-12 else None
                self.models["Ridge"] = ridge
                ensemble_metrics["Ridge"] = {
                    "name": "Ridge",
                    "rmsecv": round(best_ridge_rmse, 4),
                    "r2": round(ridge_r2, 4),
                    "rpd": round(float(ridge_rpd), 4) if ridge_rpd is not None else None,
                    "n_samples_used": n,
                    "alpha": best_alpha,
                }

            # --- SVR ---
            if n >= ENSEMBLE_MIN["SVR"]:
                best_svr = None
                best_svr_rmse = float("inf")
                for C in (1.0, 10.0, 100.0):
                    for gamma in ("scale", 0.1):
                        svr = SVR(kernel="rbf", C=C, gamma=gamma)
                        cv_pred = cross_val_predict(svr, X_scaled, y, cv=LeaveOneOut())
                        rmse_cv = float(np.sqrt(mean_squared_error(y, cv_pred)))
                        if rmse_cv < best_svr_rmse:
                            best_svr_rmse = rmse_cv
                            best_svr = SVR(kernel="rbf", C=C, gamma=gamma)
                best_svr.fit(X_scaled, y)
                svr_r2 = float(r2_score(y, best_svr.predict(X_scaled)))
                svr_rpd = (y_sd / best_svr_rmse) if best_svr_rmse > 1e-12 else None
                self.models["SVR"] = best_svr
                ensemble_metrics["SVR"] = {
                    "name": "SVR",
                    "rmsecv": round(best_svr_rmse, 4),
                    "r2": round(svr_r2, 4),
                    "rpd": round(float(svr_rpd), 4) if svr_rpd is not None else None,
                    "n_samples_used": n,
                    "C": float(best_svr.C),
                    "gamma": best_svr.gamma if isinstance(best_svr.gamma, str) else float(best_svr.gamma),
                }

            # --- Random Forest ---
            if n >= ENSEMBLE_MIN["RF"]:
                rf = RandomForestRegressor(
                    n_estimators=50,
                    max_depth=3,
                    min_samples_leaf=1,
                    random_state=42,
                )
                rf_cv = cross_val_predict(rf, X_scaled, y, cv=LeaveOneOut())
                rf_rmsecv = float(np.sqrt(mean_squared_error(y, rf_cv)))
                rf.fit(X_scaled, y)
                rf_r2 = float(r2_score(y, rf.predict(X_scaled)))
                rf_rpd = (y_sd / rf_rmsecv) if rf_rmsecv > 1e-12 else None
                self.models["RF"] = rf
                ensemble_metrics["RF"] = {
                    "name": "RF",
                    "rmsecv": round(rf_rmsecv, 4),
                    "r2": round(rf_r2, 4),
                    "rpd": round(float(rf_rpd), 4) if rf_rpd is not None else None,
                    "n_samples_used": n,
                }

            self.is_trained = True
            self.metrics = {
                "type": "ENSEMBLE" if len(self.models) > 1 else "PLSR",
                "n_samples": n,
                "n_components": best_n_comp,
                "n_features": X.shape[1],
                "r2_train": round(r2, 4),
                "rmse_train": round(rmse, 4),
                "rmsecv": round(float(best_rmsecv), 4),
                "rpd": round(float(rpd_plsr), 4) if rpd_plsr is not None else None,
                "cv_results": {str(k): round(float(v), 4) for k, v in cv_results.items()},
                "t2_scores": [round(float(t), 4) for t in t2_scores],
                "q_residuals": [round(float(q), 6) for q in q_residuals],
                "t2_limit": round(float(t2_limit), 4),
                "q_limit": round(float(q_limit), 6),
                "outliers": outliers,
                "y_actual": [round(float(v), 4) for v in y],
                "y_predicted": [round(float(v), 4) for v in y_pred_train],
                "y_min": round(float(np.min(y)), 4),
                "y_max": round(float(np.max(y)), 4),
                "trained_at": time.time(),
                "ensemble": ensemble_metrics,
                "models_trained": list(self.models.keys()),
            }

            names = ", ".join(self.models.keys())
            print(f"[BRAIN] ENSEMBLE ENTRENADO [{names}] PLSR R2={r2:.3f} RMSECV={best_rmsecv:.3f}")
            self.save_brain()
            return True
        except Exception as e:
            import traceback
            print(f"[BRAIN] Error entrenando ensemble: {e}")
            traceback.print_exc()
            return False

    @staticmethod
    def consensus_predictions(by_model, rmsecv_by_model=None):
        """Consenso robusto: mediana, descarta outliers, media de retenidos."""
        rmsecv_by_model = rmsecv_by_model or {}
        vals = []
        for name, v in by_model.items():
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(fv) or fv < 0:
                continue
            vals.append((name, fv))

        if not vals:
            return {
                "pred_by_model": {k: None for k in ("PLSR", "Ridge", "SVR", "RF")},
                "pred_consensus": None,
                "pred_consensus_method": None,
                "pred_consensus_weighted": None,
                "pred_agreement": None,
                "pred_spread": None,
                "kept_models": [],
                "discarded_models": [],
            }

        by_full = {k: None for k in ("PLSR", "Ridge", "SVR", "RF")}
        for name, fv in vals:
            by_full[name] = round(fv, 3)

        if len(vals) == 1:
            only = vals[0][1]
            return {
                "pred_by_model": by_full,
                "pred_consensus": round(only, 3),
                "pred_consensus_method": "single",
                "pred_consensus_weighted": round(only, 3),
                "pred_agreement": "single",
                "pred_spread": 0.0,
                "kept_models": [vals[0][0]],
                "discarded_models": [],
            }

        arr = np.array([v for _, v in vals], dtype=float)
        median = float(np.median(arr))
        rmsecvs = [float(rmsecv_by_model.get(n, 0) or 0) for n, _ in vals]
        med_rmsecv = float(np.median(rmsecvs)) if rmsecvs else 0.0
        threshold = max(0.20 * abs(median), 2.0 * med_rmsecv, 1e-6)

        kept = [(n, v) for n, v in vals if abs(v - median) <= threshold]
        discarded = [n for n, v in vals if abs(v - median) > threshold]
        n_kept_raw = len(kept)

        if len(kept) >= 2:
            consensus = float(np.mean([v for _, v in kept]))
            method = "median_trimmed"
        elif len(kept) == 1:
            consensus = kept[0][1]
            method = "median_after_discard"
        else:
            # Nada pasó el filtro: caer a mediana de todos
            kept = list(vals)
            discarded = [n for n, _ in vals]  # todos eran outliers relativos
            consensus = median
            method = "median_fallback"

        kept_arr = np.array([v for _, v in kept], dtype=float)
        spread = float(np.max(kept_arr) - np.min(kept_arr)) if len(kept_arr) else 0.0

        # Ponderado por 1/RMSECV² (telemetría / depuración; UI usa trimmed mean)
        weights = []
        wvals = []
        for n, v in kept:
            r = float(rmsecv_by_model.get(n, 0) or 0)
            w = 1.0 / (r * r + 1e-6)
            weights.append(w)
            wvals.append(v)
        wsum = sum(weights) or 1.0
        weighted = float(sum(w * v for w, v in zip(weights, wvals)) / wsum)

        if len(vals) >= 3 and n_kept_raw < 2:
            agreement = "disagree"
        elif discarded or (
            spread > max(0.15 * abs(median), med_rmsecv, 1e-6)
        ):
            agreement = "warn"
        else:
            agreement = "ok"

        return {
            "pred_by_model": by_full,
            "pred_consensus": round(consensus, 3),
            "pred_consensus_method": method,
            "pred_consensus_weighted": round(weighted, 3),
            "pred_agreement": agreement,
            "pred_spread": round(spread, 3),
            "kept_models": [n for n, _ in kept],
            "discarded_models": discarded,
        }

    def _predict_one_model(self, name, features):
        est = self.models.get(name)
        if est is None:
            return None
        try:
            pred = float(est.predict(features).flatten()[0])
            if pred < 0:
                pred = 0.0
            return round(pred, 3)
        except Exception as e:
            print(f"[BRAIN] Error prediciendo {name}: {e}")
            return None

    def _compute_outliers(self, X_scaled):
        t_scores = self.model.x_scores_
        n_comp = self.model.n_components
        variances = np.var(t_scores, axis=0, ddof=1)
        variances[variances == 0] = 1
        t2 = np.sum(t_scores ** 2 / variances, axis=1)
        X_pred = t_scores @ self.model.x_loadings_[:, :n_comp].T
        Q = np.sum((X_scaled - X_pred) ** 2, axis=1)
        return t2, Q

    def _outlier_thresholds(self, n, n_comp, q_residuals):
        from scipy.stats import f
        if n <= n_comp:
            return float("inf"), float("inf")
        t2_limit = (n_comp * (n - 1) / (n - n_comp)) * f.ppf(0.95, n_comp, n - n_comp)
        q_mean = np.mean(q_residuals)
        q_std = np.std(q_residuals)
        q_limit = q_mean + 1.96 * q_std if q_std > 0 else q_mean * 2
        return float(t2_limit), float(q_limit)

    def _detect_outliers(self, t2_scores, q_residuals, n, n_comp):
        t2_limit, q_limit = self._outlier_thresholds(n, n_comp, q_residuals)
        outliers = []
        for i in range(n):
            if t2_scores[i] > t2_limit or q_residuals[i] > q_limit:
                outliers.append(i)
        return outliers

    def predict(self, snv_data):
        """Compat: solo el valor numérico."""
        result = self.predict_result(snv_data)
        return result["value"] if result else None

    def predict_result(self, snv_data):
        """Predicción detallada con units, ensemble y confianza."""
        if not self.is_trained or snv_data is None:
            return None
        try:
            if self.model_type == "DEVIATION":
                snv_array = np.array(snv_data)
                features_filtered = snv_array[self.uv_ir_indices].reshape(1, -1)
                features_scaled = self.scaler.transform(features_filtered)
                distance = round(float(np.linalg.norm(features_scaled)), 3)
                return {
                    "value": distance,
                    "pred_mg_l": None,
                    "pred_deviation": distance,
                    "unit": "índice",
                    "kind": "deviation",
                    "in_model": True,
                    "confidence": "ok",
                    "confidence_label": "Índice de desviación (no es mg/L)",
                    "t2": None,
                    "q": None,
                    "pred_by_model": None,
                    "pred_consensus": None,
                    "pred_consensus_method": None,
                    "pred_consensus_weighted": None,
                    "pred_agreement": None,
                    "pred_spread": None,
                }

            if self.scaler is None:
                return None

            features = self.scaler.transform([snv_data])

            # Predicciones por modelo (ensemble o solo PLSR legacy)
            model_names = list(self.models.keys()) if self.models else (
                ["PLSR"] if self.model is not None else []
            )
            if not self.models and self.model is not None:
                self.models = {"PLSR": self.model}

            by_model = {}
            for name in ("PLSR", "Ridge", "SVR", "RF"):
                if name in self.models:
                    by_model[name] = self._predict_one_model(name, features)
                else:
                    by_model[name] = None

            ens = self.metrics.get("ensemble") or {}
            rmsecv_map = {
                name: (ens.get(name) or {}).get("rmsecv", self.metrics.get("rmsecv"))
                for name in by_model
                if by_model[name] is not None
            }
            consensus = self.consensus_predictions(by_model, rmsecv_map)
            value = consensus["pred_consensus"]
            if value is None:
                return None

            t2 = q = None
            confidence = "ok"
            confidence_label = "Dentro del modelo"
            in_model = True
            limits = self._outlier_limits or {}
            pls = self.models.get("PLSR") or self.model
            if limits and pls is not None:
                n_comp = int(limits.get("n_comp", getattr(pls, "n_components", 1)))
                variances = np.array(limits.get("variances", []), dtype=float)
                t_scores = pls.transform(features)
                t_vec = t_scores.flatten()[:n_comp]
                if len(variances) >= n_comp:
                    t2 = float(np.sum((t_vec ** 2) / variances[:n_comp]))
                X_hat = t_scores[:, :n_comp] @ pls.x_loadings_[:, :n_comp].T
                q = float(np.sum((features - X_hat) ** 2))
                t2_limit = limits.get("t2_limit", float("inf"))
                q_limit = limits.get("q_limit", float("inf"))
                if t2 > t2_limit or q > q_limit:
                    in_model = False
                    confidence = "warn"
                    confidence_label = "Muestra rara — no confíes del todo en el número"
                y_min = self.metrics.get("y_min")
                y_max = self.metrics.get("y_max")
                if y_min is not None and y_max is not None:
                    if value < y_min * 0.5 or value > y_max * 1.5:
                        confidence = "warn" if confidence == "ok" else confidence
                        if confidence_label == "Dentro del modelo":
                            confidence_label = "Fuera del rango de entrenamiento"

            agreement = consensus.get("pred_agreement")
            if agreement in ("warn", "disagree"):
                confidence = "warn"
                if agreement == "disagree":
                    confidence_label = "Los modelos no coinciden — repite o recalibra"
                elif confidence_label == "Dentro del modelo":
                    confidence_label = "Modelos con algo de discrepancia"

            n_kept = len(consensus.get("kept_models") or [])
            method = consensus.get("pred_consensus_method")
            if method == "single":
                cons_label = "un solo modelo"
            elif consensus.get("discarded_models"):
                cons_label = f"media de {n_kept} tras descartar {', '.join(consensus['discarded_models'])}"
            else:
                cons_label = f"media de {n_kept} modelos"

            return {
                "value": value,
                "pred_mg_l": value,
                "pred_deviation": None,
                "unit": "mg/L",
                "kind": "concentration",
                "in_model": in_model,
                "confidence": confidence,
                "confidence_label": confidence_label,
                "t2": round(t2, 4) if t2 is not None else None,
                "q": round(q, 6) if q is not None else None,
                "pred_by_model": consensus["pred_by_model"],
                "pred_consensus": consensus["pred_consensus"],
                "pred_consensus_method": consensus["pred_consensus_method"],
                "pred_consensus_weighted": consensus["pred_consensus_weighted"],
                "pred_agreement": consensus["pred_agreement"],
                "pred_spread": consensus["pred_spread"],
                "pred_consensus_label": cons_label,
                "kept_models": consensus.get("kept_models"),
                "discarded_models": consensus.get("discarded_models"),
            }
        except Exception as e:
            print(f"[BRAIN] Error en prediccion: {e}")
            return None

    def get_model_info(self):
        info = {
            "status": "trained" if self.is_trained else "not_trained",
            "model_type": self.model_type,
            "is_trained": self.is_trained,
            "has_baseline": self.baseline is not None,
            "n_samples": len(self.dataset_X),
            "samples": [
                {"index": i, "label_mg_l": round(float(y), 4)}
                for i, y in enumerate(self.dataset_y)
            ],
            "baseline_optics": self.baseline_optics,
            "baseline_ts": self.baseline_ts,
            "models_trained": list(self.models.keys()) if self.models else (
                ["PLSR"] if self.is_trained and self.model_type != "DEVIATION" else []
            ),
        }
        if self.is_trained and self.metrics:
            info.update(self.metrics)
        elif not self.is_trained:
            info["status"] = "not_trained"
        return info

    def get_coords(self, snv_data):
        return 0.0, 0.0

    def _is_valid_sample(self, raw):
        vals = self._extract_spectrum(raw)
        if not vals:
            return False
        arr = np.array(vals)
        return not (np.any(arr <= 0) or np.any(arr > QC_SATURATION))

    def save_brain(self):
        state = {
            "baseline": self.baseline,
            "baseline_optics": self.baseline_optics,
            "baseline_ts": self.baseline_ts,
            "dataset_X": self.dataset_X,
            "dataset_y": self.dataset_y,
            "trained": self.is_trained,
            "scaler": self.scaler,
            "model": self.model,
            "models": self.models,
            "model_type": self.model_type,
            "metrics": self.metrics,
            "outlier_limits": self._outlier_limits,
            "use_sg": self.use_sg,
            "sg_window": self.sg_window,
            "sg_polyorder": self.sg_polyorder,
        }
        joblib.dump(state, MODEL_FILE)

    def load_brain(self):
        if os.path.exists(MODEL_FILE):
            try:
                state = joblib.load(MODEL_FILE)
                self.baseline = state["baseline"]
                self.baseline_optics = state.get("baseline_optics")
                self.baseline_ts = state.get("baseline_ts")
                self.dataset_X = state.get("dataset_X", [])
                self.dataset_y = state.get("dataset_y", [])
                self.is_trained = state["trained"]
                self.model_type = state.get("model_type", "PLSR")
                if self.is_trained:
                    self.scaler = state["scaler"]
                    self.model = state.get("model", None)
                    self.models = state.get("models") or {}
                    if not self.models and self.model is not None and self.model_type != "DEVIATION":
                        self.models = {"PLSR": self.model}
                    elif self.models and self.model is None:
                        self.model = self.models.get("PLSR")
                else:
                    self.models = {}
                self.metrics = state.get("metrics", {})
                self._outlier_limits = state.get("outlier_limits", {})
                if self.is_trained and self.model_type != "DEVIATION" and self.model is not None and not self._outlier_limits:
                    if "t2_limit" in self.metrics and "q_limit" in self.metrics:
                        self._outlier_limits = {
                            "t2_limit": self.metrics["t2_limit"],
                            "q_limit": self.metrics["q_limit"],
                            "n_comp": self.metrics.get("n_components", self.model.n_components),
                            "variances": np.var(self.model.x_scores_, axis=0, ddof=1).tolist(),
                        }
                self.use_sg = state.get("use_sg", True)
                self.sg_window = state.get("sg_window", 5)
                self.sg_polyorder = state.get("sg_polyorder", 2)
                trained_names = list(self.models.keys()) if self.models else self.model_type
                print(f"[BRAIN] Cargado. {len(self.dataset_X)} muestras. Modo: {self.model_type} models={trained_names}")
            except Exception as e:
                print(f"[BRAIN] Error cargando modelo: {e}")

    def _get_wavelengths(self):
        return [
            "A_410nm", "B_435nm", "C_460nm", "D_485nm", "E_510nm", "F_535nm",
            "G_560nm", "H_585nm", "I_645nm", "J_705nm", "K_900nm", "L_940nm",
            "R_610nm", "S_680nm", "T_730nm", "U_760nm", "V_810nm", "W_860nm",
        ]

    def _nm_sort_indices(self):
        wls = self._get_wavelengths()
        nms = [int(re.search(r"(\d+)", w).group(1)) for w in wls]
        order = np.argsort(nms)
        inv = np.argsort(order)
        return order, inv

    def _extract_spectrum(self, data):
        vals = []
        try:
            for wl in self._get_wavelengths():
                val = data.get(wl) if wl in data else data.get(f"raw_{wl}")
                if val is None:
                    return None
                vals.append(float(val))
            return vals
        except Exception:
            return None
