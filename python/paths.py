"""Shared project paths. Override with DATA_ROOT and MODELS_DIR env vars."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_ROOT = Path(__file__).resolve().parent

DATA_ROOT = Path(os.environ.get('DATA_ROOT', PROJECT_ROOT / 'data'))
RAW_ROOT = DATA_ROOT / 'raw'
PROCESSED_ROOT = DATA_ROOT / 'processed'
MODELS_DIR = Path(os.environ.get('MODELS_DIR', PROJECT_ROOT / 'models'))

CAPTURE_SCRIPT = PYTHON_ROOT / 'capture' / 'capture_csi_binary.py'
VALIDATE_SCRIPT = PYTHON_ROOT / 'collect' / 'validate_csi_captures.py'
