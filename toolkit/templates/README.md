# Amplifier Toolkit Templates

## Quick Start

1. **Copy the template:**

   ```bash
   cp toolkit/templates/cli_tool_template.py scripts/my_tool.py
   ```

2. **Update the module contract:**

   - Define purpose (ONE clear responsibility)
   - Specify inputs and outputs
   - Document side effects
   - List dependencies

3. **Implement processing logic:**

   - Fill in the `_process_single_item()` function
   - Use toolkit utilities for common operations
   - Follow the fail-gracefully principle

4. **Test your tool:**
   ```bash
   python scripts/my_tool.py test_data/ -o results.json
   ```

## Template Structure

The template provides a standard structure with:

- **Module Contract**: Clear specification of what the tool does
- **Data Models**: Standard result format using dataclasses
- **Core Processing**: Main logic with error handling
- **CLI Interface**: Consistent argument parsing and logging
- **Progress Reporting**: Built-in progress tracking
- **Incremental Saving**: Results saved periodically

## Best Practices Checklist

### Functionality

- [ ] Single, clear purpose (do one thing well)
- [ ] Recursive file discovery (`**/pattern` not `*.pattern`)
- [ ] Input validation before processing
- [ ] Progress visibility during execution
- [ ] Incremental saving (save after each item or batch)

### Robustness

- [ ] Handles missing/invalid files gracefully
- [ ] Continues processing on failures (partial > nothing)
- [ ] Collects and reports all errors
- [ ] Returns partial results when possible
- [ ] Uses retry logic for I/O operations

### Code Quality

- [ ] Follows ruthless simplicity principle
- [ ] Clear public interface (`__all__` exports)
- [ ] Complete contract specification
- [ ] Uses toolkit utilities (don't reinvent)
- [ ] Meaningful error messages

## Using Toolkit Utilities

The toolkit provides tested utilities for common operations:

### File Operations

```python
from toolkit.utilities import discover_files, read_json, write_json

# Discover files recursively
files = discover_files(Path("docs"), "**/*.md")

# Read/write JSON with retry logic (handles cloud sync)
data = read_json(Path("config.json"))
write_json(results, Path("output.json"))
```

### Progress Reporting

```python
from toolkit.utilities import ProgressReporter

# Track progress with automatic logging
progress = ProgressReporter(len(items), "Processing items")
for item in items:
    process(item)
    progress.update(item.name)
progress.complete()
```

### Validation

```python
from toolkit.utilities import (
    validate_input_path,
    validate_minimum_files,
    validate_output_path
)

# Validate inputs with clear errors
validate_input_path(input_path, must_be_dir=True)
validate_minimum_files(files, minimum=2, file_type="profiles")
validate_output_path(output_path)
```

## Philosophy Alignment

Tools built with this toolkit follow amplifier philosophy:

### Mechanism not Policy

Tools provide capabilities, users decide how to use them. Don't bake in assumptions about workflows.

### Ruthless Simplicity

- Minimal abstractions
- Direct implementations
- Clear failure modes
- No unnecessary complexity

### Modular Design

- Clear contracts (inputs/outputs/side-effects)
- Self-contained functionality
- Regeneratable from specification
- No hidden dependencies

### Fail Gracefully

- Partial results > complete failure
- Continue on errors when sensible
- Report what worked and what didn't
- Save progress incrementally

## Common Patterns

### Pattern 1: Batch Processing

```python
def process(input_dir, output_path):
    files = discover_files(Path(input_dir), "**/*.md")
    validate_minimum_files(files, 1)

    results = []
    errors = []
    progress = ProgressReporter(len(files), "Processing")

    for file in files:
        try:
            result = process_file(file)
            results.append(result)
        except Exception as e:
            errors.append({"file": str(file), "error": str(e)})
        progress.update(file.name)

        # Save incrementally
        if len(results) % 10 == 0:
            write_json({"results": results, "errors": errors}, output_path)

    progress.complete()
    return ToolResult(
        status="partial" if errors else "success",
        data=results,
        errors=errors
    )
```

### Pattern 2: Validation Pipeline

```python
def validate_profiles(profiles_dir):
    # Stage 1: Discovery
    files = discover_files(Path(profiles_dir), "**/*.yaml")

    # Stage 2: Validation
    validation_results = []
    for file in files:
        try:
            data = read_yaml(file)  # You implement read_yaml
            validate_json_structure(data, ["name", "version"])
            validation_results.append({"file": str(file), "valid": True})
        except Exception as e:
            validation_results.append({
                "file": str(file),
                "valid": False,
                "error": str(e)
            })

    # Stage 3: Summary
    valid_count = sum(1 for r in validation_results if r["valid"])
    return ToolResult(
        status="success" if valid_count == len(files) else "partial",
        data=validation_results,
        metadata={"valid": valid_count, "total": len(files)}
    )
```

## Example Tools

Here are examples of tools built with this toolkit:

### Profile Validator

Validates YAML configuration profiles:

```bash
python scripts/validate_profiles.py profiles/ -o validation.json
```

### Module Smoke Test

Tests all modules in a directory:

```bash
python scripts/test_modules.py modules/ -o test_results.json
```

### Dependency Checker

Checks for missing or outdated dependencies:

```bash
python scripts/check_deps.py . -p "**/pyproject.toml" -o deps.json
```

## Troubleshooting

### Common Issues

**No files found:**

- Check your pattern uses `**` for recursion
- Verify the input path exists
- Try with `-v` flag for verbose logging

**I/O errors with cloud sync:**

- Toolkit handles retries automatically
- Enable "Always keep on this device" for OneDrive/Dropbox folders
- Check file permissions

**Tool runs slowly:**

- Process fewer items with `-m/--max-items`
- Save results less frequently (modify template)
- Use parallel processing if appropriate (advanced)

## Next Steps

1. Copy the template
2. Define your tool's contract
3. Implement the core logic
4. Test with real data
5. Iterate based on usage

Remember: Start simple, make it work, then optimize if needed.
