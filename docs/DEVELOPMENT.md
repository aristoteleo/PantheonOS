# Documentation Development Guide

This guide helps you develop and maintain the Pantheon Agents documentation with live reload capabilities.

## 🚀 Quick Start - Development Mode

All methods use your current Python environment (no virtual environment created).

### Option 1: Using Make (Recommended)
```bash
cd docs
make dev
# Opens http://localhost:8080 with live reload
```

### Option 2: Quick Script (Simplest)
```bash
cd docs
./quick_dev.sh
# Minimal setup, starts server immediately
```

### Option 3: Shell Script with Full Setup
```bash
cd docs
./dev_server.sh
# Shows environment info and installs dependencies
```

### Option 4: Direct Command
```bash
cd docs
sphinx-autobuild source build/html --host 127.0.0.1 --port 8080
```

## 📦 First Time Setup

If you haven't installed the documentation dependencies:

```bash
cd docs
make install  # or pip install -r requirements.txt
make dev
```

Or use the combined command:
```bash
make dev-install  # Installs deps and starts server
```

## 📝 Development Workflow

1. **Start Development Server**
   ```bash
   make dev
   ```
   - Automatically rebuilds on file changes
   - Browser auto-refreshes
   - Shows build errors in browser

2. **Edit Documentation**
   - Modify `.rst` or `.md` files in `source/`
   - Changes appear instantly in browser
   - No manual rebuild needed

3. **Preview Changes**
   - Open http://localhost:8080
   - Navigate to edited pages
   - Check both light and dark modes

## 🛠️ Configuration Options

### Environment Variables
```bash
# Custom host (default: 127.0.0.1)
export DOC_HOST=0.0.0.0

# Custom port (default: 8080)
export DOC_PORT=8888

# Build delay in seconds (default: 5)
export DOC_DELAY=2

# Run with custom settings
./dev_server.sh
```

### Sphinx-autobuild Options
```bash
# Watch additional directories
sphinx-autobuild source build/html --watch ../pantheon

# Ignore specific patterns
sphinx-autobuild source build/html --ignore "*.tmp"

# Custom rebuild delay
sphinx-autobuild source build/html --delay 2

# Open browser automatically
sphinx-autobuild source build/html --open-browser
```

## 📁 File Watching

The development server watches:
- All files in `source/` directory
- Python source files in `../pantheon/` (for autodoc)
- Configuration files (`conf.py`, etc.)

Ignored patterns:
- `*.pyc` - Python cache files
- `*.swp` - Vim swap files
- `*~` - Backup files
- `.git/*` - Git directory
- `build/*` - Build output

## 🎨 Live Editing Tips

### 1. Structure Changes
When adding new pages:
```rst
.. toctree::
   :maxdepth: 2
   
   new_page
```
The navigation updates immediately.

### 2. Theme Customization
Edit `_static/custom.css`:
```css
/* Changes appear instantly */
.custom-class {
    color: #1e88e5;
}
```

### 3. Code Examples
Test syntax highlighting:
```python
# Live preview of code blocks
def example():
    """See changes immediately"""
    pass
```

### 4. Dark Mode Testing
Toggle system theme to test both modes:
- macOS: System Preferences → Appearance
- Windows: Settings → Personalization → Colors
- Linux: Depends on desktop environment

## 🔍 Debugging

### Check Build Errors
Errors appear in:
1. Terminal output
2. Browser (red banner at top)
3. Browser console

### Common Issues

**Port Already in Use**
```bash
# Find process using port
lsof -i :8080
# Kill process or use different port
export DOC_PORT=8888
```

**Import Errors for Autodoc**
```bash
# Ensure pantheon is importable
cd docs
python -c "import sys; sys.path.insert(0, '..'); import pantheon"
```

**Missing Dependencies**
```bash
pip install -r requirements.txt
```

## 🏗️ Build for Production

After development, build final version:
```bash
# Clean build
make clean
make html

# Check for warnings
make html 2>&1 | grep -i warning

# Validate links
make linkcheck
```

## 🔧 Advanced Development

### Multiple Configurations
Create `source/conf_dev.py`:
```python
# Development-specific settings
from conf import *

# Faster builds
autodoc_mock_imports = ['heavy_dependency']
html_theme_options['announcement'] = "🚧 Development Build"
```

Run with custom config:
```bash
sphinx-autobuild -c source/conf_dev.py source build/html
```

### Custom Watch Patterns
Create `.sphinx-autobuild.ini`:
```ini
[autobuild]
watch = ../pantheon
watch = ../examples
ignore = *.log
ignore = *.tmp
delay = 3
```

### Integration with IDEs

**VS Code**
- Install "reStructuredText" extension
- Live preview in editor
- Syntax validation

**PyCharm**
- Built-in reStructuredText support
- Preview panel
- Quick documentation

## 📊 Performance Tips

1. **Incremental Builds**
   - Only changed files rebuild
   - Faster than full rebuild

2. **Selective Watching**
   ```bash
   # Only watch documentation files
   sphinx-autobuild source build --ignore "../pantheon/*"
   ```

3. **Parallel Building**
   ```bash
   # Use multiple cores
   sphinx-autobuild source build -j auto
   ```

## 🎉 Happy Documenting!

The live development mode makes documentation writing enjoyable and efficient. No more manual rebuilds!