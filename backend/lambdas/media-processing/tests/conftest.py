import sys
from pathlib import Path


MEDIA_PROCESSING_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEDIA_PROCESSING_ROOT))
