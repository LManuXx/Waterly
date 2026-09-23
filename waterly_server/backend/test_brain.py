import unittest
import numpy as np
import os
from brain import SpectralBrain, MODEL_FILE

class TestSpectralBrain(unittest.TestCase):

    def setUp(self):
        import brain
        brain.MODEL_FILE = "/tmp/test_waterly_model.pkl"
        if os.path.exists(brain.MODEL_FILE):
            os.remove(brain.MODEL_FILE)
        self.brain = brain.SpectralBrain()

    def tearDown(self):
        import brain
        if os.path.exists(brain.MODEL_FILE):
            os.remove(brain.MODEL_FILE)

    def test_calibration(self):
        sample_1 = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        sample_2 = {wl: 49000.0 for wl in self.brain._get_wavelengths()}

        success = self.brain.calibrate([sample_1, sample_2])
        self.assertTrue(success)
        self.assertIsNotNone(self.brain.baseline)
        self.assertEqual(len(self.brain.baseline), 18)
        self.assertTrue(np.all(self.brain.baseline > 0))

    def test_get_absorbance_snv(self):
        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        test_sample = {wl: 25000.0 for wl in self.brain._get_wavelengths()}

        abs_dict, success, abs_snv = self.brain.get_absorbance(test_sample)

        self.assertTrue(success)
        self.assertEqual(len(abs_dict), 18)
        self.assertIsNotNone(abs_snv)
        self.assertEqual(len(abs_snv), 18)

    def test_savitzky_golay_filtering(self):
        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        test_sample = {wl: 25000.0 for wl in self.brain._get_wavelengths()}
        abs_dict, success, abs_snv = self.brain.get_absorbance(test_sample)

        self.assertTrue(success)
        self.assertIsNotNone(abs_snv)
        self.assertEqual(len(abs_snv), 18)

    def test_training_and_prediction_deviation(self):
        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        self.brain.model_type = "DEVIATION"
        snv_data = np.ones(18) * 0.5
        self.brain.add_training_sample(snv_data, 0.0)

        success = self.brain.train_model()
        self.assertTrue(success)
        self.assertTrue(self.brain.is_trained)
        self.assertIn("type", self.brain.metrics)
        self.assertEqual(self.brain.metrics["type"], "DEVIATION")

        prediction = self.brain.predict(snv_data)
        self.assertIsNotNone(prediction)

    def test_training_and_prediction_plsr(self):
        np.random.seed(42)

        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        self.brain.model_type = "PLSR"
        for i in range(1, 11):
            concentration = float(i)
            base_pattern = np.linspace(0, 1, 18)
            snv_data = base_pattern * concentration + np.random.normal(0, 0.1, 18)
            self.brain.add_training_sample(snv_data, concentration)

        self.assertEqual(len(self.brain.dataset_X), 10)

        success = self.brain.train_model()
        self.assertTrue(success)
        self.assertTrue(self.brain.is_trained)
        self.assertIsNotNone(self.brain.model)

        # Verify metrics
        self.assertIn("r2_train", self.brain.metrics)
        self.assertIn("rmsecv", self.brain.metrics)
        self.assertIn("cv_results", self.brain.metrics)
        self.assertGreater(self.brain.metrics["r2_train"], 0.5)

        # Verify prediction
        test_concentration = 5.5
        test_snv = np.linspace(0, 1, 18) * test_concentration + np.random.normal(0, 0.1, 18)

        prediction = self.brain.predict(test_snv)
        self.assertIsNotNone(prediction)
        self.assertGreaterEqual(prediction, 0.0)
        self.assertTrue(abs(prediction - test_concentration) < 2.0,
                        f"Prediccion {prediction} muy alejada de {test_concentration}")

    def test_loocv_selects_optimal_components(self):
        np.random.seed(42)

        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        self.brain.model_type = "PLSR"
        for i in range(1, 6):
            concentration = float(i * 5)
            base_pattern = np.linspace(0, 1, 18)
            snv_data = base_pattern * concentration + np.random.normal(0, 0.05, 18)
            self.brain.add_training_sample(snv_data, concentration)

        success = self.brain.train_model()
        self.assertTrue(success)
        self.assertIn("n_components", self.brain.metrics)
        self.assertIn("rmsecv", self.brain.metrics)
        self.assertGreater(self.brain.metrics["rmsecv"], 0)

    def test_outlier_detection(self):
        np.random.seed(42)

        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        self.brain.model_type = "PLSR"
        for i in range(1, 8):
            concentration = float(i * 5)
            base_pattern = np.linspace(0, 1, 18)
            snv_data = base_pattern * concentration + np.random.normal(0, 0.05, 18)
            self.brain.add_training_sample(snv_data, concentration)

        success = self.brain.train_model()
        self.assertTrue(success)
        self.assertIn("t2_scores", self.brain.metrics)
        self.assertIn("q_residuals", self.brain.metrics)
        self.assertIn("outliers", self.brain.metrics)

    def test_model_persistence(self):
        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        self.brain.model_type = "DEVIATION"
        snv_data = np.ones(18) * 0.5
        self.brain.add_training_sample(snv_data, 0.0)
        self.brain.train_model()

        metrics_before = self.brain.metrics.copy()
        self.brain.save_brain()

        brain2 = SpectralBrain()
        self.assertTrue(brain2.is_trained)
        self.assertEqual(brain2.metrics, metrics_before)

    def test_prediction_clamped_non_negative(self):
        np.random.seed(42)

        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])

        self.brain.model_type = "PLSR"
        for i in range(1, 6):
            concentration = float(i * 10)
            base_pattern = np.linspace(0, 1, 18)
            snv_data = base_pattern * concentration + np.random.normal(0, 0.1, 18)
            self.brain.add_training_sample(snv_data, concentration)

        self.brain.train_model()

        prediction = self.brain.predict(np.zeros(18))
        self.assertIsNotNone(prediction)
        self.assertGreaterEqual(prediction, 0.0)

    def test_ensemble_and_consensus(self):
        np.random.seed(42)
        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])
        self.brain.model_type = "PLSR"
        for i in range(1, 11):
            concentration = float(i)
            snv_data = np.linspace(0, 1, 18) * concentration + np.random.normal(0, 0.05, 18)
            self.brain.add_training_sample(snv_data, concentration)

        self.assertTrue(self.brain.train_model())
        self.assertIn("PLSR", self.brain.models)
        self.assertIn("Ridge", self.brain.models)
        self.assertIn("SVR", self.brain.models)
        self.assertIn("RF", self.brain.models)
        self.assertIn("ensemble", self.brain.metrics)
        self.assertIn("rpd", self.brain.metrics)

        test_snv = np.linspace(0, 1, 18) * 5.0
        result = self.brain.predict_result(test_snv)
        self.assertIsNotNone(result)
        self.assertEqual(result["pred_mg_l"], result["pred_consensus"])
        self.assertIsInstance(result["pred_by_model"], dict)
        self.assertIsNotNone(result["pred_by_model"]["PLSR"])
        self.assertIsNotNone(result["pred_by_model"]["Ridge"])
        self.assertIn(result["pred_agreement"], ("ok", "warn", "disagree", "single"))

        # Consenso unitario: descarta outlier
        c = SpectralBrain.consensus_predictions(
            {"PLSR": 10.0, "Ridge": 10.5, "SVR": 50.0, "RF": None},
            {"PLSR": 1.0, "Ridge": 1.0, "SVR": 1.0},
        )
        self.assertIn("SVR", c["discarded_models"])
        self.assertAlmostEqual(c["pred_consensus"], 10.25, places=2)

    def test_ensemble_min_models_by_n(self):
        np.random.seed(0)
        base_sample = {wl: 50000.0 for wl in self.brain._get_wavelengths()}
        self.brain.calibrate([base_sample])
        self.brain.model_type = "PLSR"
        for i in range(1, 4):
            snv_data = np.linspace(0, 1, 18) * float(i) + np.random.normal(0, 0.02, 18)
            self.brain.add_training_sample(snv_data, float(i * 10))
        self.assertTrue(self.brain.train_model())
        self.assertEqual(set(self.brain.models.keys()), {"PLSR", "Ridge"})


if __name__ == '__main__':
    unittest.main()
