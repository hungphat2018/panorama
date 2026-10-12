"""Evaluation harness for the film-analysis content model: methods, metrics, adapters.

The test set (clips, labels, manifest) lives in spikes/film-analysis/testset/; this package reads it through
testset_common. Importing fa_eval puts that folder on sys.path (python -I does not add any folder by itself).
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
EVAL_DIR = _HERE.parent
FILM_ANALYSIS_DIR = EVAL_DIR.parent
TESTSET_DIR = FILM_ANALYSIS_DIR / "testset"
REPO = FILM_ANALYSIS_DIR.parents[1]
MODELS_DIR = REPO / "tools" / "models"

for _p in (str(TESTSET_DIR),):
    if _p not in sys.path:
        sys.path.insert(0, _p)
