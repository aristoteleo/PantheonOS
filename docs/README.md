# Pantheon Agents Documentation

This directory contains the documentation for Pantheon Agents, built with Sphinx and using the modern Sphinx Book Theme.

## Features

- 🌓 **Dark/Light Mode**: Automatic theme switching based on system preferences
- 📱 **Responsive Design**: Works great on all devices
- 🔍 **Full-Text Search**: Quick navigation through documentation
- 📚 **Comprehensive Coverage**: From getting started to advanced topics
- 🎨 **Modern UI**: Clean, professional appearance with Sphinx Book Theme
- 📊 **Mermaid Diagrams**: Support for flowcharts and diagrams
- 🔗 **Interactive Examples**: Grid-based navigation with card layouts

## Quick Start

### Building Documentation

```bash
# Install dependencies
make install

# Build HTML documentation
make html

# Start development server with auto-reload
make dev
```

The development server will start at http://localhost:8080

## Documentation Structure

```
docs/
├── source/
│   ├── _static/          # Static assets (CSS, JS, images)
│   │   ├── custom.css    # Custom styling
│   │   ├── pantheon.png  # Logo
│   │   └── mermaid-init.js
│   ├── _templates/       # Custom templates
│   ├── agent/           # Agent documentation
│   ├── api/             # API reference documentation
│   ├── endpoint_chatroom/ # ChatRoom service docs
│   ├── examples/        # Individual example pages with cards
│   │   ├── index.rst    # Card-based example gallery
│   │   ├── search_bot.rst
│   │   ├── sequential_team.rst
│   │   └── ...
│   ├── team/            # Team collaboration patterns
│   ├── toolsets/        # Toolset documentation
│   ├── conf.py          # Sphinx configuration
│   ├── index.md         # Main documentation index
│   ├── installation.rst
│   ├── quickstart.rst
│   └── concepts.md
├── os/                  # Design notes: the PantheonOS core (nodes, controller, CLI, orchestration)
├── apps/                # Design notes: Apps (lifecycle, gateway, dependencies, hosting, Store)
├── agent/               # Design notes: Pantheon-Agent (core, release, repository, migration)
├── build/               # Built documentation (git-ignored)
├── requirements.txt     # Documentation dependencies
├── Makefile            # Build automation
└── README.md           # This file
```

`os/`, `apps/` and `agent/` hold the design notes for the three parts of the
repository; they are not part of the Sphinx site.

## Key Features

### Grid-Based Navigation

The documentation uses sphinx-design for creating responsive grid layouts with cards:

```rst
.. grid:: 2 2 3 4
   :gutter: 2

   .. grid-item-card::
      :img-top: ../_static/pantheon.png
      :link: example_page
      :link-type: doc
```

### Collapsible Sidebar

The sidebar navigation is configured to be collapsed by default with:
- `collapse_navigation: True`
- `show_navbar_depth: 1`

### Custom Styling

Custom CSS provides:
- Improved code block spacing
- Custom scrollbar styling
- Better sidebar navigation
- Enhanced grid card hover effects

## Building Documentation

### Prerequisites

```bash
# Using pip
pip install -r requirements.txt

# Or using make
make install
```

### Build Commands

```bash
# Build HTML documentation
make html

# Clean build directory
make clean

# Run development server (port 8080)
make dev

# Check for broken links
make linkcheck

# Build other formats
make latexpdf    # PDF via LaTeX
make epub        # EPUB format
make singlehtml  # Single HTML page
```

## Development Workflow

1. **Start the development server**:
   ```bash
   make dev
   ```

2. **Edit documentation files**:
   - RST files for structured content
   - Markdown files (with MyST) for simpler content
   - Update toctree directives when adding new pages

3. **Preview changes**:
   - The dev server auto-reloads on file changes
   - Check responsive design and dark mode

4. **Test the build**:
   ```bash
   make clean html
   ```

## Adding New Documentation

### Creating a New Example

1. Create an RST file in `source/examples/`:
   ```rst
   Example Title
   =============
   
   Overview, features, code, usage instructions...
   ```

2. Add a card to `source/examples/index.rst`:
   ```rst
   .. grid-item-card::
      :img-top: ../_static/pantheon.png
      :link: your_example
      :link-type: doc
      
      **Your Example**
      ^^^
      Brief description
   ```

3. Update the toctree in `index.rst`

### Adding API Documentation

1. Create RST files in `source/api/`
2. Use autodoc directives for automatic API extraction
3. Add to the API index toctree

## Theme Configuration

The Sphinx Book Theme is configured in `conf.py`:

```python
html_theme_options = {
    "repository_url": "https://github.com/aristoteleo/pantheon-agents",
    "use_repository_button": True,
    "collapse_navigation": True,
    "show_navbar_depth": 1,
    # ... more options
}
```

## Extensions Used

- **sphinx.ext.autodoc**: Automatic API documentation
- **sphinx.ext.napoleon**: Google/NumPy docstring support
- **sphinx_design**: Grid layouts and cards
- **myst_nb**: Markdown and Jupyter notebook support
- **sphinx_copybutton**: Copy button for code blocks
- **sphinxcontrib.mermaid**: Diagram support

## Troubleshooting

### Common Issues

1. **Module import errors**: Ensure pantheon-agents is installed
2. **Missing dependencies**: Run `make install`
3. **Build warnings**: Check RST syntax and toctree references
4. **Scrollbar issues**: Custom CSS handles sidebar scrolling

### Environment Setup

The Makefile detects the conda environment. For manual builds:

```bash
conda activate pantheon
make html
```

## Contributing

1. Follow RST/Markdown best practices
2. Test builds locally before committing
3. Ensure all links work (`make linkcheck`)
4. Update toctrees when adding new pages
5. Maintain consistent formatting

## Deployment

Documentation can be deployed to:
- GitHub Pages (via GitHub Actions)
- Read the Docs (via webhook)
- Custom hosting (upload `build/html/`)

For Read the Docs, the `.readthedocs.yaml` file configures the build process.

## Resources

- [Sphinx Documentation](https://www.sphinx-doc.org/)
- [Sphinx Book Theme](https://sphinx-book-theme.readthedocs.io/)
- [MyST Parser](https://myst-parser.readthedocs.io/)
- [sphinx-design](https://sphinx-design.readthedocs.io/)