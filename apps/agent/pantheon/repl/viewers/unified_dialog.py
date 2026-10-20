"""Unified Review Dialog for files and questions.

Combines file preview and question answering in a single tabbed interface.
Users can switch between file tabs and question tabs using number keys or Tab.
"""

import shutil
from enum import Enum
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import List, Optional, Dict, Any

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window, DynamicContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, Box
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText, ANSI, HTML

from rich.markdown import Markdown
from rich.console import Console


class TabType(Enum):
    """Type of tab content."""
    FILE = "file"
    QUESTION = "question"


@dataclass
class Tab:
    """Unified tab representation."""
    type: TabType
    index: int              # 1-based index for display
    label: str              # Short label for tab bar

    # For FILE tabs
    file_path: Optional[Path] = None
    file_lines: Optional[List[str]] = None
    scroll_offset: int = 0

    # For QUESTION tabs
    question_data: Optional[Dict[str, Any]] = None
    selected_option: Optional[str] = None  # For single_choice
    selected_options: Optional[List[str]] = None  # For multiple_choice
    text_answer: str = ""  # For text_input or "Other"
    current_focus: int = 0  # Which option is currently focused

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.type == TabType.QUESTION and self.selected_options is None:
            self.selected_options = []


@dataclass
class DialogResult:
    """Result from unified dialog."""
    submitted: bool  # True if submitted, False if cancelled
    answers: List[Dict[str, Any]]  # Question answers
    feedback: str = ""  # Rejection feedback (when user provides feedback instead of submitting)


STYLE = Style.from_dict({
    'dialog': 'bg:#1e1e1e',
    'dialog.border': '#61afef',
    'title': '#e5c07b bold',
    'message': '#abb2bf',

    # Tab bar
    'tab': '#5c6370',
    'tab.selected': 'bg:#61afef #282c34 bold',
    'tab.number': '#e06c75',
    'tab.file': '#98c379',
    'tab.question': '#c678dd',

    # File preview
    'file.line-number': '#5c6370',
    'file.content': '#abb2bf',

    # Question
    'question.title': '#98c379 bold',
    'question.header': '#e06c75',
    'question.required': '#e06c75',
    'option': '#abb2bf',
    'option.selected': 'bg:#61afef #282c34 bold',
    'option.description': '#5c6370',
    'input': 'bg:#282c34 #abb2bf',
    'input.placeholder': '#5c6370 italic',

    # UI elements
    'button': '#abb2bf',
    'button.selected': 'bg:#61afef #282c34 bold',
    'footer': '#5c6370',
    'footer.key': '#e5c07b bold',
    'error': '#e06c75 bold',
})


