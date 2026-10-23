"""Central settings. Values can be overridden in .env or the environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv(path):
    """Minimal .env reader so the project has no third-party dependencies."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

# Simulated data by default. Set SAMPLE_MODE=false to call the real vision model.
SAMPLE_MODE = os.environ.get("SAMPLE_MODE", "true").strip().lower() not in ("false", "0", "no")

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NIM_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = "meta/llama-3.2-11b-vision-instruct"
NIM_TIMEOUT_S = 60
NIM_MAX_IMAGE_B64 = 180_000  # inline base64 limit for the hosted NIM endpoint

# Whole run must finish in under 10 minutes; leave headroom.
RUN_BUDGET_S = 480

# Verification thresholds
VERIFY_ACCEPT = 0.70        # confidence >= this -> verified
VERIFY_REJECT = 0.40        # confidence < this -> rejected; in between -> held for review
DUPLICATE_RADIUS_M = 75     # reports this close together...
DUPLICATE_WINDOW_H = 48     # ...within this many hours are treated as the same dump

# Evaluator / dispatch loop
MAX_REVISIONS = 3
# LLM-written explanation of each rejection (text only - never affects pass/fail).
# Used whenever NVIDIA_API_KEY is set; otherwise a rule-based template is written instead.
EVALUATOR_LLM_EXPLAIN = os.environ.get("EVALUATOR_LLM_EXPLAIN", "true").strip().lower() not in ("false", "0", "no")
EVALUATOR_LLM_TIMEOUT_S = 30

# Risk thresholds
RISK_CONFIDENCE_FLOOR = 0.85  # verified reports below this still need a human look
RISK_LOAD_CEILING = 0.90      # routes loaded above this share of capacity are "tight"
