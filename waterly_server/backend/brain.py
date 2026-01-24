import numpy as np
from sklearn.decomposition import PCA
import json
import os
import joblib

class SpectralBrain:
    def __init__(self):
        self.baseline = None # Aquí guardaremos el espectro del "Agua limpia"
        self.pca_model = PCA(n_components=2) # Queremos reducir a 2D (X, Y)
        self.history = [] # Memoria a corto plazo para entrenar el PCA
        self.is_fitted = False
        
        # Orden oficial de canales para asegurar que los arrays siempre coincidan
        self.channels = [
            "A_410nm", "B_435nm", "C_460nm", "D_485nm", "E_510nm", "F_535nm",
            "G_560nm", "H_585nm", "R_610nm", "I_645nm", "S_680nm", "J_705nm",
            "T_730nm", "U_760nm", "V_810nm", "W_860nm", "K_900nm", "L_940nm"
        ]

    def calibrate(self, raw_data):
        """ Guarda la medida actual como referencia (Blanco/I0) """
        try:
            # Convertimos el diccionario a un array ordenado de numpy
            vector = [raw_data.get(ch, 1.0) for ch in self.channels]
            self.baseline = np.array(vector, dtype=float)
            print("[CEREBRO] Calibración realizada. Nueva línea base guardada.")
            return True
        except Exception as e:
            print(f"[CEREBRO] Error calibrando: {e}")
            return False

    def get_absorbance(self, raw_data):
        """ Calcula A = -log10(Muestra / Referencia) """
        if self.baseline is None:
            # Si no hay calibración, devolvemos raw pero avisamos
            return raw_data, False

        try:
            sample = np.array([raw_data.get(ch, 1.0) for ch in self.channels], dtype=float)
            
            # Evitamos división por cero o logaritmos de negativos
            # Añadimos un valor epsilon muy pequeño (1e-6)
            ratio = (sample + 1e-6) / (self.baseline + 1e-6)
            absorbance = -np.log10(ratio)
            
            # Reconstruimos el diccionario con los valores procesados
            abs_data = {self.channels[i]: round(absorbance[i], 4) for i in range(len(self.channels))}
            return abs_data, True
        except Exception as e:
            print(f"[CEREBRO] Error calculando absorbancia: {e}")
            return raw_data, False

    def update_pca(self, abs_data):
        """
        Entrena el PCA dinámicamente con los datos que van llegando.
        Necesitamos acumular al menos 5-10 muestras para que funcione bien.
        """
        vector = [abs_data.get(ch, 0) for ch in self.channels]
        self.history.append(vector)
        
        # Mantenemos un buffer máximo de 200 muestras para que sea ágil
        if len(self.history) > 200:
            self.history.pop(0)
            
        # Si tenemos suficientes datos, re-entrenamos el mapa
        if len(self.history) > 5:
            try:
                self.pca_model.fit(self.history)
                self.is_fitted = True
            except Exception as e:
                print(f"[CEREBRO] Error entrenando PCA: {e}")

    def get_coords(self, abs_data):
        """ Proyecta el espectro actual en el mapa 2D """
        if not self.is_fitted:
            return 0.0, 0.0
        
        try:
            vector = np.array([abs_data.get(ch, 0) for ch in self.channels]).reshape(1, -1)
            coords = self.pca_model.transform(vector)
            return round(coords[0][0], 3), round(coords[0][1], 3) # PC1, PC2
        except:
            return 0.0, 0.0