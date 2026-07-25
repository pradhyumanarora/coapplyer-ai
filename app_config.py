"""Runtime config loader.

Loads local `config.py` when present; otherwise falls back to `config.example.py`.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def _load_module_from_path(name: str, path: Path) -> ModuleType:
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec for {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_config_module() -> ModuleType:
    root = Path(__file__).resolve().parent
    local_config = root / "config.py"
    example_config = root / "config.example.py"

    if local_config.exists():
        return _load_module_from_path("config", local_config)

    if example_config.exists():
        return _load_module_from_path("config_example", example_config)

    raise FileNotFoundError(
        "No config file found. Create config.py or add config.example.py."
    )


_cfg = _load_config_module()

# Re-export all uppercase config fields.
for _name in dir(_cfg):
    if _name.isupper():
        globals()[_name] = getattr(_cfg, _name)
