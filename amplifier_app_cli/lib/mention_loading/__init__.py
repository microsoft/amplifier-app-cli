"""Mention loading library for Amplifier.

This library loads files referenced by @mentions, deduplicates content,
and returns Message objects for use in context.
"""

from .deduplicator import ContentDeduplicator
from .loader import MentionLoader
from .models import ContextFile
from .resolver import MentionResolver

__all__ = ["MentionLoader", "MentionResolver", "ContentDeduplicator", "ContextFile"]
