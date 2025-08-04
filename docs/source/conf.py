# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
sys.path.insert(0, os.path.abspath('../../'))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'Pantheon Agents'
copyright = '2025, Pantheon Team'
author = 'Pantheon Team'
release = '0.1.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx.ext.autosummary',
    'sphinx_copybutton',
    'sphinx_design',
    'myst_nb',
    'sphinx.ext.linkcode',
    'sphinx_togglebutton',
    'sphinx_thebe',
    'sphinxcontrib.mermaid',
]

# Use a light syntax highlighting style
pygments_style = 'default'
pygments_dark_style = 'monokai'

# Generate autosummary pages
autosummary_generate = True

templates_path = ['_templates']
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_book_theme'
html_static_path = ['_static']
html_title = "Pantheon"
html_logo = "_static/pantheon.png"  
html_favicon = "_static/pantheon.png"
html_css_files = ['custom.css']
html_js_files = [
    ('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js', {'loading_method': 'async'}),
    'mermaid-init.js',
]

# Sphinx Book Theme options
html_theme_options = {
    "repository_url": "https://github.com/aristoteleo/pantheon-agents",
    "use_repository_button": True,
    "use_edit_page_button": True,
    "use_source_button": True,
    "use_issues_button": True,
    "use_download_button": True,
    "path_to_docs": "pantheon-agents/docs/source",
    "repository_branch": "main",
    "home_page_in_toc": True,
    "show_navbar_depth": 2,
    "collapse_navigation": True,  # Collapse navigation by default
    "logo": {
        "image_light": "_static/pantheon.png",
        "image_dark": "_static/pantheon.png",
        "text": "Pantheon",
    },
    "navigation_with_keys": True,
}

# -- Extension configuration -------------------------------------------------

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = True
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# Autodoc settings
autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__'
}

# MyST settings
myst_enable_extensions = [
    "amsmath",
    "colon_fence",
    "deflist",
    "dollarmath",  
    "fieldlist",
    "html_admonition",
    "html_image",
    "linkify",
    "replacements",
    "smartquotes",
    "strikethrough",
    "substitution",
    "tasklist",
]

# Ensure MyST-NB parses markdown correctly
myst_update_mathjax = False

# Intersphinx mapping
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'pandas': ('https://pandas.pydata.org/docs/', None),
}

# Copy button settings
copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

# MyST-NB settings
nb_execution_mode = "off"
nb_execution_timeout = 60

# Mermaid settings
mermaid_version = "10.9.0"
mermaid_init_js = "mermaid.initialize({startOnLoad:true, theme: 'default'});"
mermaid_output_format = 'raw'
mermaid_embed_js = True

# Source suffix
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'myst-nb',
    '.ipynb': 'myst-nb',
}

# Source code linking
def linkcode_resolve(domain, info):
    if domain != 'py':
        return None
    if not info['module']:
        return None
    
    filename = info['module'].replace('.', '/')
    return f"https://github.com/aristoteleo/pantheon-agents/blob/main/pantheon/{filename}.py"

# Use master_doc for index
master_doc = 'index'