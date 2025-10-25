#!/usr/bin/env python3
"""
Document Analyzer - Example Amplifier-Dev CLI Tool

Demonstrates:
- AmplifierSession direct use (no wrapper)
- Profile loading via ProfileLoader
- Toolkit utilities integration
- Tool-specific state management
- Multi-file processing with progress
- Graceful error handling
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from amplifier_core import AmplifierSession
from amplifier_app_cli.profile_system import ProfileLoader, compile_profile_to_mount_plan
from amplifier_app_cli.toolkit import (
    discover_files,
    ProgressReporter,
    require_minimum_files,
    validate_input_path,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

STATE_FILE = ".document_analyzer_state.json"


def save_state(processed: list[str], results: list[dict]) -> None:
    """Save state for resumability."""
    state = {"processed": processed, "results": results, "updated": datetime.now().isoformat()}
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


def load_state() -> tuple[list[str], list[dict]]:
    """Load saved state if exists."""
    if Path(STATE_FILE).exists():
        data = json.loads(Path(STATE_FILE).read_text())
        return data.get("processed", []), data.get("results", [])
    return [], []


async def analyze_document(file: Path, session: AmplifierSession) -> dict:
    """Analyze a single document using amplifier-core.

    Returns dict matching code_plan.md lines 346-362:
    {
        "file": str,
        "summary": str,
        "key_insights": list[str],
        "main_topics": list[str],
        "tokens_used": int (or None if not available)
    }
    """
    prompt = f"""Analyze this document and extract key information:

{file.read_text()}

Return JSON with:
- summary: Brief 1-2 sentence summary
- key_insights: List of 3-5 key insights from the document
- main_topics: List of main topics discussed

Focus on actionable insights and core concepts."""

    response = await session.execute(prompt)

    try:
        analysis = json.loads(response)
        return {
            "file": str(file),
            "summary": analysis.get("summary", ""),
            "key_insights": analysis.get("key_insights", []),
            "main_topics": analysis.get("main_topics", []),
            "tokens_used": None,
        }
    except json.JSONDecodeError:
        return {
            "file": str(file),
            "summary": response[:200] + "..." if len(response) > 200 else response,
            "key_insights": [],
            "main_topics": [],
            "tokens_used": None,
        }


async def main(
    input_dir: str, profile: str = "dev", pattern: str = "**/*.md", verbose: bool = False
) -> None:
    """Main entry point for document analyzer."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    validate_input_path(input_dir, must_exist=True, must_be_dir=True)

    files = discover_files(input_dir, pattern)
    require_minimum_files(files, minimum=1, context="document analysis")

    logger.info(f"Found {len(files)} files to analyze")
    for i, file in enumerate(files[:5], 1):
        logger.info(f"  {i}. {file.name}")
    if len(files) > 5:
        logger.info(f"  ... and {len(files) - 5} more")

    loader = ProfileLoader()
    profile_obj = loader.load_profile(profile)
    mount_plan = compile_profile_to_mount_plan(profile_obj)

    processed, results = load_state()
    if processed:
        logger.info(f"Resuming from {len(processed)} already processed files")

    async with AmplifierSession(config=mount_plan) as session:
        progress = ProgressReporter(len(files), "Analyzing documents")

        for file in files:
            if str(file) in processed:
                progress.update()
                continue

            try:
                result = await analyze_document(file, session)

                processed.append(str(file))
                results.append(result)

                save_state(processed, results)

                progress.update()

            except Exception as e:
                logger.error(f"Error analyzing {file.name}: {e}")
                continue

        progress.finish()

    logger.info(f"✓ Processed {len(processed)} files")
    logger.info(f"✓ Results saved to {STATE_FILE}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze markdown documents using AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python document_analyzer.py ./docs
  python document_analyzer.py ./docs --profile production
  python document_analyzer.py ./docs --verbose
        """,
    )
    parser.add_argument("input_dir", help="Directory containing markdown files")
    parser.add_argument("--profile", default="dev", help="Profile to use (default: dev)")
    parser.add_argument("--pattern", default="**/*.md", help="File pattern (default: **/*.md)")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    asyncio.run(main(args.input_dir, args.profile, args.pattern, args.verbose))
