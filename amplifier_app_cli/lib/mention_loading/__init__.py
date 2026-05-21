"""Mention loading library for Amplifier.

This library provides the AppMentionResolver for @mention resolution.
File expansion is handled by amplifier_foundation.mentions.expand_mentions_in_instruction.
"""

from amplifier_foundation.mentions import ContentDeduplicator
from amplifier_foundation.mentions import ContextFile

from .app_resolver import AppMentionResolver
from .app_resolver import MentionResolverProtocol

__all__ = [
    "AppMentionResolver",
    "ContentDeduplicator",
    "ContextFile",
    "MentionResolverProtocol",
]
