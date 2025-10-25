#!/usr/bin/env python3
"""
CLI Tool Template - Demonstrates Correct Amplifier-Dev Pattern

CRITICAL: Always use amplifier-core for LLM interactions.
Never wrap AmplifierSession - use it directly.

Pattern:
- Code handles: loops, file I/O, state, coordination
- Amplifier-core handles: LLM interactions via orchestrator
- Toolkit utilities handle: file discovery, progress, validation

This template shows:
1. Profile loading via ProfileLoader + compile_profile_to_mount_plan
2. AmplifierSession direct use (async context manager)
3. Tool-specific state management (save/load functions)
4. All toolkit utilities (discover_files, ProgressReporter, validation)
5. Incremental saves after each operation
6. Graceful error handling (continue processing on failures)
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

# Kernel mechanism - use directly (no wrapper!)
from amplifier_core import AmplifierSession

# App-layer utilities
from amplifier_app_cli.profile_system import ProfileLoader, compile_profile_to_mount_plan
from amplifier_app_cli.toolkit import (
    discover_files,
    ProgressReporter,
    require_minimum_files,
    validate_input_path,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# STATE MANAGEMENT (Tool-Specific Pattern)
# ============================================================================
# Fixed filename, overwrite pattern - enables resumability after interruption

STATE_FILE = ".tool_state.json"


def save_state(processed: list[str], results: list[dict]) -> None:
    """Save tool state for resumability (fixed filename, overwrite pattern).

    This function writes state after EVERY operation to ensure no data loss
    if the process is interrupted.

    Args:
        processed: List of items already processed (file paths as strings)
        results: List of result dictionaries from processing
    """
    state = {"processed": processed, "results": results, "updated": datetime.now().isoformat()}
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


def load_state() -> tuple[list[str], list[dict]]:
    """Load saved state if exists.

    Returns:
        Tuple of (processed items, results) or ([], []) if no state file exists
    """
    if not Path(STATE_FILE).exists():
        return [], []

    data = json.loads(Path(STATE_FILE).read_text())
    return data.get("processed", []), data.get("results", [])


# ============================================================================
# CORE PROCESSING FUNCTIONS
# ============================================================================


async def analyze_file(file: Path, session: AmplifierSession) -> dict:
    """
    Analyze a single file using amplifier-core.

    CRITICAL: This function demonstrates the correct pattern:
    - Use session.execute() directly (no wrapper!)
    - Amplifier-core handles: providers, retries, parsing, streaming, hooks
    - Tool handles: prompt construction, result extraction, error handling

    Args:
        file: Path to file to analyze
        session: AmplifierSession instance (kernel mechanism)

    Returns:
        Dictionary with analysis results
    """
    # Construct prompt (tool-specific logic)
    content = file.read_text(encoding="utf-8")
    prompt = f"""Analyze this file and extract key information:

{content}

Provide key insights about the file's purpose, structure, and important details."""

    # Use amplifier-core for intelligence (kernel mechanism)
    # Session handles: provider selection, retries, parsing, hooks
    response = await session.execute(prompt)

    # Return structured result (tool-specific format)
    return {"file": str(file), "analysis": response, "timestamp": datetime.now().isoformat()}


