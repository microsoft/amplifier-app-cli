"""Mention loading library for Amplifier.

This library loads files referenced by @mentions, deduplicates content,
and returns Message objects for use in context.
"""

from amplifier_foundation.mentions import ContentDeduplicator
from amplifier_foundation.mentions import ContextFile

from .app_resolver import AppMentionResolver
from .app_resolver import MentionResolverProtocol
from .loader import MentionLoader

__all__ = [
    "AppMentionResolver",
    "ContentDeduplicator",
    "ContextFile",
    "MentionLoader",
    "MentionResolverProtocol",
]
