"""Unified User Response Formatting.

Provides a consistent format for all user responses (approvals, questions, feedback)
sent back to the LLM after interactive dialogs.
"""

import json
from typing import List, Dict, Any, Optional
from enum import Enum


class ResponseType(Enum):
    """Types of user responses."""
    FILE_APPROVAL = "file_approval"
    QUESTION_ANSWERS = "question_answers"
    REJECTION = "rejection"
    SIMPLE_APPROVAL = "simple_approval"


class UserResponseFormatter:
    """Format user responses in a unified, structured way."""

    @staticmethod
    def format_file_approval(
        approved: bool,
        files_reviewed: Optional[List[str]] = None,
        feedback: str = ""
    ) -> str:
        """Format file approval response.

        Args:
            approved: Whether user approved
            files_reviewed: List of file paths that were reviewed
            feedback: Optional feedback (used when rejected)

        Returns:
            Formatted response string
        """
        if not approved:
            return UserResponseFormatter.format_rejection(feedback)

        data = {
            "action": "approved",
            "files_reviewed": files_reviewed or []
        }

        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        return f"""I've reviewed the files and approve.

<user_response type="file_approval">
{json_str}
</user_response>"""

    @staticmethod
    def format_question_answers(answers: List[Dict[str, Any]]) -> str:
        """Format question answers response.

        Args:
            answers: List of answer dicts with keys:
                - question: Question text
                - header: Short label
                - answer: Answer value (string or list)
                - input_type: Optional type (single_choice/multiple_choice/text_input)

        Returns:
            Formatted response string
        """
        if not answers:
            return UserResponseFormatter.format_simple_approval()

        # Build simplified answer data (remove redundant question text)
        answer_data = []
        for qa in answers:
            item = {
                "header": qa['header'],
                "answer": qa['answer']
            }
            # Add input_type if available (helps LLM understand context)
            if 'input_type' in qa:
                item['type'] = qa['input_type']
            answer_data.append(item)

        json_str = json.dumps({"answers": answer_data}, ensure_ascii=False, indent=2)

        # Build human-readable summary
        summary_lines = []
        for i, qa in enumerate(answers, 1):
            answer = qa['answer']
            if isinstance(answer, list):
                answer_str = ', '.join(answer)
            else:
                answer_str = str(answer)
            summary_lines.append(f"  {i}. {qa['header']}: {answer_str}")

        summary = '\n'.join(summary_lines)

        return f"""I've answered your questions:

{summary}

<user_response type="question_answers">
{json_str}
</user_response>"""

    @staticmethod
    def format_rejection(feedback: str) -> str:
        """Format rejection with feedback.

        Args:
            feedback: User's feedback explaining rejection

        Returns:
            Formatted response string
        """
        data = {
            "feedback": feedback
        }

        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        return f"""I have feedback on your proposal:

{feedback}

<user_response type="rejection">
{json_str}
</user_response>"""

    @staticmethod
    def format_simple_approval() -> str:
        """Format simple approval without additional data.

        Returns:
            Formatted response string
        """
        data = {
            "action": "approved"
        }

        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        return f"""Approved. Please proceed.

<user_response type="simple_approval">
{json_str}
</user_response>"""

    @staticmethod
    def format_continue_planning() -> str:
        """Format 'continue planning' response.

        Returns:
            Formatted response string
        """
        data = {
            "action": "continue_planning"
        }

        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        return f"""Please continue planning.

<user_response type="simple_approval">
{json_str}
</user_response>"""


__all__ = [
    "ResponseType",
    "UserResponseFormatter",
]
