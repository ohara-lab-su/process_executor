"""Shared Sphinx configuration for process-executor."""
from pathlib import Path
import os
import sys

DOCS_DIR = Path(__file__).resolve().parent
ROOT_DIR = DOCS_DIR.parent
sys.path.insert(0, str(ROOT_DIR / "src"))
project = "process-executor"
root_doc = "index"
language = os.environ.get("DOCS_LANGUAGE", "en")
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
extensions = ["sphinx.ext.autodoc", "sphinx.ext.autosummary", "sphinx.ext.napoleon", "sphinx.ext.viewcode", "myst_parser"]
autosummary_generate = True
autodoc_default_options = {"members": True, "undoc-members": True, "show-inheritance": True}
autodoc_typehints = "description"
autodoc_preserve_defaults = True
myst_enable_extensions = ["deflist", "colon_fence", "linkify"]
myst_linkify_fuzzy_links = True
myst_heading_anchors = 3
html_theme = "sphinx_rtd_theme"
html_theme_options = {"collapse_navigation": False, "navigation_depth": 4, "titles_only": False}
exclude_patterns = ["_build", "_work"]
