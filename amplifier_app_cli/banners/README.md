# BBS-Style ANSI Art Banners

Authentic 90s-style ANSI art banners for Amplifier's interactive sessions, powered by the **ansiterm** library.

## Philosophy

- **Ruthless simplicity**: Use proper BBS art library (ansiterm), render .ANS files
- **Modular design**: Separate library for ANSI art (reusable, portable)
- **Mechanism, not policy**: ansiterm handles rendering, we handle file selection
- **Authenticity**: Real CP437 encoding, ANSI escape sequences, SAUCE metadata
- **Fail gracefully**: Falls back to simple text banner if ansiterm unavailable

## Available Styles

### classic (default)
Clean, professional cyan banner. Minimal art, maximum clarity.

### cyber
Rainbow gradient blocks with neon colors. Futuristic BBS aesthetic.

### underground
Multi-color chaotic art with "NO RULES" attitude. Street art vibes.

### matrix
Green-on-black code rain aesthetic. Classic hacker theme.

### retro
Full 90s BBS with gradient borders (░▒▓). Maximum nostalgia.

## Usage

### In Configuration

Add to your profile or config file:

```yaml
# In a profile (e.g., ~/.amplifier/profiles/myprofile.md)
banner_style: cyber
```

Or in TOML:

```toml
# In a config file
banner_style = "retro"
```

### Environment Variable

```bash
# Set banner style via environment
export AMPLIFIER_BANNER_STYLE=underground
amplifier run --mode chat
```

### Command Line

When you need a one-off style, export the environment variable inline:

```bash
# Use specific banner for a single run
AMPLIFIER_BANNER_STYLE=matrix amplifier run --mode chat
```

## How It Works

Banners are stored as authentic .ANS files in the `art/` directory:

1. **Generation**: Created programmatically using ansiterm's AnsiBuilder API
2. **Storage**: Real .ANS files with CP437 encoding and SAUCE metadata
3. **Rendering**: ansiterm library handles proper terminal control
4. **Fallback**: Simple text banner if ansiterm unavailable

**Philosophy**: Use a proper library for proper BBS art, don't reinvent the wheel.

## Architecture

```
banners/
  __init__.py          # Simple loader (69 lines) - uses ansiterm
  README.md           # This file
  art/                # Actual .ANS files
    classic.ans       # 468 bytes
    cyber.ans         # 1007 bytes
    underground.ans   # 637 bytes
    matrix.ans        # 803 bytes
    retro.ans         # 1367 bytes
```

## The ansiterm Library

Amplifier uses the **ansiterm** library for all ANSI art rendering.

**Why a separate library?**
- Reusable across projects
- Complete BBS art toolkit
- Proper terminal control
- SAUCE metadata support
- iCE color handling
- Safety filtering

**What ansiterm provides:**
- `render_file()` - Render .ANS files to terminal
- `render_bytes()` - Render raw ANSI bytes
- `AnsiBuilder` - Programmatic .ANS generation
- `ansi-view` CLI - View .ANS files
- `ansify` CLI - Generate simple banners

See the ansiterm library README for complete documentation.

## Creating New Banners

### Option 1: Use ansify CLI

```bash
ansify --text "MY BANNER" --fg 15 --bg 4 --center -o custom.ans
```

### Option 2: Use AnsiBuilder API

```python
from ansiterm import AnsiBuilder

b = AnsiBuilder(80, 25)
b.clear().home()

# Your creative vision here
b.fg(15, bright=True).bg(4)  # Bright white on blue
b.move(10, 20).text("╔═══ CUSTOM BANNER ═══╗")
b.move(11, 20).text("║     Your Text       ║")
b.move(12, 20).text("╚═════════════════════╝")
b.reset()

# Export with SAUCE
data = b.to_bytes(
    add_sauce=True,
    title="Custom Banner",
    author="Your Name",
)

Path("custom.ans").write_bytes(data)
```

### Option 3: Import Real BBS Art

Download art from [16colo.rs](https://16colo.rs/) and add to `art/` directory!

```bash
# Download a pack
wget https://16colo.rs/pack/acdu0595/acdu0595.zip
unzip acdu0595.zip

# Copy an .ANS file
cp COOL-ART.ANS amplifier-app-cli/amplifier_app_cli/banners/art/epic.ans

# Update AVAILABLE_STYLES in __init__.py
# Add "epic" to the list

# Use it
AMPLIFIER_BANNER_STYLE=epic amplifier run --mode chat
```

## Testing

View banners with ansi-view:

```bash
cd amplifier-app-cli/amplifier_app_cli/banners/art
ansi-view *.ans
```

Or use the built-in loader:

```python
from amplifier_app_cli.banners import load_banner

for style in ["classic", "cyber", "underground", "matrix", "retro"]:
    print(f"\n=== {style.upper()} ===")
    load_banner(style=style, version="0.1.0")
```

## Technical Details

### CP437 Encoding

BBS art uses the IBM PC character set (Code Page 437) which includes:
- Box drawing: `╔═╗║╚═╝├─┤`
- Blocks: `█▓▒░▀▄`
- Special chars: `☺☻♥♦♣♠`

### ANSI Escape Sequences

Colors and positioning via escape codes:
- `ESC[31m` - Red foreground
- `ESC[44m` - Blue background
- `ESC[10;20H` - Move to row 10, column 20
- `ESC[2J` - Clear screen
- `ESC[0m` - Reset all attributes

### iCE Colors

Historical BBS boards used the "blink" bit for bright backgrounds. ansiterm automatically maps these to modern bright background codes (100-107).

### SAUCE Metadata

Standard Architecture for Universal Comment Extensions - 128-byte metadata block containing title, author, group, date, and dimensions. All our .ANS files include SAUCE.

## Design Notes

These banners embrace authentic BBS aesthetics:
- **Classic**: Professional but with personality
- **Cyber**: Neon colors and geometric shapes
- **Underground**: Street art with rainbow effects
- **Matrix**: Green code rain aesthetic
- **Retro**: Full 90s BBS with system status boards

The goal: nostalgic fun while staying functional.

## Future Ideas

- Animated sequences (modem connect, loading bars)
- Time-based variations (different art for morning/evening)
- Import art packs from 16colo.rs
- Random banner selection
- User-customizable banner directory (`~/.amplifier/banners/`)
- TheDraw-style editor integration
