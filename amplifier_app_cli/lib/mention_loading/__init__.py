"""Mention loading library for Amplifier.

This library loads files referenced by @mentions, deduplicates content,
and returns Message objects for use in context.
"""

from amplifier_foundation.mentions import ContentDeduplicator
from amplifier_foundation.mentions import ContextFile

from .app_resolver import AppMentionResolver
from .loader import MentionLoader
from .resolver import MentionResolver

__all__ = [
    "AppMentionResolver",
    "ContentDeduplicator",
    "ContextFile",
    "MentionLoader",
    "MentionResolver",
]
