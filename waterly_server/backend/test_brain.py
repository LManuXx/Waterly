import unittest
import numpy as np
import os
from brain import SpectralBrain, MODEL_FILE

class TestSpectralBrain(unittest.TestCase):

    def setUp(self):
        # Asegurarnos de que no haya un modelo guardado antes de cada test para que sea un entorno limpio
        if os.path.exists(MODEL_FILE):
            os.remove(MODEL_FILE)
        self.brain = SpectralBrain()

    def tearDown(self):
        # Limpiar al finalizar
        if os.path.exists(MODEL_FILE):
            os.remove(MODEL_FILE)

    def test_calibration(self):
        # Simular datos raw de agua limpia (I0)
        # 18 canales
        sample_1 = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        sample_2 = {wl: 49000.0 for wl in self.brain._get_wavelengths()}
        
        success = self.brain.calibrate([sample_1, sample_2])
        self.assertTrue(success)
        self.assertIsNotNone(self.brain.baseline)
        self.assertEqual(len(self.brain.baseline), 18)
        self.assertTrue(np.all(self.brain.baseline > 0))

    def test_get_absorbance_snv(self):
        # Primero calibrar
        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        # Simular una muestra con menor intensidad (absorbe luz)
        test_sample = {wl: 25000.0 for wl in self.brain._get_wavelengths()}
        
        abs_dict, success, abs_snv = self.brain.get_absorbance(test_sample)
        
        self.assertTrue(success)
        self.assertEqual(len(abs_dict), 18)
        self.assertIsNotNone(abs_snv)
        self.assertEqual(len(abs_snv), 18)

    def test_training_and_prediction(self):
        # Generar datos sintéticos con una correlación lineal para que el PLS funcione bien
        np.random.seed(42)
        
        # Simular 10 muestras con concentraciones del 1 al 10
        # SNV es un array de 18 elementos
        for i in range(1, 11):
            concentration = float(i)
            # Simular un patrón que PLS pueda relacionar con la concentración
            # Por ejemplo, un patrón base + ruido + un multiplicador relacionado con la concentración
            base_pattern = np.linspace(0, 1, 18)
            snv_data = base_pattern * concentration + np.random.normal(0, 0.1, 18)
            
            self.brain.add_training_sample(snv_data, concentration)
            
        self.assertEqual(len(self.brain.dataset_X), 10)
        self.assertEqual(len(self.brain.dataset_y), 10)
        
        # Entrenar el modelo
        success = self.brain.train_model()
        self.assertTrue(success)
        self.assertTrue(self.brain.is_trained)
        self.assertIsNotNone(self.brain.model)
        
        # Predecir sobre un dato nuevo similar a una concentración conocida
        test_concentration = 5.5
        test_snv = np.linspace(0, 1, 18) * test_concentration + np.random.normal(0, 0.1, 18)
        
        prediction = self.brain.predict(test_snv)
        
        self.assertIsNotNone(prediction)
        # Comprobar que la predicción está razonablemente cerca del valor esperado
        # Damos un margen de error (ej. ±1.0)
        self.assertTrue(abs(prediction - test_concentration) < 1.0, 
                        f"Predicción {prediction} muy alejada de {test_concentration}")

if __name__ == '__main__':
    unittest.main()
