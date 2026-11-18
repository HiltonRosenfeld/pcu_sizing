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
        # Select "Enter data on screen" mode
        at.run()
        # Find input mode radio (first radio in card_input)
        at.radio[0].set_value("Enter data on screen")
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
        at.radio[0].set_value("Enter data on screen")
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


if __name__ == "__main__":
    unittest.main()
