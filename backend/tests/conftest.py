import sys
from pathlib import Path

# backend/tests/ lives inside the package dir; make plain `import telemetry` etc. work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
