"""File viewer using prompt_toolkit full_screen.

Usage:
    /view <filepath>

Uses prompt_toolkit's native full-screen mode, which is the same
framework as the REPL, ensuring good compatibility.
"""

import asyncio
from pathlib import Path
from typing import List, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, ScrollablePane
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, TextArea
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles import Style

# Try to import pygments lexers
try:
    from pygments.lexers import get_lexer_for_filename, TextLexer
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


STYLE = Style.from_dict({
    'header': 'bg:#1e1e1e #61afef bold',
    'header.filename': '#e5c07b bold',
    'header.info': '#abb2bf',
    'footer': 'bg:#1e1e1e #abb2bf',
    'footer.key': '#61afef bold',
    'line-number': '#5c6370',
    'content': '#abb2bf',
})


class PTKFileViewer:
    """Full-screen file viewer using prompt_toolkit."""
    
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.lines: List[str] = []
        self.app: Optional[Application] = None
        
    def load_file(self) -> bool:
        """Load file contents"""
        try:
            content = self.file_path.read_text(encoding='utf-8')
            self.lines = content.splitlines()
            return True
        except UnicodeDecodeError:
            try:
                content = self.file_path.read_text(encoding='latin-1')
                self.lines = content.splitlines()
                return True
            except Exception:
                return False
        except Exception:
            return False
    
    def _create_layout(self) -> Layout:
        """Create the layout"""
        # Get file info
        file_size = self.file_path.stat().st_size
        size_str = f"{file_size:,} bytes" if file_size < 1024 else f"{file_size/1024:.1f} KB"
        
        # Header
        header_text = [
            ('class:header.filename', f' {self.file_path.name}'),
            ('class:header.info', f'  |  {len(self.lines)} lines  |  {size_str} '),
        ]
        header = Window(
            content=FormattedTextControl(header_text),
            height=1,
            style='class:header'
        )
        
        # Content - using TextArea for scrolling
        content_text = "\n".join(self.lines)
        
        # Try to get lexer for syntax highlighting
        lexer = None
        if PYGMENTS_AVAILABLE:
            try:
                pygments_lexer = get_lexer_for_filename(self.file_path.name)
                lexer = PygmentsLexer(type(pygments_lexer))
            except Exception:
                pass
        
        self.text_area = TextArea(
            text=content_text,
            read_only=True,
            scrollbar=True,
            lexer=lexer,
            line_numbers=True,
            wrap_lines=False,
        )
        
        # Footer
        footer_text = [
            ('class:footer.key', ' j/k'),
            ('class:footer', ':scroll  '),
            ('class:footer.key', 'g/G'),
            ('class:footer', ':top/bottom  '),
            ('class:footer.key', 'q/ESC'),
            ('class:footer', ':quit '),
        ]
        footer = Window(
            content=FormattedTextControl(footer_text),
            height=1,
            style='class:footer'
        )
        
        return Layout(
            HSplit([
                header,
                self.text_area,
                footer,
            ])
        )
    
    def _create_keybindings(self) -> KeyBindings:
        """Create key bindings"""
        kb = KeyBindings()
        
        def safe_exit(event):
            """Safely exit the app, ignoring if already exiting."""
            try:
                if not event.app._is_running:
                    return
                event.app.exit()
            except Exception:
                pass  # Already exiting
        
        @kb.add('q')
        @kb.add('Q')
        @kb.add('escape')
        def exit_(event):
            safe_exit(event)
        
        @kb.add('j')
        @kb.add('down')
        def scroll_down(event):
            self.text_area.buffer.cursor_down()
        
        @kb.add('k')
        @kb.add('up')
        def scroll_up(event):
            self.text_area.buffer.cursor_up()
        
        @kb.add('c-f')
        @kb.add(' ')
        def page_down(event):
            for _ in range(20):
                self.text_area.buffer.cursor_down()
        
        @kb.add('c-b')
        def page_up(event):
            for _ in range(20):
                self.text_area.buffer.cursor_up()
        
        @kb.add('g')
        def goto_top(event):
            self.text_area.buffer.cursor_position = 0
        
        @kb.add('G')
        def goto_bottom(event):
            self.text_area.buffer.cursor_position = len(self.text_area.text)
        
        @kb.add('c-c')
        def interrupt(event):
            safe_exit(event)
        
        return kb
    
    async def run_async(self) -> int:
        """Run the viewer asynchronously"""
        if not self.load_file():
            print(f"Error: Could not read file {self.file_path}")
            return 1
        
        self.app = Application(
            layout=self._create_layout(),
            key_bindings=self._create_keybindings(),
            style=STYLE,
            full_screen=True,
            mouse_support=True,
        )
        
        try:
            await self.app.run_async()
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1
    
    def run(self) -> int:
        """Run the viewer synchronously"""
        return asyncio.run(self.run_async())


async def view_file_ptk(file_path: str) -> dict:
    """View file using prompt_toolkit
    
    Args:
        file_path: Path to file to view
        
    Returns:
        dict with success status
    """
    path = Path(file_path)
    
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    
    if not path.is_file():
        return {"success": False, "error": f"Not a file: {file_path}"}
    
    try:
        viewer = PTKFileViewer(str(path.absolute()))
        exit_code = await viewer.run_async()
        
        return {
            "success": exit_code == 0,
            "exit_code": exit_code
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


# Entry point for direct execution
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        viewer = PTKFileViewer(sys.argv[1])
        sys.exit(viewer.run())
    else:
        print("Usage: python -m pantheon.repl.viewers.file_viewer_ptk <filepath>")
        sys.exit(1)
