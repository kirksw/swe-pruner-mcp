"""SWE-Pruner MCP Server."""
import os
import re
import sys
import logging
import subprocess
import asyncio
from pathlib import Path
from typing import Any

from mcp.server.lowlevel import Server
from mcp.types import TextContent

from .logger import PrunerLogger

# Configure logging to stderr only
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

torch = None
AutoTokenizer = None
AutoModelForSequenceClassification = None
NO_MATCH_PREFIX = "No matches found for pattern: "


def run_rg_search(pattern: str, search_root: str, max_matches: int) -> str:
    """Run ripgrep and return stdout or a no-match marker."""
    rg_cmd = [
        "rg",
        "--line-number",
        "--with-filename",
        "--hidden",
        "--glob",
        "!.git",
        "--max-count",
        str(max_matches),
        pattern,
        search_root,
    ]
    result = subprocess.run(
        rg_cmd,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.stdout:
        return result.stdout
    return f"No matches found for pattern: {pattern}"


async def run_rg_search_async(
    pattern: str,
    search_root: str,
    max_matches: int,
    timeout_seconds: float,
) -> str:
    """Run ripgrep off the event loop with a hard async timeout."""
    return await asyncio.wait_for(
        asyncio.to_thread(run_rg_search, pattern, search_root, max_matches),
        timeout=timeout_seconds,
    )


async def prune_with_timeout(
    pruner: "SWEPrunerService",
    code: str,
    query: str | None,
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]]:
    """Bound pruning time so the MCP call does not wait indefinitely."""
    return await asyncio.wait_for(pruner.prune(code, query), timeout=timeout_seconds)


async def prune_search_output(
    pruner: "SWEPrunerService",
    output: str,
    query: str | None,
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]]:
    """Return no-match search output immediately, otherwise prune it."""
    if output.startswith(NO_MATCH_PREFIX):
        return output, {
            "pruned": False,
            "tokens": len(output),
            "reason": "No matches found",
        }

    return await prune_with_timeout(pruner, output, query, timeout_seconds)


