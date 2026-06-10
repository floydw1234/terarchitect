import os
import sys
import types
import importlib.util
from pathlib import Path

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _load_embedding_model_module():
    package_name = "_hipporag_embedding_model_testpkg"
    module_name = f"{package_name}.embedding_model"
    module_path = Path(_BACKEND_DIR) / "hipporag_minimal" / "embedding_model" / "__init__.py"

    root_pkg = types.ModuleType(package_name)
    root_pkg.__path__ = []
    sys.modules[package_name] = root_pkg

    utils_pkg = types.ModuleType(f"{package_name}.utils")
    utils_pkg.__path__ = []
    sys.modules[utils_pkg.__name__] = utils_pkg

    logging_utils = types.ModuleType(f"{package_name}.utils.logging_utils")
    logging_utils.get_logger = lambda _name: types.SimpleNamespace(debug=lambda *a, **k: None)
    sys.modules[logging_utils.__name__] = logging_utils

    base_mod = types.ModuleType(f"{module_name}.base")
    base_mod.EmbeddingConfig = type("EmbeddingConfig", (), {})
    base_mod.BaseEmbeddingModel = type("BaseEmbeddingModel", (), {})
    sys.modules[base_mod.__name__] = base_mod

    openai_mod = types.ModuleType(f"{module_name}.OpenAI")
    openai_mod.OpenAIEmbeddingModel = type("OpenAIEmbeddingModel", (), {})
    sys.modules[openai_mod.__name__] = openai_mod

    spec = importlib.util.spec_from_file_location(
        module_name,
        module_path,
        submodule_search_locations=[str(module_path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, openai_mod.OpenAIEmbeddingModel


def test_minimal_hipporag_allows_bge_large_openai_compatible_model_name():
    module, openai_embedding_model = _load_embedding_model_module()

    assert module._get_embedding_model_class("bge-large") is openai_embedding_model


def test_minimal_hipporag_rejects_empty_embedding_model_name():
    module, _ = _load_embedding_model_module()

    with pytest.raises(ValueError, match="non-empty embedding model name"):
        module._get_embedding_model_class("")
