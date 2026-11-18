"""
Headless tests for Streamlit app with AppTest.
"""
import os
import unittest
import pandas as pd
from streamlit.testing.v1 import AppTest

APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))


class TestAppHeadless(unittest.TestCase):
    def test_app_loads(self):
        at = AppTest.from_file(APP_PATH)
        at.run()
        self.assertFalse(at.exception)

    def test_app_manual_entry_flow(self):
        at = AppTest.from_file(APP_PATH)
        # Select "Manual entry" mode
        at.run()
        # Find input mode segmented control
        at.segmented_control[0].set_value("Manual entry")
        at.run()
        self.assertFalse(at.exception)

        # Populate manual_df in session_state with a test database
        test_df = pd.DataFrame([{
            "Database": "db_test",
            "Provider": "AWS",
            "Region": "us-east-1",
            "Engine": "Non-vector",
            "Tenancy": "Shared",
            "Environment": "Production",
            "Data size (GB)": 20.0,
            "Write tps": 5000.0,
            "Read tps": 1000.0,
        }])
        at.session_state["manual_df"] = test_df
        # Find the Calculate button in the manual entry form and click it
        if len(at.button) > 0:
            for b in at.button:
                if "Calculate" in b.label:
                    b.click()
                    break
        at.run()
        self.assertFalse(at.exception)

    def test_app_throughput_model_selection(self):
        at = AppTest.from_file(APP_PATH)
        at.run()
        at.segmented_control[0].set_value("Manual entry")
        at.run()

        # Switch workload to Key-Value
        # Find selectbox for workload
        at.selectbox[0].set_value("kv")
        at.run()
        self.assertFalse(at.exception)

        # Set manual df
        test_df = pd.DataFrame([{
            "Database": "db_test",
            "Provider": "AWS",
            "Region": "us-east-1",
            "Engine": "Non-vector",
            "Tenancy": "Shared",
            "Environment": "Production",
            "Data size (GB)": 20.0,
            "Write tps": 5000.0,
            "Read tps": 1000.0,
        }])
        at.session_state["manual_df"] = test_df
        for b in at.button:
            if "Calculate" in b.label:
                b.click()
                break
        at.run()
        self.assertFalse(at.exception)


    def test_app_configuration_panel_structure(self):
        at = AppTest.from_file(APP_PATH)
        at.run()
        self.assertFalse(at.exception)

        # Check expander exists and is expanded by default
        self.assertTrue(len(at.expander) > 0)
        self.assertTrue(at.expander[0].proto.expanded)
        self.assertIn("Configuration", at.expander[0].label)

        # Check segmented controls: Data source and TPS basis
        self.assertEqual(len(at.segmented_control), 2)
        self.assertEqual(at.segmented_control[0].value, "Upload file")
        self.assertEqual(at.segmented_control[1].value, "P90")

        # Check number inputs for capacity
        self.assertEqual(len(at.number_input), 2)
        self.assertEqual(at.number_input[0].value, 100.0)
        self.assertEqual(at.number_input[1].value, 100.0)

        # Check toggle for isolate production databases
        self.assertEqual(len(at.toggle), 1)
        self.assertTrue(at.toggle[0].value)

    def test_app_tps_basis_and_capacity_changes(self):
        at = AppTest.from_file(APP_PATH)
        at.run()
        at.segmented_control[0].set_value("Manual entry")
        at.run()

        # Change TPS basis to Maximum
        at.segmented_control[1].set_value("Maximum")
        # Change capacity settings
        at.number_input[0].set_value(80.0)
        at.number_input[1].set_value(50.0)
        at.toggle[0].set_value(False)
        at.run()
        self.assertFalse(at.exception)

        # Provide test data and calculate
        test_df = pd.DataFrame([{
            "Database": "db_test",
            "Provider": "AWS",
            "Region": "us-east-1",
            "Engine": "Non-vector",
            "Tenancy": "Shared",
            "Environment": "Production",
            "Data size (GB)": 20.0,
            "Write tps": 5000.0,
            "Read tps": 1000.0,
        }])
        at.session_state["manual_df"] = test_df
        for b in at.button:
            if "Calculate" in b.label:
                b.click()
                break
        at.run()
        self.assertFalse(at.exception)


if __name__ == "__main__":
    unittest.main()
