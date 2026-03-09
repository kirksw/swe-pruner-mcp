# SWE-Pruner MCP Server

Model Context Protocol (MCP) server for [SWE-Pruner](https://github.com/Ayanami1314/swe-pruner), enabling context-aware code pruning to reduce token usage by 23-54%.

## What It Does

SWE-Pruner is a 0.6B parameter model that selectively prunes code based on your current task or question. This MCP server wraps it as tools that can be used with opencode and other MCP-compatible AI agents.

## Installation

This package is installed as part of your nixfiles-v2 configuration:

```bash
# Enable the module
# In hosts/darwin/work/home.nix, add:
homeModules.swePrunerMcp.enable = true;

# Apply changes
ns
```

At runtime, the server will try to load a model from `MODEL_PATH` if it exists. If no model is available, it falls back to heuristic pruning and still returns useful output.

## Usage

### Tools Available

#### `read_pruned(file_path, context_focus_question?)`

Read a file with optional context-aware pruning.

**Parameters:**
- `file_path` (required): Path to the file to read
- `context_focus_question` (optional): Question to guide pruning. Only code relevant to this question will be returned.

**Examples:**
```bash
# Without pruning (returns full file)
read_pruned(file_path="src/main.py")

# With pruning (returns only relevant sections)
read_pruned(
  file_path="src/main.py",
  context_focus_question="How is authentication handled in this file?"
)
```

#### `search_pruned(pattern, context_focus_question?)`

Search codebase with optional context-aware pruning.

**Parameters:**
- `pattern` (required): Pattern to search for (regex supported)
- `context_focus_question` (optional): Question to guide pruning. Only matches relevant to this question will be returned.

**Examples:**
```bash
# Search without pruning
search_pruned(pattern="class User")

# Search with pruning
search_pruned(
  pattern="class User",
  context_focus_question="What fields does the User class have?"
)
```

## How Pruning Works

1. **No Query**: returns full content.
2. **With Query + Model Available**: uses model-backed line relevance scoring.
3. **With Query + No Model**: uses heuristic pruning fallback.
4. **Fallback Behavior**: if pruning fails, full content is returned automatically.
4. **Statistics**: All operations logged to `$HOME/.cache/swe-pruner/stats.json`

## Performance

- **Token Savings**: 23-54% on average (based on SWE-Pruner paper)
- **First model-backed call**: can be slow (model load)
- **Heuristic fallback**: fast and available without model files
- **Model size**: depends on chosen model path

## Statistics

View pruning statistics:

```bash
cat $HOME/.cache/swe-pruner/stats.json
```

Format:
```json
[
  {
    "timestamp": "2026-02-04T12:00:00",
    "operation": "prune",
    "input_size": 15234,
    "output_size": 7890,
    "compression_ratio": 0.482,
    "status": "success",
    "error": null,
    "metadata": {
      "query": "How is authentication handled in this file?"
    }
  }
]
```

## Troubleshooting

### Model Loading Fails

Check if model path is correct:
```bash
echo $MODEL_PATH
ls -la $MODEL_PATH
```

### Tools Not Found in Opencode

Ensure opencode is configured to use the MCP server. Check `modules/home/programs/opencode.nix` has the MCP server configured.

### Slow Performance

- First call is always slow due to model loading
- Consider keeping opencode open for longer sessions
- Check stats JSON to see actual compression ratios

## Updating Model

When a new model version is released:

```bash
# Update flake inputs (includes any model updates)
nu

# Rebuild
ns
```

The nix store automatically garbage collects old model versions after 7 days.

## License

MIT
