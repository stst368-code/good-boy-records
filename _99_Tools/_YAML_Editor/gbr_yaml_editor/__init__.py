"""Explicit source maintenance, separate from generation's read-only imports."""
from pathlib import Path
import sys

# Share the strict validator; the generation writer retains its narrower permissions.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / '_02_Generation'))