class SWEPrunerService:
    """Service to load and use SWE-Pruner model."""

    def __init__(self, model_path: str | None = None):
        """Initialize pruner service."""
        self.model_path = model_path or os.getenv("MODEL_PATH")
        self.allow_remote_model_download = os.getenv("ALLOW_REMOTE_MODEL_DOWNLOAD", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.remote_model_name = os.getenv("REMOTE_MODEL_NAME", "ayanami-kitasan/code-pruner")
        self.stats_file = os.getenv("STATS_FILE")
        self.logger = PrunerLogger(self.stats_file)

        self.tokenizer = None
        self.model = None
        self._model_load_attempted = False

    def _resolve_model_name(self) -> str | None:
        """Resolve the model source to use, or None for heuristic-only mode."""
        if self.model_path:
            expanded_model_path = Path(self.model_path).expanduser()
            if expanded_model_path.exists():
                logger.info(f"Loading model from local path: {expanded_model_path}")
                return str(expanded_model_path)

            logger.warning(f"MODEL_PATH does not exist: {expanded_model_path}")

        if not self.allow_remote_model_download:
            logger.info("Remote model download disabled; using heuristic fallback")
            return None

        logger.info(f"Loading model from HuggingFace: {self.remote_model_name}")
        return self.remote_model_name

    def _load_model(self):
        """Load SWE-Pruner model from HuggingFace or local path."""
        try:
            model_name = self._resolve_model_name()
            if model_name is None:
                return

            self._ensure_model_dependencies()

            logger.info("Loading tokenizer...")
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)

            logger.info("Loading model (this may take 30+ seconds)...")
            self.model = AutoModelForSequenceClassification.from_pretrained(model_name)

            self.model.eval()
            logger.info("Model loaded successfully!")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            logger.warning("Will operate in heuristic fallback mode (no model scoring)")

    @staticmethod
    def _ensure_model_dependencies():
        """Import model dependencies lazily so heuristic mode still works."""
        global torch, AutoTokenizer, AutoModelForSequenceClassification
        if torch is not None and AutoTokenizer is not None and AutoModelForSequenceClassification is not None:
            return

        import torch as torch_module
        from transformers import AutoModelForSequenceClassification as auto_model
        from transformers import AutoTokenizer as auto_tokenizer

        torch = torch_module
        AutoTokenizer = auto_tokenizer
        AutoModelForSequenceClassification = auto_model

    def _ensure_model_loaded(self):
        """Attempt model loading once per process."""
        if self._model_load_attempted:
            return
        self._model_load_attempted = True
        self._load_model()

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query.lower())
        stopwords = {
            "the",
            "and",
            "with",
            "from",
            "this",
            "that",
            "what",
            "where",
            "when",
            "which",
            "how",
            "into",
            "have",
            "has",
            "for",
            "file",
            "code",
        }
        unique = []
        seen = set()
        for token in tokens:
            if token in stopwords or token in seen:
                continue
            seen.add(token)
            unique.append(token)
        return unique

    def _fallback_prune(self, code: str, query: str) -> str:
        """Heuristic line pruning that preserves structure and query matches."""
        lines = code.splitlines()
        if not lines:
            return code

        keywords = self._tokenize_query(query)
        structural_prefixes = ("import ", "from ", "class ", "def ", "@", "# ")

        keep = set()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            lower = stripped.lower()
            if stripped.startswith(structural_prefixes):
                keep.add(idx)
            if keywords and any(k in lower for k in keywords):
                keep.add(idx)
                if idx > 0:
                    keep.add(idx - 1)
                if idx + 1 < len(lines):
                    keep.add(idx + 1)

        if not keep:
            # Keep a tiny scaffold so callers always get syntactic context.
            for idx, line in enumerate(lines):
                if line.strip().startswith(structural_prefixes):
                    keep.add(idx)
            if not keep:
                keep.update(range(min(80, len(lines))))

        ordered = sorted(keep)
        return "\n".join(lines[i] for i in ordered)

    def _model_prune(self, code: str, query: str) -> str:
        """Model-backed line relevance scoring with batched inference."""
        lines = code.splitlines()
        if len(lines) <= 10:
            return code

        candidates = [line for line in lines]
        if not candidates:
            return code

        # Compute a relevance score per line using sequence classification.
        scores: list[float] = []
        batch_size = 64
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            prompts = [f"{query}\n\n{line}" for line in batch]
            inputs = self.tokenizer(
                prompts,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            with torch.no_grad():
                outputs = self.model(**inputs)
                logits = outputs.logits
                if logits.shape[-1] == 1:
                    probs = torch.sigmoid(logits).squeeze(-1)
                else:
                    probs = torch.softmax(logits, dim=-1)[:, -1]
                scores.extend(probs.detach().cpu().tolist())

        # Keep top lines plus structural anchors.
        keep_count = max(20, int(len(lines) * 0.35))
        ranked = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)
        keep = set(ranked[:keep_count])

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ", "class ", "def ", "@", "# ")):
                keep.add(idx)

        ordered = sorted(keep)
        return "\n".join(lines[i] for i in ordered)

    async def prune(self, code: str, query: str | None = None) -> tuple[str, dict[str, Any]]:
        """
        Prune code based on query if provided, otherwise return full code.

        Args:
            code: The code content to potentially prune
            query: The context focus question to guide pruning

        Returns:
            Tuple of (result_code, metadata)
        """
        input_size = len(code)

        if not query:
            return code, {
                "pruned": False,
                "reason": "No query provided",
                "tokens": input_size,
            }

        try:
            self._ensure_model_loaded()

            if self.model is not None and self.tokenizer is not None:
                pruned_code = self._model_prune(code, query)
                backend = "model"
            else:
                pruned_code = self._fallback_prune(code, query)
                backend = "heuristic"

            output_size = len(pruned_code)
            compression_ratio = 1 - (output_size / input_size) if input_size > 0 else 0

            # Log operation
            self.logger.log_operation(
                operation="prune",
                input_size=input_size,
                output_size=output_size,
                compression_ratio=round(compression_ratio, 4),
                metadata={
                    "query": query[:100],
                    "backend": backend,
                },
            )

            logger.info(
                f"Pruned: {input_size} -> {output_size} tokens "
                f"({compression_ratio:.1%} reduction)"
            )

            return pruned_code, {
                "pruned": True,
                "tokens": output_size,
                "original_tokens": input_size,
                "compression_ratio": compression_ratio,
                "backend": backend,
            }

        except Exception as e:
            logger.error(f"Pruning failed: {e}, returning full code")
            self.logger.log_operation(
                operation="prune",
                input_size=input_size,
                output_size=input_size,
                compression_ratio=0.0,
                status="error",
                error=str(e),
            )
            return code, {
                "pruned": False,
                "reason": f"Pruning error: {str(e)}",
                "tokens": input_size,
            }

