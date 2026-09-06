"""Aigator parsers package."""

from aigator.parsers.base import (
    BaseParser,
    detect_and_parse,
    get_registered_parsers,
    register_parser,
)
from aigator.parsers.chatgpt import ChatGPTParser
from aigator.parsers.claude import ClaudeParser
from aigator.parsers.gemini import GeminiParser
from aigator.parsers.perplexity import PerplexityParser
from aigator.parsers.vscode_copilot import VSCodeCopilotParser, COPILOT_DB_DEFAULT

__all__ = [
    "BaseParser",
    "register_parser",
    "get_registered_parsers",
    "detect_and_parse",
    "ChatGPTParser",
    "ClaudeParser",
    "GeminiParser",
    "PerplexityParser",
    "VSCodeCopilotParser",
    "COPILOT_DB_DEFAULT",
]
