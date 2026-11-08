"""Explicit new index representation and original columns source closures."""
from pathlib import Path
import hashlib,json
from .agent_protocol import ModelError
from .followup_scope_columns_binding import verify_inputs as verify_production_inputs
HERE=Path(__file__).resolve().parents[2]
MANIFEST='schemas/followup-index-columns-bindings.json'
EXPECTED='dde49ef0a3010ce017c98dac84dab198ac533f21c9639710cac6913b13833de8'
def verify_inputs():
    try:
        manifest=HERE/MANIFEST
        if manifest.is_symlink() or hashlib.sha256(manifest.read_bytes()).hexdigest()!=EXPECTED:raise ValueError()
        for name,wanted in json.loads(manifest.read_text(encoding='utf-8')).items():
            value=HERE/name
            if value.is_symlink() or value.resolve()!=HERE.resolve()/name or hashlib.sha256(value.read_bytes()).hexdigest()!=wanted:raise ValueError()
        verify_production_inputs()
    except (OSError,ValueError,TypeError):raise ModelError('not_authorized',request_sent=False) from None
    return EXPECTED
