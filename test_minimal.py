

from __future__ import annotations

import io
import math
import os
import platform
import resource
import socket
import sys
import time
import tracemalloc

import pandas as pd
import streamlit as st

from pcu_sizing.catalog import (
    CATALOG,
    HOURS_PER_MONTH,
    PCU_HARD_CAP,
    PCU_TARGET,
    UTIL_CEILING,
    UTIL_CEILING_HEADROOM,
    DbDemand,
    bucket_databases,
)
import pcu_sizing.engine as engine
from pcu_sizing.engine import (
    PcuGroup,
    all_partitions,
    optimise_bucket,
    run_all_variants,
)

# Set default EXACT_THRESHOLD to 8 in test_app
engine.EXACT_THRESHOLD = 8
from pcu_sizing.exporter import build_excel
from pcu_sizing.importer import parse_excel, parse_manual

st.set_page_config(
    page_title="PCU Sizing — Diagnostic Test App",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 PCU Sizing — Diagnostic & Debug App")
st.caption("Use this stripped-down app to diagnose issues on Streamlit Cloud.")


st.write("Hello")
