#!/usr/bin/env python3
"""
CLI Tool Template for Amplifier Tools

This template provides a standard structure for building Amplifier-powered tools.
Follows toolkit best practices and amplifier philosophy.

To use:
1. Copy this template to your scripts directory
2. Update the MODULE CONTRACT section
3. Implement the _process_single_item function
4. Test with: python your_tool.py <input_path> -o results.json
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

# ============================================================================
# MODULE CONTRACT
# ============================================================================
"""
Module: [Tool Name]
Purpose: [Single clear responsibility]

Inputs:
  - input_path: Path to file or directory to process
  - pattern: Glob pattern for file discovery (default: "**/*.md")
  - output: Output file path (default: "results.json")

Outputs:
  - JSON file with processing results
  - Status: "success", "partial", or "error"
  - Data: Processing results
  - Metadata: Errors, statistics, etc.

Side Effects:
  - Writes to output file incrementally
  - [Any other side effects]

Dependencies:
  - [List any external dependencies]
"""

__all__ = ["process", "validate_input", "ToolResult"]


# ============================================================================
# DATA MODELS
# ============================================================================


@dataclass
class ToolResult:
    """Standard result format for all tools."""

    status: str  # "success", "partial", "error"
    data: Any
    metadata: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


# ============================================================================
# CORE PROCESSING
# ============================================================================


def validate_input(input_path: Path) -> bool:
    """Validate input before processing.

    Args:
        input_path: Path to validate

    Returns:
        True if valid

    Raises:
        ValueError: If input is invalid
    """
    if not input_path.exists():
        raise ValueError(f"Path does not exist: {input_path}")

    # Add more validation as needed
    return True


def discover_files(base_path: Path, pattern: str = "**/*.md") -> list[Path]:
    """Discover files recursively with pattern.

    Args:
        base_path: Directory to search
        pattern: Glob pattern (always recursive with **)

    Returns:
        List of matching file paths
    """
    if base_path.is_file():
        return [base_path]

    files = list(base_path.glob(pattern))
    return sorted(files)  # Consistent ordering


def _process_single_item(item_path: Path) -> dict:
    """Process a single item (file or other unit).

    This is where your tool's core logic goes.

    Args:
        item_path: Path to item to process

    Returns:
        Processing result dictionary

    Raises:
        Exception: Any processing errors (will be caught and logged)
    """
    # TODO: Implement your processing logic here
    # Example:
    content = item_path.read_text(encoding="utf-8")
    return {
        "path": str(item_path),
        "size": len(content),
        "lines": content.count("\n"),
        # Add your processing results
    }


def save_results(results: ToolResult, output_path: str | Path) -> None:
    """Save results to JSON file incrementally.

    Args:
        results: Results to save
        output_path: Output file path
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write with temporary file
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(results.to_dict(), f, indent=2, ensure_ascii=False)
        temp_path.replace(output_path)  # Atomic on POSIX
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def process(
    input_path: str | Path,
    output_path: str | Path = "results.json",
    pattern: str = "**/*.md",
    max_items: int | None = None,
) -> ToolResult:
    """Main processing function with standard interface.

    Args:
        input_path: Path to file or directory to process
        output_path: Output file path for results
        pattern: Glob pattern for file discovery
        max_items: Optional limit on items to process

    Returns:
        ToolResult with processing outcome
    """
    # 1. Validate input
    input_path_obj = Path(input_path)
    validate_input(input_path_obj)

    # 2. Discover files/items
    items = discover_files(input_path_obj, pattern)
    if max_items:
        items = items[:max_items]

    # Validate minimum items
    if len(items) == 0:
        return ToolResult(
            status="error",
            data=[],
            metadata={"error": f"No files matching pattern: {pattern}"},
        )

    logging.info(f"Processing {len(items)} items")
    if len(items) <= 5:
        for item in items:
            logging.info(f"  • {item.name}")
    else:
        for item in items[:3]:
            logging.info(f"  • {item.name}")
        logging.info(f"  • ... and {len(items) - 3} more")

    # 3. Process with progress
    results_data = []
    errors = []
    processed = 0

    for i, item in enumerate(items, 1):
        try:
            # Process single item
            result = _process_single_item(item)
            results_data.append(result)
            processed += 1

            # Log progress
            if i % 10 == 0 or i == len(items):
                logging.info(f"Progress: {i}/{len(items)} items processed")

            # Save incrementally every 10 items
            if i % 10 == 0:
                partial_result = ToolResult(
                    status="partial",
                    data=results_data,
                    metadata={"processed": processed, "total": len(items)},
                    errors=errors,
                )
                save_results(partial_result, output_path)

        except Exception as e:
            # Collect error but continue processing
            error_info = {"item": str(item), "error": str(e)}
            errors.append(error_info)
            logging.warning(f"Failed to process {item.name}: {e}")

    # 5. Final save with status
    if errors:
        status = "partial" if results_data else "error"
    else:
        status = "success"

    final_result = ToolResult(
        status=status,
        data=results_data,
        metadata={
            "processed": processed,
            "total": len(items),
            "failed": len(errors),
        },
        errors=errors,
    )

    save_results(final_result, output_path)
    logging.info(f"✓ Results saved to: {output_path}")

    return final_result


# ============================================================================
# CLI INTERFACE
# ============================================================================


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with consistent format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    """Standard CLI with consistent argument handling."""
    parser = argparse.ArgumentParser(
        description="[Tool purpose description]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all markdown files in directory
  python tool.py docs/ -o results.json

  # Process specific file
  python tool.py README.md -o results.json

  # Process with custom pattern
  python tool.py src/ -p "**/*.py" -o results.json

  # Limit processing
  python tool.py data/ -m 100 -o results.json
        """,
    )

    parser.add_argument("input_path", help="Path to file or directory to process")
    parser.add_argument("-o", "--output", default="results.json", help="Output file path (default: results.json)")
    parser.add_argument(
        "-p",
        "--pattern",
        default="**/*.md",
        help="Glob pattern for file discovery (default: **/*.md)",
    )
    parser.add_argument("-m", "--max-items", type=int, help="Maximum items to process")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        # Process
        result = process(
            input_path=args.input_path,
            output_path=args.output,
            pattern=args.pattern,
            max_items=args.max_items,
        )

        # Report summary
        logger.info(f"Status: {result.status}")
        logger.info(f"Processed: {result.metadata.get('processed', 0)} items")
        if result.errors:
            logger.warning(f"Errors: {len(result.errors)}")

        # Exit with appropriate code
        if result.status == "error":
            sys.exit(1)
        elif result.status == "partial":
            sys.exit(2)  # Partial success

    except Exception as e:
        logger.error(f"Tool failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