async def process(input_dir: str, profile: str = "dev", pattern: str = "**/*.md") -> dict:
    """
    Main processing function demonstrating complete pattern.

    Pattern breakdown:
    1. Validate inputs (toolkit utilities)
    2. Discover files (toolkit utilities)
    3. Load profile (app-layer: ProfileLoader + compile_profile_to_mount_plan)
    4. Create session (kernel: AmplifierSession - use directly!)
    5. Process with progress (code for structure, amplifier-core for intelligence)
    6. Save state incrementally (tool-specific state management)
    7. Handle errors gracefully (continue processing on failures)

    Args:
        input_dir: Path to input directory
        profile: Profile name to use (default: "dev")
        pattern: Glob pattern for file discovery (default: "**/*.md")

    Returns:
        Dictionary with processing summary
    """
    # ========================================================================
    # STEP 1: Validate Input (Toolkit Utilities)
    # ========================================================================
    # Fail fast with clear errors if input is invalid
    logger.info("Validating input...")
    input_path = Path(input_dir)
    validate_input_path(input_path, must_exist=True, must_be_dir=True)

    # ========================================================================
    # STEP 2: Discover Files (Toolkit Utilities)
    # ========================================================================
    # Always use recursive patterns (**/) to search all subdirectories
    logger.info(f"Discovering files with pattern: {pattern}")
    files = discover_files(input_path, pattern)

    # Validate minimum files required for processing
    require_minimum_files(files, minimum=1, context="processing requires at least one file")

    logger.info(f"Found {len(files)} files to process")
    # Show preview of files (first 5)
    for file in files[:5]:
        logger.info(f"  • {file.name}")
    if len(files) > 5:
        logger.info(f"  • ... and {len(files) - 5} more")

    # ========================================================================
    # STEP 3: Load Profile (App-Layer Responsibility)
    # ========================================================================
    # CRITICAL: Use ProfileLoader + compile_profile_to_mount_plan
    # NOT ProfileManager.get_profile_as_mount_plan() (that method doesn't exist)
    logger.info(f"Loading profile: {profile}")
    loader = ProfileLoader()
    profile_obj = loader.load_profile(profile)  # Load profile object
    mount_plan = compile_profile_to_mount_plan(profile_obj)  # Compile to mount plan

    # ========================================================================
    # STEP 4: Load State for Resumability (Tool-Specific Pattern)
    # ========================================================================
    # Load any previously saved state to enable resume after interruption
    processed, results = load_state()
    if processed:
        logger.info(f"Resuming from previous run - {len(processed)} files already processed")

    # ========================================================================
    # STEP 5: Create Session and Process (Kernel Mechanism - Use Directly!)
    # ========================================================================
    # CRITICAL: Use AmplifierSession directly (no wrapper!)
    # Session handles: providers, orchestrator, hooks, context
    async with AmplifierSession(config=mount_plan) as session:
        # Progress reporting (toolkit utility)
        progress = ProgressReporter(len(files), "Processing files")

        # Process each file with error handling
        for file in files:
            # ================================================================
            # Resume Check: Skip Already Processed Files
            # ================================================================
            if str(file) in processed:
                progress.update()  # Still update progress for skipped files
                continue

            try:
                # ============================================================
                # Use Amplifier-Core for Intelligence
                # ============================================================
                # Session.execute() handles:
                # - Provider selection (from profile)
                # - Retries (from orchestrator)
                # - Response parsing (from provider)
                # - Streaming (if enabled)
                # - Hook execution (logging, approval, redaction)
                result = await analyze_file(file, session)

                # ============================================================
                # Save Result and Update State
                # ============================================================
                processed.append(str(file))
                results.append(result)

                # ============================================================
                # Incremental Save (After EVERY Operation!)
                # ============================================================
                # CRITICAL: Save after each file, not in batches
                # This ensures no data loss if interrupted
                save_state(processed, results)

                # Update progress
                progress.update()

            except Exception as e:
                # ============================================================
                # Graceful Error Handling
                # ============================================================
                # Log error but CONTINUE processing other files
                # Partial results better than nothing
                logger.error(f"Error processing {file}: {e}")
                # Optional: Record error in state
                # Continue to next file without saving as "processed"
                continue

        # Finish progress reporting
        progress.finish()

    # ========================================================================
    # STEP 6: Return Summary
    # ========================================================================
    return {
        "total": len(files),
        "processed": len(processed),
        "results": results,
        "state_file": STATE_FILE,
    }


# ============================================================================
# CLI INTERFACE
# ============================================================================


def main() -> None:
    """Standard CLI interface with argument parsing."""
    import argparse

    parser = argparse.ArgumentParser(
        description="CLI Tool Template - Demonstrates Correct Amplifier-Dev Pattern",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all markdown files in directory
  python cli_tool_template.py ./docs

  # Use different profile
  python cli_tool_template.py ./docs --profile production

  # Process Python files instead
  python cli_tool_template.py ./src --pattern "**/*.py"

State Management:
  - Tool saves state to .tool_state.json after each file
  - Interrupt with Ctrl+C - safe to restart, will resume
  - Delete .tool_state.json to start fresh
        """,
    )

    parser.add_argument("input_dir", help="Input directory to process")
    parser.add_argument("--profile", default="dev", help="Profile to use (default: dev)")
    parser.add_argument("--pattern", default="**/*.md", help="File pattern (default: **/*.md)")

    args = parser.parse_args()

    # Run processing
    result = asyncio.run(process(args.input_dir, args.profile, args.pattern))

    # Report results
    logger.info(f"\n✓ Processing complete!")
    logger.info(f"  Total files: {result['total']}")
    logger.info(f"  Processed: {result['processed']}")
    logger.info(f"  Results saved to: {result['state_file']}")


if __name__ == "__main__":
    main()