class UnifiedReviewDialog:
    """Unified dialog for file review and question answering."""

    MAX_MESSAGE_LINES = 8

    def __init__(
        self,
        message: str,
        paths: List[str] = None,
        questions: List[Dict[str, Any]] = None
    ):
        """Initialize dialog.

        Args:
            message: Context message from agent
            paths: List of file paths to review (optional)
            questions: List of question dicts (optional)
        """
        self.message = message
        self.paths = self._parse_paths(paths or [])
        self.questions = questions or []

        # Build tabs
        self.tabs: List[Tab] = []
        self._build_tabs()

        # State
        self.current_tab_idx = 0
        self.selected_button_idx = 0  # 0=Submit, 1=Feedback, 2=Cancel
        self.validation_error = ""
        self.result: Optional[DialogResult] = None
        self.feedback_mode = False  # True when user is typing feedback
        self.feedback_text = ""  # User's rejection feedback

        self.app: Optional[Application] = None

    def _parse_paths(self, paths) -> List[Path]:
        """Parse and validate file paths."""
        result = []
        if paths is None:
            return result

        if isinstance(paths, str):
            if ',' in paths:
                paths = [p.strip() for p in paths.split(',')]
            else:
                paths = [paths.strip()]

        if not hasattr(paths, '__iter__'):
            return result

        for p in paths:
            try:
                if not p or not isinstance(p, str):
                    continue
                path = Path(p.strip())
                if path.exists() and path.is_file():
                    result.append(path)
            except Exception:
                continue

        return result

    def _build_tabs(self):
        """Build tab list from files and questions."""
        tab_index = 1

        # Add file tabs
        for path in self.paths:
            try:
                content = path.read_text(encoding='utf-8')
                lines = content.splitlines()
            except UnicodeDecodeError:
                try:
                    content = path.read_text(encoding='latin-1')
                    lines = content.splitlines()
                except Exception:
                    lines = ["<Unable to read file>"]
            except Exception as e:
                lines = [f"<Error: {e}>"]

            self.tabs.append(Tab(
                type=TabType.FILE,
                index=tab_index,
                label=path.name,
                file_path=path,
                file_lines=lines,
                scroll_offset=0,
            ))
            tab_index += 1

        # Add question tabs
        for i, q in enumerate(self.questions):
            # Short label for tab
            header = q.get('header', f'Q{i+1}')

            self.tabs.append(Tab(
                type=TabType.QUESTION,
                index=tab_index,
                label=f"Q{i+1}: {header}",
                question_data=q,
                selected_option=None,
                selected_options=[],
                text_answer="",
                current_focus=0,
            ))
            tab_index += 1

    def _get_current_tab(self) -> Optional[Tab]:
        """Get currently selected tab."""
        if 0 <= self.current_tab_idx < len(self.tabs):
            return self.tabs[self.current_tab_idx]
        return None

    def _is_text_input_active(self) -> bool:
        """Check if user is currently in text input mode."""
        tab = self._get_current_tab()
        if not tab or tab.type != TabType.QUESTION:
            return False

        q = tab.question_data
        q_type = q.get('input_type')

        # text_input type: always in input mode
        if q_type == 'text_input':
            return True

        # single_choice/multiple_choice: input mode when "Other" is selected
        if q_type == 'single_choice':
            return tab.selected_option == '__other__'
        elif q_type == 'multiple_choice':
            return '__other__' in tab.selected_options

        return False

    def _switch_tab(self, new_idx: int):
        """Switch to a different tab."""
        if 0 <= new_idx < len(self.tabs):
            self.current_tab_idx = new_idx
            if self.app:
                self.app.invalidate()

    def _get_visible_lines(self) -> int:
        """Calculate visible lines for content area."""
        term_height = shutil.get_terminal_size().lines
        fixed_lines = 14  # Message, tabs, buttons, footer, borders
        return max(10, term_height - fixed_lines)

    def _render_markdown_to_ansi(self, text: str, width: int) -> str:
        """Render markdown to ANSI."""
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
            console.print(text)
        return buffer.getvalue()

    def _get_message_text(self):
        """Get formatted message text."""
        term_width = shutil.get_terminal_size().columns - 8
        width = max(40, term_width)

        ansi_str = self._render_markdown_to_ansi(self.message, width)
        lines = ansi_str.split('\n')

        if len(lines) > self.MAX_MESSAGE_LINES:
            lines = lines[:self.MAX_MESSAGE_LINES - 1]
            lines.append('\x1b[2m... (message truncated)\x1b[0m')

        indented_lines = ['  ' + line for line in lines]
        return ANSI('\n'.join(indented_lines))

    def _get_tab_bar_text(self):
        """Get formatted tab bar."""
        items = [('', '  Tabs: ')]

        for tab in self.tabs:
            # Tab number
            items.append(('class:tab.number', f'[{tab.index}] '))

            # Tab label with type indicator
            if tab.type == TabType.FILE:
                style = 'class:tab.file'
            else:
                style = 'class:tab.question'

            # Highlight selected tab
            if tab == self._get_current_tab():
                items.append(('class:tab.selected', f' {tab.label} '))
            else:
                items.append((style, f'{tab.label}'))

            items.append(('', '  '))

        return FormattedText(items)

    def _get_content_area(self):
        """Get content for current tab (dynamic)."""
        tab = self._get_current_tab()
        if not tab:
            return Window(
                content=FormattedTextControl(
                    FormattedText([('', '  (No content)')])
                )
            )

        if tab.type == TabType.FILE:
            return self._render_file_content(tab)
        else:
            return self._render_question_content(tab)

    def _render_file_content(self, tab: Tab) -> Window:
        """Render file preview content."""
        if not tab.file_lines:
            return Window(
                content=FormattedTextControl(
                    FormattedText([('class:file.content', '  (No content)')])
                ),
                height=Dimension(min=5)
            )

        def get_file_text():
            visible = self._get_visible_lines()
            start = tab.scroll_offset
            end = min(start + visible, len(tab.file_lines))

            items = []
            for i in range(start, end):
                # Line number
                line_num = f" {i+1:4d} │ "
                items.append(('class:file.line-number', line_num))
                items.append(('class:file.content', tab.file_lines[i] + '\n'))

            # Scroll indicator
            if end < len(tab.file_lines):
                remaining = len(tab.file_lines) - end
                items.append(('class:tab', f'  ... ({remaining} more lines)'))

            return FormattedText(items)

        return Window(
            content=FormattedTextControl(get_file_text),
            wrap_lines=False,
        )

    def _render_question_content(self, tab: Tab) -> HSplit:
        """Render question content with manual selection UI."""
        q = tab.question_data
        q_type = q.get('input_type')

        # Question title
        title_text = f"  {q['question']}"
        header_text = f"  ({q['header']})"
        if q.get('required', True):
            header_text += " *Required"

        title_window = Window(
            content=FormattedTextControl(
                HTML(f'<question.title>{title_text}</question.title>\n'
                     f'<question.header>{header_text}</question.header>')
            ),
            dont_extend_height=True,
        )

        # Render based on question type
        if q_type == 'single_choice':
            return self._render_single_choice(tab, title_window)
        elif q_type == 'multiple_choice':
            return self._render_multiple_choice(tab, title_window)
        elif q_type == 'text_input':
            return self._render_text_input(tab, title_window)
        else:
            return HSplit([title_window])

    def _render_single_choice(self, tab: Tab, title_window: Window) -> HSplit:
        """Render single choice question."""
        q = tab.question_data
        options = q.get('options', [])

        def get_options_text():
            items = []
            items.append(('', '\n'))

            # Regular options
            for i, opt in enumerate(options):
                is_focused = (tab.current_focus == i)
                is_selected = (tab.selected_option == opt['value'])

                if is_focused:
                    prefix = '  > '
                    style = 'class:option.selected'
                else:
                    prefix = '    '
                    style = 'class:option'

                # Radio button
                if is_selected:
                    radio = '● '
                else:
                    radio = '○ '

                items.append((style, f"{prefix}{radio}{opt['label']} - {opt['description']}\n"))

            # "Other" option
            is_other_focused = (tab.current_focus == len(options))
            is_other_selected = (tab.selected_option == '__other__')

            if is_other_focused:
                prefix = '  > '
                style = 'class:option.selected'
            else:
                prefix = '    '
                style = 'class:option'

            if is_other_selected:
                radio = '● '
            else:
                radio = '○ '

            items.append((style, f"{prefix}{radio}Other (specify below)\n"))

            # Text input for "Other"
            items.append(('', '\n'))
            items.append(('class:input', f"  Other: {tab.text_answer}"))
            if is_other_focused or is_other_selected:
                items.append(('class:option.selected', '█'))  # Cursor

            return FormattedText(items)

        options_window = Window(
            content=FormattedTextControl(get_options_text),
            dont_extend_height=True,
        )

        return HSplit([title_window, Window(height=1), options_window])

    def _render_multiple_choice(self, tab: Tab, title_window: Window) -> HSplit:
        """Render multiple choice question."""
        q = tab.question_data
        options = q.get('options', [])

        def get_options_text():
            items = []
            items.append(('', '\n'))

            # Regular options
            for i, opt in enumerate(options):
                is_focused = (tab.current_focus == i)
                is_selected = (opt['value'] in tab.selected_options)

                if is_focused:
                    prefix = '  > '
                    style = 'class:option.selected'
                else:
                    prefix = '    '
                    style = 'class:option'

                # Checkbox
                if is_selected:
                    checkbox = '☑ '
                else:
                    checkbox = '☐ '

                items.append((style, f"{prefix}{checkbox}{opt['label']} - {opt['description']}\n"))

            # "Other" option
            is_other_focused = (tab.current_focus == len(options))
            is_other_selected = ('__other__' in tab.selected_options)

            if is_other_focused:
                prefix = '  > '
                style = 'class:option.selected'
            else:
                prefix = '    '
                style = 'class:option'

            if is_other_selected:
                checkbox = '☑ '
            else:
                checkbox = '☐ '

            items.append((style, f"{prefix}{checkbox}Other (specify below)\n"))

            # Text input for "Other"
            items.append(('', '\n'))
            items.append(('class:input', f"  Other: {tab.text_answer}"))
            if is_other_focused or is_other_selected:
                items.append(('class:option.selected', '█'))  # Cursor

            return FormattedText(items)

        options_window = Window(
            content=FormattedTextControl(get_options_text),
            dont_extend_height=True,
        )

        return HSplit([title_window, Window(height=1), options_window])

    def _render_text_input(self, tab: Tab, title_window: Window) -> HSplit:
        """Render text input question - simplified without redundant options."""
        q = tab.question_data
        placeholder = q.get('placeholder', 'Enter your answer...')

        def get_input_text():
            items = []
            items.append(('', '\n'))
            items.append(('class:option', '  Your answer:\n'))
            items.append(('class:input', f"    {tab.text_answer}"))
            items.append(('class:option.selected', '█'))  # Cursor
            items.append(('', '\n\n'))
            items.append(('class:footer', f"  Hint: {placeholder}"))
            return FormattedText(items)

        input_window = Window(
            content=FormattedTextControl(get_input_text),
            dont_extend_height=True,
        )

        return HSplit([title_window, Window(height=1), input_window])

    def _validate_answers(self) -> bool:
        """Validate all required questions are answered."""
        for tab in self.tabs:
            if tab.type != TabType.QUESTION:
                continue

            q = tab.question_data
            if not q.get('required', True):
                continue

            q_type = q.get('input_type')
            q_num = tab.label.split(':')[0]  # Extract "Q1" from "Q1: Auth"

            if q_type == 'single_choice':
                if tab.selected_option is None:
                    self.validation_error = f"{q_num}: Please select an option"
                    return False
                elif tab.selected_option == '__other__':
                    if not tab.text_answer.strip():
                        self.validation_error = f"{q_num}: Please specify 'Other' answer"
                        return False

            elif q_type == 'multiple_choice':
                if not tab.selected_options:
                    self.validation_error = f"{q_num}: Please select at least one"
                    return False
                elif '__other__' in tab.selected_options:
                    if not tab.text_answer.strip():
                        self.validation_error = f"{q_num}: Please specify 'Other' answer"
                        return False

            elif q_type == 'text_input':
                if not tab.text_answer.strip():
                    self.validation_error = f"{q_num}: Please enter an answer"
                    return False

        self.validation_error = ""
        return True

    def _collect_answers(self) -> List[Dict[str, Any]]:
        """Collect answers from all question tabs."""
        answers = []

        for tab in self.tabs:
            if tab.type != TabType.QUESTION:
                continue

            q = tab.question_data
            q_type = q.get('input_type')

            if q_type == 'single_choice':
                if tab.selected_option is None:
                    continue  # Skip unanswered optional questions

                if tab.selected_option == '__other__':
                    answer = tab.text_answer.strip()
                else:
                    answer = tab.selected_option

                answers.append({
                    'question': q['question'],
                    'header': q['header'],
                    'answer': answer,
                    'input_type': 'single_choice'
                })

            elif q_type == 'multiple_choice':
                if not tab.selected_options:
                    continue  # Skip unanswered optional questions

                answer_list = []
                for val in tab.selected_options:
                    if val == '__other__':
                        other_text = tab.text_answer.strip()
                        if other_text:
                            answer_list.append(other_text)
                    else:
                        answer_list.append(val)

                answers.append({
                    'question': q['question'],
                    'header': q['header'],
                    'answer': answer_list,
                    'input_type': 'multiple_choice'
                })

            elif q_type == 'text_input':
                answer = tab.text_answer.strip()
                if not answer:
                    continue  # Skip unanswered optional questions

                answers.append({
                    'question': q['question'],
                    'header': q['header'],
                    'answer': answer,
                    'input_type': 'text_input'
                })

        return answers

    def _create_layout(self) -> Layout:
        """Create the dialog layout."""
        # Message area
        message_window = Window(
            content=FormattedTextControl(self._get_message_text),
            dont_extend_height=True,
        )

        # Tab bar
        tab_bar_window = Window(
            content=FormattedTextControl(self._get_tab_bar_text),
            height=1,
        )

        # Separator
        def separator():
            width = shutil.get_terminal_size().columns - 4
            return FormattedText([('class:dialog.border', '─' * width)])

        separator_window = Window(
            content=FormattedTextControl(separator),
            height=1,
        )

        # Dynamic content area
        content_container = DynamicContainer(self._get_content_area)

        # Validation error
        def get_error_text():
            if self.validation_error:
                return HTML(f'<error>  ⚠ {self.validation_error}</error>')
            return ""

        error_window = Window(
            content=FormattedTextControl(get_error_text),
            height=1,
        )

        # Feedback input area (shown when feedback mode is active)
        def get_feedback_text():
            if not self.feedback_mode:
                return ""
            text = self.feedback_text or ""
            display = text + "█"  # cursor
            return HTML(
                f'<question.header>  Feedback: </question.header>'
                f'<input>{display}</input>'
            )

        feedback_window = Window(
            content=FormattedTextControl(get_feedback_text),
            height=lambda: 2 if self.feedback_mode else 0,
        )

        # Buttons
        def get_buttons_text():
            items = [('', '     ')]
            if self.feedback_mode:
                buttons = ['Send Feedback', 'Back']
            else:
                buttons = ['Submit', 'Provide Feedback', 'Cancel']

            for i, btn in enumerate(buttons):
                if i == self.selected_button_idx:
                    items.append(('class:button.selected', f' {btn} '))
                else:
                    items.append(('class:button', f' {btn} '))
                items.append(('', '   '))

            return FormattedText(items)

        buttons_window = Window(
            content=FormattedTextControl(get_buttons_text),
            height=1,
        )

        # Footer
        footer_window = Window(
            content=FormattedTextControl(
                HTML('<footer>  <footer.key>1-9/Tab</footer.key>: Switch tab   '
                     '<footer.key>↑/↓</footer.key>: Scroll/Navigate   '
                     '<footer.key>Space</footer.key>: Toggle   '
                     '<footer.key>←/→</footer.key>: Button   '
                     '<footer.key>Enter</footer.key>: Submit   '
                     '<footer.key>Esc</footer.key>: Cancel</footer>')
            ),
            height=1,
        )

        # Main layout
        return Layout(
            Frame(
                HSplit([
                    message_window,
                    Window(height=1),
                    tab_bar_window,
                    separator_window,
                    content_container,
                    Window(height=1),
                    error_window,
                    feedback_window,
                    buttons_window,
                    footer_window,
                ]),
                title="Review & Answer Questions",
                style='class:dialog.border',
            )
        )

    def _create_keybindings(self) -> KeyBindings:
        """Create key bindings."""
        kb = KeyBindings()

        # Tab switching (1-9)
        for i in range(1, 10):
            @kb.add(str(i))
            def switch_by_number(event, idx=i-1):
                if self.feedback_mode:
                    self.feedback_text += str(idx + 1)
                    return
                if self._is_text_input_active():
                    # In text input mode, allow number input
                    tab = self._get_current_tab()
                    if tab:
                        tab.text_answer += str(idx + 1)
                elif idx < len(self.tabs):
                    self._switch_tab(idx)

        @kb.add('tab')
        def next_tab(event):
            # Tab always switches to next tab, even in text input mode
            # This ensures users can navigate between question tabs
            if self.tabs:
                next_idx = (self.current_tab_idx + 1) % len(self.tabs)
                self._switch_tab(next_idx)

        @kb.add('s-tab')
        def prev_tab(event):
            # Shift+Tab always switches to previous tab
            if self.tabs:
                prev_idx = (self.current_tab_idx - 1) % len(self.tabs)
                self._switch_tab(prev_idx)

        # Scrolling and navigation (context-aware)
        @kb.add('down')
        @kb.add('j')
        def scroll_or_navigate_down(event):
            if self.feedback_mode:
                if event.key_sequence[0].key == 'j':
                    self.feedback_text += 'j'
                return
            if self._is_text_input_active():
                tab = self._get_current_tab()
                if tab and event.key_sequence[0].key == 'j':
                    tab.text_answer += 'j'
                return

            tab = self._get_current_tab()
            if tab and tab.type == TabType.FILE and tab.file_lines:
                # File tab: scroll down
                max_offset = max(0, len(tab.file_lines) - self._get_visible_lines())
                tab.scroll_offset = min(tab.scroll_offset + 1, max_offset)
            elif tab and tab.type == TabType.QUESTION:
                # Question tab: navigate down through options
                q = tab.question_data
                q_type = q.get('input_type')

                if q_type == 'single_choice':
                    max_focus = len(q.get('options', [])) + 1  # +1 for "Other"
                    tab.current_focus = min(tab.current_focus + 1, max_focus - 1)
                elif q_type == 'multiple_choice':
                    max_focus = len(q.get('options', [])) + 1  # +1 for "Other"
                    tab.current_focus = min(tab.current_focus + 1, max_focus - 1)
                elif q_type == 'text_input':
                    # text_input doesn't need focus navigation
                    pass

        @kb.add('up')
        @kb.add('k')
        def scroll_or_navigate_up(event):
            if self.feedback_mode:
                if event.key_sequence[0].key == 'k':
                    self.feedback_text += 'k'
                return
            if self._is_text_input_active():
                tab = self._get_current_tab()
                if tab and event.key_sequence[0].key == 'k':
                    tab.text_answer += 'k'
                return

            tab = self._get_current_tab()
            if tab and tab.type == TabType.FILE:
                # File tab: scroll up
                tab.scroll_offset = max(0, tab.scroll_offset - 1)
            elif tab and tab.type == TabType.QUESTION:
                # Question tab: navigate up through options
                tab.current_focus = max(0, tab.current_focus - 1)

        @kb.add('pagedown')
        @kb.add(' ')
        def page_down_or_select(event):
            if self.feedback_mode:
                if event.key_sequence[0].key == ' ':
                    self.feedback_text += ' '
                return
            if self._is_text_input_active():
                tab = self._get_current_tab()
                if tab and event.key_sequence[0].key == ' ':
                    tab.text_answer += ' '
                return

            tab = self._get_current_tab()
            if tab and tab.type == TabType.FILE and tab.file_lines:
                # File tab: page down
                max_offset = max(0, len(tab.file_lines) - self._get_visible_lines())
                tab.scroll_offset = min(
                    tab.scroll_offset + self._get_visible_lines(),
                    max_offset
                )
            elif tab and tab.type == TabType.QUESTION:
                # Question tab: select/toggle current option
                q = tab.question_data
                q_type = q.get('input_type')
                options = q.get('options', [])

                if q_type == 'single_choice':
                    if tab.current_focus < len(options):
                        # Select regular option
                        tab.selected_option = options[tab.current_focus]['value']
                    else:
                        # Select "Other"
                        tab.selected_option = '__other__'

                elif q_type == 'multiple_choice':
                    if tab.current_focus < len(options):
                        # Toggle regular option
                        value = options[tab.current_focus]['value']
                        if value in tab.selected_options:
                            tab.selected_options.remove(value)
                        else:
                            tab.selected_options.append(value)
                    else:
                        # Toggle "Other"
                        if '__other__' in tab.selected_options:
                            tab.selected_options.remove('__other__')
                        else:
                            tab.selected_options.append('__other__')

        @kb.add('pageup')
        def page_up(event):
            tab = self._get_current_tab()
            if tab and tab.type == TabType.FILE:
                tab.scroll_offset = max(
                    0,
                    tab.scroll_offset - self._get_visible_lines()
                )

        # Button navigation
        @kb.add('left')
        @kb.add('h')
        def prev_button(event):
            if self._is_text_input_active() or self.feedback_mode:
                tab = self._get_current_tab()
                if self.feedback_mode and event.key_sequence[0].key == 'h':
                    self.feedback_text += 'h'
                elif tab and event.key_sequence[0].key == 'h':
                    tab.text_answer += 'h'
                return
            self.selected_button_idx = max(0, self.selected_button_idx - 1)

        @kb.add('right')
        @kb.add('l')
        def next_button(event):
            if self._is_text_input_active() or self.feedback_mode:
                tab = self._get_current_tab()
                if self.feedback_mode and event.key_sequence[0].key == 'l':
                    self.feedback_text += 'l'
                elif tab and event.key_sequence[0].key == 'l':
                    tab.text_answer += 'l'
                return
            max_btn = 1 if self.feedback_mode else 2
            self.selected_button_idx = min(max_btn, self.selected_button_idx + 1)

        # Submit
        @kb.add('enter')
        def submit(event):
            if self.feedback_mode:
                # In feedback mode
                if self.selected_button_idx == 0:  # Send Feedback
                    if self.feedback_text.strip():
                        self.result = DialogResult(
                            submitted=False,
                            answers=[],
                            feedback=self.feedback_text.strip()
                        )
                        event.app.exit()
                    else:
                        self.validation_error = "Please enter feedback before sending"
                else:  # Back
                    self.feedback_mode = False
                    self.selected_button_idx = 1  # Return focus to "Provide Feedback"
                    self.validation_error = ""
                return

            if self._is_text_input_active():
                # In text input mode, enter adds a newline
                tab = self._get_current_tab()
                if tab:
                    tab.text_answer += '\n'
                return

            if self.selected_button_idx == 0:  # Submit
                if self._validate_answers():
                    self.result = DialogResult(
                        submitted=True,
                        answers=self._collect_answers()
                    )
                    event.app.exit()
            elif self.selected_button_idx == 1:  # Provide Feedback
                self.feedback_mode = True
                self.selected_button_idx = 0  # Focus "Send Feedback"
                self.validation_error = ""
            else:  # Cancel
                self.result = DialogResult(submitted=False, answers=[])
                event.app.exit()

        # Cancel
        @kb.add('escape')
        @kb.add('q')
        @kb.add('c-c')
        def cancel(event):
            if self.feedback_mode and event.key_sequence[0].key == 'q':
                self.feedback_text += 'q'
                return
            if self._is_text_input_active() and event.key_sequence[0].key == 'q':
                tab = self._get_current_tab()
                if tab:
                    tab.text_answer += 'q'
                return
            if self.feedback_mode and event.key_sequence[0].key == 'escape':
                # Esc in feedback mode goes back to normal mode
                self.feedback_mode = False
                self.selected_button_idx = 1
                self.validation_error = ""
                return
            self.result = DialogResult(submitted=False, answers=[])
            event.app.exit()

        # Text input for questions and feedback
        @kb.add('<any>')
        def handle_text_input(event):
            """Handle text input for question tabs and feedback mode."""
            char = event.data
            if not char or len(char) != 1 or not char.isprintable():
                return
            if self.feedback_mode:
                self.feedback_text += char
                return
            tab = self._get_current_tab()
            if tab and tab.type == TabType.QUESTION:
                tab.text_answer += char

        @kb.add('backspace')
        def handle_backspace(event):
            """Handle backspace for text input and feedback mode."""
            if self.feedback_mode:
                if self.feedback_text:
                    self.feedback_text = self.feedback_text[:-1]
                return
            tab = self._get_current_tab()
            if tab and tab.type == TabType.QUESTION:
                if tab.text_answer:
                    tab.text_answer = tab.text_answer[:-1]

        return kb

    async def run_async(self) -> DialogResult:
        """Run the dialog asynchronously."""
        self.app = Application(
            layout=self._create_layout(),
            key_bindings=self._create_keybindings(),
            style=STYLE,
            full_screen=True,
            mouse_support=True,
        )

        try:
            await self.app.run_async()
        except Exception:
            pass

        return self.result or DialogResult(submitted=False, answers=[])


async def show_unified_dialog(
    message: str,
    paths: List[str] = None,
    questions: List[Dict[str, Any]] = None
) -> DialogResult:
    """Show unified review and question dialog.

    Args:
        message: Context message from agent
        paths: List of file paths to review (optional)
        questions: List of question dicts (optional)

    Returns:
        DialogResult with user's answers
    """
    dialog = UnifiedReviewDialog(message, paths, questions)
    return await dialog.run_async()