def create_server():
    """Create and return MCP server"""
    app = Server("swe-pruner")

    # Initialize pruner service
    model_path = os.getenv("MODEL_PATH")
    pruner = SWEPrunerService(model_path)

    @app.call_tool()
    async def read_pruned(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Read file contents with optional context-aware pruning"""
        if name != "read_pruned":
            raise ValueError(f"Unknown tool: {name}")

        file_path = arguments.get("file_path")
        if not file_path:
            raise ValueError("Missing required argument: file_path")

        context_focus_question = arguments.get("context_focus_question")
        prune_timeout_seconds = float(os.getenv("PRUNE_TIMEOUT_SECONDS", "30"))

        try:
            path = Path(file_path).expanduser()
            if not path.is_file():
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File not found: {file_path}",
                    )
                ]

            max_file_bytes = int(os.getenv("MAX_FILE_BYTES", "5000000"))
            if path.stat().st_size > max_file_bytes:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File too large ({path.stat().st_size} bytes). "
                        f"MAX_FILE_BYTES={max_file_bytes}",
                    )
                ]

            content = path.read_text(encoding="utf-8", errors="ignore")
            result, metadata = await prune_with_timeout(
                pruner,
                content,
                context_focus_question,
                prune_timeout_seconds,
            )

            result_text = f"/* Tokens: {metadata['tokens']}"
            if metadata.get("pruned"):
                result_text += f" (reduced from {metadata['original_tokens']}, saved {metadata['compression_ratio']:.1%})"
            result_text += f" */\n\n{result}"

            return [TextContent(type="text", text=result_text)]

        except asyncio.TimeoutError:
            return [TextContent(type="text", text="Error: Pruning timed out")]
        except Exception as e:
            logger.error(f"Error in read_pruned: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @app.call_tool()
    async def search_pruned(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Search codebase with optional context-aware pruning"""
        if name != "search_pruned":
            raise ValueError(f"Unknown tool: {name}")

        pattern = arguments.get("pattern")
        context_focus_question = arguments.get("context_focus_question")

        if not pattern:
            raise ValueError("Missing required argument: pattern")

        try:
            search_root = os.getenv("SEARCH_ROOT", ".")
            max_matches = int(os.getenv("MAX_SEARCH_MATCHES", "1000"))
            search_timeout_seconds = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "30"))
            prune_timeout_seconds = float(os.getenv("PRUNE_TIMEOUT_SECONDS", "30"))
            output = await run_rg_search_async(
                pattern,
                search_root,
                max_matches,
                search_timeout_seconds,
            )

            result, metadata = await prune_search_output(
                pruner,
                output,
                context_focus_question,
                prune_timeout_seconds,
            )

            result_text = f"/* Tokens: {metadata['tokens']}"
            if metadata.get("pruned"):
                result_text += f" (reduced from {metadata['original_tokens']}, saved {metadata['compression_ratio']:.1%})"
            result_text += f" */\n\n{result}"

            return [TextContent(type="text", text=result_text)]

        except asyncio.TimeoutError:
            return [TextContent(type="text", text="Error: Search or pruning timed out")]
        except FileNotFoundError:
            return [TextContent(type="text", text="Error: `rg` is not available in PATH")]
        except subprocess.TimeoutExpired:
            return [TextContent(type="text", text="Error: Search timed out after 10 seconds")]
        except Exception as e:
            logger.error(f"Error in search_pruned: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @app.list_tools()
    async def list_tools() -> list[dict[str, Any]]:
        """List available tools"""
        return [
            {
                "name": "read_pruned",
                "description": "Read file contents with optional context-aware pruning based on a focus question. "
                "If no context_focus_question is provided, returns full content. "
                "If provided, returns only content relevant to the question, saving tokens.",
                "inputSchema": {
                    "type": "object",
                    "required": ["file_path"],
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file to read",
                        },
                        "context_focus_question": {
                            "type": "string",
                            "description": "Optional question to guide pruning. "
                            "Only code relevant to this question will be returned. "
                            "If not provided, full file content is returned.",
                        },
                    },
                },
            },
            {
                "name": "search_pruned",
                "description": "Search codebase for a pattern with optional context-aware pruning. "
                "If no context_focus_question is provided, returns all matches. "
                "If provided, returns only matches relevant to the question.",
                "inputSchema": {
                    "type": "object",
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Pattern to search for (regex supported)",
                        },
                        "context_focus_question": {
                            "type": "string",
                            "description": "Optional question to guide pruning. "
                            "Only matches relevant to this question will be returned.",
                        },
                    },
                },
            },
        ]

    return app


async def async_main():
    """Async entry point for MCP server."""
    from mcp.server.stdio import stdio_server

    app = create_server()

    logger.info("Starting SWE-Pruner MCP server...")
    logger.info(f"Model path: {os.getenv('MODEL_PATH', 'not set, will use HuggingFace')}")
    logger.info(f"Stats file: {os.getenv('STATS_FILE', 'not set')}")

    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


def main():
    """Synchronous console entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
