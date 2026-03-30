import asyncio
import json
import os
from inspect import iscoroutine
from unittest.mock import patch

from swe_pruner_mcp import server
from swe_pruner_mcp.server import SWEPrunerService, run_rg_search


def test_tokenize_query_filters_noise():
    tokens = SWEPrunerService._tokenize_query(
        "How is authentication handled in this file and where is the user class defined?"
    )
    assert "authentication" in tokens
    assert "user" in tokens
    assert "class" in tokens
    assert "the" not in tokens


def test_fallback_prune_keeps_structural_and_keyword_lines(tmp_path):
    os.environ["STATS_FILE"] = str(tmp_path / "stats.json")
    service = SWEPrunerService(model_path="/tmp/non-existent")
    service._model_load_attempted = True

    code = "\n".join(
        [
            "import os",
            "def login(user):",
            "    token = create_token(user)",
            "    return token",
            "",
            "def other():",
            "    return 1",
        ]
    )

    pruned = service._fallback_prune(code, "How is login token handled")
    assert "import os" in pruned
    assert "def login(user):" in pruned
    assert "token = create_token(user)" in pruned


def test_prune_without_query_returns_full_content(tmp_path):
    os.environ["STATS_FILE"] = str(tmp_path / "stats.json")
    service = SWEPrunerService(model_path="/tmp/non-existent")
    service._model_load_attempted = True

    code = "line1\nline2"
    result, metadata = asyncio.run(service.prune(code, query=None))

    assert result == code
    assert metadata["pruned"] is False
    assert metadata["reason"] == "No query provided"


def test_prune_with_query_uses_heuristic_when_model_unavailable(tmp_path):
    os.environ["STATS_FILE"] = str(tmp_path / "stats.json")
    service = SWEPrunerService(model_path="/tmp/non-existent")
    service._model_load_attempted = True

    code = "\n".join(
        ["def auth():", "    verify_token()", "    return True", "def noop():", "    pass"]
    )
    result, metadata = asyncio.run(service.prune(code, query="token auth"))

    assert metadata["pruned"] is True
    assert metadata["backend"] == "heuristic"
    assert "verify_token" in result


def test_prune_falls_back_when_model_dependencies_fail(tmp_path):
    os.environ["STATS_FILE"] = str(tmp_path / "stats.json")
    service = SWEPrunerService(model_path="/tmp/non-existent")

    with patch.object(service, "_ensure_model_dependencies", side_effect=ImportError("missing deps")):
        result, metadata = asyncio.run(
            service.prune(
                "\n".join(["def auth():", "    verify_token()", "    return True"]),
                query="token auth",
            )
        )

    assert metadata["pruned"] is True
    assert metadata["backend"] == "heuristic"
    assert "verify_token" in result


def test_prune_does_not_attempt_remote_download_by_default(tmp_path):
    with patch.dict(
        os.environ,
        {
            "STATS_FILE": str(tmp_path / "stats.json"),
            "ALLOW_REMOTE_MODEL_DOWNLOAD": "",
        },
        clear=False,
    ):
        service = SWEPrunerService()

        with patch.object(service, "_ensure_model_dependencies") as deps_mock:
            result, metadata = asyncio.run(
                service.prune(
                    "\n".join(["def auth():", "    verify_token()", "    return True"]),
                    query="token auth",
                )
            )

    deps_mock.assert_not_called()
    assert metadata["pruned"] is True
    assert metadata["backend"] == "heuristic"
    assert "verify_token" in result


def test_prune_can_attempt_remote_download_when_enabled(tmp_path):
    with patch.dict(
        os.environ,
        {
            "STATS_FILE": str(tmp_path / "stats.json"),
            "ALLOW_REMOTE_MODEL_DOWNLOAD": "1",
        },
        clear=False,
    ):
        service = SWEPrunerService()

        with patch.object(
            service,
            "_ensure_model_dependencies",
            side_effect=ImportError("missing deps"),
        ) as deps_mock:
            result, metadata = asyncio.run(
                service.prune(
                    "\n".join(["def auth():", "    verify_token()", "    return True"]),
                    query="token auth",
                )
            )

    deps_mock.assert_called_once()
    assert metadata["pruned"] is True
    assert metadata["backend"] == "heuristic"
    assert "verify_token" in result


def test_prune_survives_stats_logging_failures(tmp_path):
    os.environ["STATS_FILE"] = str(tmp_path / "stats.json")
    service = SWEPrunerService(model_path="/tmp/non-existent")
    service._model_load_attempted = True

    with patch.object(service.logger, "_write_stats", side_effect=PermissionError("no write")):
        result, metadata = asyncio.run(
            service.prune(
                "\n".join(["def auth():", "    verify_token()", "    return True"]),
                query="token auth",
            )
        )

    assert metadata["pruned"] is True
    assert metadata["backend"] == "heuristic"
    assert "verify_token" in result
    assert service.logger.enabled is False


def test_prune_writes_stats_file_with_compression_ratio(tmp_path):
    stats_file = tmp_path / "stats.json"
    os.environ["STATS_FILE"] = str(stats_file)
    service = SWEPrunerService(model_path="/tmp/non-existent")
    service._model_load_attempted = True

    code = "\n".join(
        ["def auth():", "    verify_token()", "    return True", "def noop():", "    pass"]
    )
    _, _ = asyncio.run(service.prune(code, query="auth token"))

    assert stats_file.exists()
    entries = json.loads(stats_file.read_text())
    assert len(entries) >= 1
    assert entries[-1]["operation"] == "prune"
    assert "compression_ratio" in entries[-1]


def test_run_rg_search_returns_matches(tmp_path):
    sample = tmp_path / "a.py"
    sample.write_text("def login_user():\n    return 1\n", encoding="utf-8")
    output = run_rg_search("login_user", str(tmp_path), 100)
    assert "login_user" in output


def test_run_rg_search_returns_no_match_message(tmp_path):
    sample = tmp_path / "a.py"
    sample.write_text("def login_user():\n    return 1\n", encoding="utf-8")
    output = run_rg_search("does_not_exist_123", str(tmp_path), 100)
    assert "No matches found for pattern" in output


def test_main_wraps_async_entrypoint():
    with (
        patch.object(server.asyncio, "run") as run_mock,
        patch.object(server, "async_main") as async_main_mock,
    ):
        server.main()

    async_main_mock.assert_called_once_with()
    run_mock.assert_called_once()
    coroutine = run_mock.call_args.args[0]
    assert iscoroutine(coroutine)
    coroutine.close()
