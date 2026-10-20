"""Interactive Notify Dialog for user approval.

Provides a full-screen dialog for notify_user with interrupt=True,
allowing users to review files and approve/continue.
"""

import shutil
import textwrap
from enum import Enum
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import List, Optional

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText, ANSI

from rich.markdown import Markdown
from rich.console import Console


class NotifyAction(Enum):
    """User action in notify dialog."""
    APPROVE = "approve"
    CONTINUE = "continue"
    REJECT = "reject"


@dataclass
class NotifyResult:
    """Result from notify dialog."""
    action: NotifyAction
    feedback: str = ""  # Only used for REJECT


STYLE = Style.from_dict({
    'dialog': 'bg:#1e1e1e',
    'dialog.border': '#61afef',
    'title': '#e5c07b bold',
    'message': '#abb2bf',
    'file-list': '#5c6370',
    'file-list.selected': '#61afef bold',
    'file-list.number': '#e06c75',
    'preview': '#abb2bf',
    'preview.title': '#98c379',
    'button': '#abb2bf',
    'button.selected': 'bg:#61afef #282c34 bold',
    'footer': '#5c6370',
    'footer.key': '#e5c07b bold',
})


class InteractiveNotifyDialog:
    """Interactive dialog for notify_user with file preview."""
    
    BUTTONS = ["Approve", "Continue Planning"]
    
    def __init__(self, message: str, paths):
        """Initialize dialog.
        
        Args:
            message: Notification message
            paths: List of file paths to review (can be malformed)
        """
        self.message = message
        
        # Robust path handling - silently ignore invalid paths
        self.paths = self._parse_paths(paths)
        
        # State
        self.selected_file_idx = 0
        self.selected_button_idx = 0
        self.scroll_offset = 0
        self.file_lines: List[str] = []
        self.result: Optional[NotifyResult] = None
        
        # Build UI - initialize app before loading file
        self.app: Optional[Application] = None
        self.show_line_numbers = False  # No line numbers in notify dialog
        
        # Load first file
        if self.paths:
            self._load_file(0)
    
    def _parse_paths(self, paths) -> List[Path]:
        """Robustly parse paths from potentially malformed input.
        
        Silently ignores:
        - Non-list inputs
        - Non-string items
        - Non-existent files
        - Any parsing errors
        
        Args:
            paths: Raw paths input (could be list, string, None, etc.)
            
        Returns:
            List of valid Path objects for existing files
        """
        result = []
        
        # Handle None
        if paths is None:
            return result
        
        # Handle string (single path or comma-separated)
        if isinstance(paths, str):
            # Try comma-separated first
            if ',' in paths:
                paths = [p.strip() for p in paths.split(',')]
            else:
                paths = [paths.strip()]
        
        # Handle non-iterable
        if not hasattr(paths, '__iter__'):
            return result
        
        # Process each path
        for p in paths:
            try:
                # Skip empty or non-string
                if not p or not isinstance(p, str):
                    continue
                
                path = Path(p.strip())
                
                # Only include existing files (not directories)
                if path.exists() and path.is_file():
                    result.append(path)
            except Exception:
                # Silently ignore any errors
                continue
        
        return result
    
    def _load_file(self, idx: int):
        """Load file content at index."""
        if idx < 0 or idx >= len(self.paths):
            return
        
        self.selected_file_idx = idx
        self.scroll_offset = 0
        
        path = self.paths[idx]
        try:
            content = path.read_text(encoding='utf-8')
            self.file_lines = content.splitlines()
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding='latin-1')
                self.file_lines = content.splitlines()
            except Exception:
                self.file_lines = ["<Unable to read file>"]
        except Exception as e:
            self.file_lines = [f"<Error: {e}>"]
        
        if self.app is not None:
            self.app.invalidate()
    
    def _get_visible_lines(self) -> int:
        """Number of visible lines in preview area.
        
        Dynamically calculated based on terminal height minus fixed UI elements.
        """
        term_height = shutil.get_terminal_size().lines
        
        # Subtract fixed UI elements:
        # - Frame borders: 2 lines (top + bottom)
        # - Message area: ~3 lines (estimate, could be more if long)
        # - File list: 1 line
        # - Separator: 1 line
        # - Preview title: 1 line
        # - Buttons: 1 line
        # - Footer: 1 line
        # - Extra padding: 2 lines
        fixed_lines = 12
        
        available = term_height - fixed_lines
        return max(5, available)  # Minimum 5 lines
    
    # Maximum lines for message area
    MAX_MESSAGE_LINES = 8
    
    def _render_markdown_to_ansi(self, text: str, width: int) -> str:
        """Render markdown text to ANSI string using Rich.
        
        Args:
            text: Markdown text to render
            width: Target width for rendering
            
        Returns:
            ANSI-formatted string
        """
        buffer = StringIO()
        console = Console(
            file=buffer, 
            force_terminal=True, 
            width=width,
            no_color=False
        )
        try:
            console.print(Markdown(text))
        except Exception:
            # Fallback to plain text
            console.print(text)
        
        return buffer.getvalue()
    
    def _get_message_text(self):
        """Get formatted message text with markdown rendering and height limit."""
        term_width = shutil.get_terminal_size().columns - 8  # Leave margin for frame
        width = max(40, term_width)
        
        # Render markdown to ANSI
        ansi_str = self._render_markdown_to_ansi(self.message, width)
        
        # Split into lines and limit height
        lines = ansi_str.split('\n')
        if len(lines) > self.MAX_MESSAGE_LINES:
            lines = lines[:self.MAX_MESSAGE_LINES - 1]
            lines.append('\x1b[2m... (message truncated)\x1b[0m')  # Dim gray
        
        # Add indent to each line
        indented_lines = ['  ' + line for line in lines]
        limited_ansi = '\n'.join(indented_lines)
        
        return ANSI(limited_ansi)
    
    def _get_file_list_text(self):
        """Get formatted file list text."""
        if not self.paths:
            return FormattedText([('class:file-list', '  (No files to review)')])
        
        items = []
        items.append(('class:file-list', '  Files: '))
        
        for i, path in enumerate(self.paths):
            # Number
            items.append(('class:file-list.number', f'[{i+1}] '))
            
            # Filename
            if i == self.selected_file_idx:
                items.append(('class:file-list.selected', f'{path.name}'))
            else:
                items.append(('class:file-list', f'{path.name}'))
            
            items.append(('class:file-list', '  '))
        
        return FormattedText(items)
    
    def _get_preview_title(self):
        """Get preview section title."""
        if not self.paths:
            return "Preview"
        
        path = self.paths[self.selected_file_idx]
        total = len(self.paths)
        return f"Preview: {path.name} ({self.selected_file_idx + 1}/{total})"
    
    def _get_preview_text(self):
        """Get formatted preview content."""
        if not self.file_lines:
            return FormattedText([('class:preview', '  (No content)')])
        
        visible = self._get_visible_lines()
        start = self.scroll_offset
        end = min(start + visible, len(self.file_lines))
        
        items = []
        for i in range(start, end):
            if self.show_line_numbers:
                line_num = f" {i+1:4d} │ "
                items.append(('class:file-list.number', line_num))
            else:
                items.append(('class:preview', '  '))  # Simple indent
            items.append(('class:preview', self.file_lines[i] + '\n'))
        
        # Show scroll indicator (no extra newline)
        if end < len(self.file_lines):
            remaining = len(self.file_lines) - end
            items.append(('class:file-list', f'  ... ({remaining} more lines)'))
        
        return FormattedText(items)
    
    def _get_buttons_text(self):
        """Get formatted buttons."""
        items = []
        items.append(('', '          '))  # Padding
        
        for i, btn in enumerate(self.BUTTONS):
            if i == self.selected_button_idx:
                items.append(('class:button.selected', f' {btn} '))
            else:
                items.append(('class:button', f' {btn} '))
            items.append(('', '      '))
        
        return FormattedText(items)
    
    def _get_footer_text(self):
        """Get footer help text."""
        return FormattedText([
            ('class:footer', '  '),
            ('class:footer.key', '↑/↓'),
            ('class:footer', ': Scroll   '),
            ('class:footer.key', '1-9/Tab'),
            ('class:footer', ': Switch file   '),
            ('class:footer.key', '←/→'),
            ('class:footer', ': Button   '),
            ('class:footer.key', 'Enter'),
            ('class:footer', ': Confirm   '),
            ('class:footer.key', 'Esc'),
            ('class:footer', ': Cancel'),
        ])
    
    def _create_layout(self) -> Layout:
        """Create the dialog layout."""
        term_width = shutil.get_terminal_size().columns - 6
        
        # Message area - auto height, content already limited by MAX_MESSAGE_LINES
        message_window = Window(
            content=FormattedTextControl(self._get_message_text),
            dont_extend_height=True,  # Don't extend beyond content
        )
        
        # File list
        file_list_window = Window(
            content=FormattedTextControl(self._get_file_list_text),
            height=1,
        )
        
        # Separator - use full terminal width
        def separator():
            width = shutil.get_terminal_size().columns - 4  # Leave margin for frame
            return FormattedText([('class:dialog.border', '─' * width)])
        
        separator_window = Window(
            content=FormattedTextControl(separator),
            height=1,
        )
        
        # Preview title
        def preview_title():
            title = self._get_preview_title()
            return FormattedText([('class:preview.title', f'  {title}')])
        
        preview_title_window = Window(
            content=FormattedTextControl(preview_title),
            height=1,
        )
        
        # Preview content - fills remaining space (no dont_extend_height)
        preview_window = Window(
            content=FormattedTextControl(self._get_preview_text),
            wrap_lines=False,
        )
        
        # Buttons
        buttons_window = Window(
            content=FormattedTextControl(self._get_buttons_text),
            height=1,
        )
        
        # Footer
        footer_window = Window(
            content=FormattedTextControl(self._get_footer_text),
            height=1,
        )
        
        # Main content area that expands
        main_content = HSplit([
            message_window,
            file_list_window,
            separator_window,
            preview_title_window,
            preview_window,
        ])
        
        # Bottom fixed elements
        bottom_bar = HSplit([
            buttons_window,
            footer_window,
        ])
        
        return Layout(
            Frame(
                HSplit([
                    main_content,
                    bottom_bar,
                ], padding=0),
                title="Review",
                style='class:dialog.border',
            )
        )
    
    def _create_keybindings(self) -> KeyBindings:
        """Create key bindings."""
        kb = KeyBindings()
        
        def safe_exit(event):
            """Safely exit the app, ignoring if already exiting."""
            try:
                if not event.app._is_running:
                    return
                event.app.exit()
            except Exception:
                pass  # Already exiting
        
        # Scrolling
        @kb.add('down')
        @kb.add('j')
        def scroll_down(event):
            max_offset = max(0, len(self.file_lines) - self._get_visible_lines())
            self.scroll_offset = min(self.scroll_offset + 1, max_offset)
        
        @kb.add('up')
        @kb.add('k')
        def scroll_up(event):
            self.scroll_offset = max(0, self.scroll_offset - 1)
        
        @kb.add('pagedown')
        @kb.add(' ')
        def page_down(event):
            max_offset = max(0, len(self.file_lines) - self._get_visible_lines())
            self.scroll_offset = min(
                self.scroll_offset + self._get_visible_lines(),
                max_offset
            )
        
        @kb.add('pageup')
        def page_up(event):
            self.scroll_offset = max(
                0,
                self.scroll_offset - self._get_visible_lines()
            )
        
        # File switching (1-9)
        for i in range(1, 10):
            @kb.add(str(i))
            def switch_file(event, idx=i-1):
                if idx < len(self.paths):
                    self._load_file(idx)
        
        @kb.add('tab')
        def next_file(event):
            if self.paths:
                next_idx = (self.selected_file_idx + 1) % len(self.paths)
                self._load_file(next_idx)
        
        @kb.add('s-tab')  # Shift+Tab
        def prev_file(event):
            if self.paths:
                prev_idx = (self.selected_file_idx - 1) % len(self.paths)
                self._load_file(prev_idx)
        
        # Button switching
        @kb.add('left')
        @kb.add('h')
        def prev_button(event):
            self.selected_button_idx = max(0, self.selected_button_idx - 1)
        
        @kb.add('right')
        @kb.add('l')
        def next_button(event):
            self.selected_button_idx = min(
                len(self.BUTTONS) - 1,
                self.selected_button_idx + 1
            )
        
        # Confirm
        @kb.add('enter')
        def confirm(event):
            btn = self.BUTTONS[self.selected_button_idx]
            if btn == "Approve":
                self.result = NotifyResult(action=NotifyAction.APPROVE)
            else:  # Continue
                self.result = NotifyResult(action=NotifyAction.CONTINUE)
            safe_exit(event)
        
        # Quick keys
        @kb.add('a')
        def quick_approve(event):
            self.result = NotifyResult(action=NotifyAction.APPROVE)
            safe_exit(event)
        
        @kb.add('c')
        def quick_continue(event):
            self.result = NotifyResult(action=NotifyAction.CONTINUE)
            safe_exit(event)
        
        # Cancel
        @kb.add('escape')
        @kb.add('q')
        def cancel(event):
            self.result = NotifyResult(action=NotifyAction.CONTINUE)
            safe_exit(event)
        
        @kb.add('c-c')
        def interrupt(event):
            self.result = NotifyResult(action=NotifyAction.CONTINUE)
            safe_exit(event)
        
        return kb
    
    async def run_async(self) -> NotifyResult:
        """Run the dialog asynchronously.
        
        Returns:
            NotifyResult with user's action
        """
        self.app = Application(
            layout=self._create_layout(),
            key_bindings=self._create_keybindings(),
            style=STYLE,
            full_screen=True,
            mouse_support=False,
        )
        
        try:
            await self.app.run_async()
        except Exception:
            pass
        
        return self.result or NotifyResult(action=NotifyAction.CONTINUE)


async def show_notify_dialog(message: str, paths: List[str]) -> NotifyResult:
    """Show interactive notify dialog.
    
    This should be called within an in_terminal() context when
    there's an active REPL prompt_app.
    
    Args:
        message: Notification message
        paths: List of file paths to review
        
    Returns:
        NotifyResult with user's action
    """
    dialog = InteractiveNotifyDialog(message, paths)
    return await dialog.run_async()
