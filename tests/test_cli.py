"""Unit and integration tests for speechify_add/cli.py

Covers the parts not already tested in test_pure_logic.py:
- _precheck_url (HTTP HEAD auth check)
- _fetch_google_doc_text (Google Doc export errors)
- CLI commands via click.testing.CliRunner
- _do_progress_batch (JSON parsing and output format)
- _do_verify (search term extraction logic)
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from speechify_add.cli import (
    _do_progress_batch,
    _do_verify,
    _fetch_google_doc_text,
    _precheck_url,
    cli,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def _mock_httpx_client(status_code, text=""):
    """Build a mock httpx.AsyncClient context manager returning a fixed response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_resp)
    mock_client.get = AsyncMock(return_value=mock_resp)

    mock_class = MagicMock()
    mock_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_class.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_class


# ---------------------------------------------------------------------------
# _precheck_url
# ---------------------------------------------------------------------------

class TestPrecheckUrl:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_status_raises_runtime_error(self, status):
        with patch("httpx.AsyncClient", _mock_httpx_client(status)):
            with pytest.raises(RuntimeError, match=str(status)):
                run(_precheck_url("https://example.com/private"))

    def test_200_returns_none(self):
        with patch("httpx.AsyncClient", _mock_httpx_client(200)):
            result = run(_precheck_url("https://example.com/public"))
        assert result is None

    def test_network_error_is_swallowed(self):
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(side_effect=httpx.HTTPError("connection reset"))
        mock_class = MagicMock()
        mock_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_class.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", mock_class):
            result = run(_precheck_url("https://example.com/flaky"))
        assert result is None

    def test_404_does_not_raise(self):
        # 404 is not an auth error — Speechify can still try it
        with patch("httpx.AsyncClient", _mock_httpx_client(404)):
            result = run(_precheck_url("https://example.com/missing"))
        assert result is None


# ---------------------------------------------------------------------------
# _fetch_google_doc_text
# ---------------------------------------------------------------------------

_GDOC_URL = "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"


class TestFetchGoogleDocText:
    @pytest.mark.parametrize("status", [401, 403])
    def test_private_doc_raises_with_helpful_message(self, status):
        with patch("httpx.AsyncClient", _mock_httpx_client(status)):
            with pytest.raises(RuntimeError, match="private"):
                run(_fetch_google_doc_text(_GDOC_URL))

    def test_404_raises_not_found(self):
        with patch("httpx.AsyncClient", _mock_httpx_client(404)):
            with pytest.raises(RuntimeError, match="not found"):
                run(_fetch_google_doc_text(_GDOC_URL))

    def test_500_raises_export_failed(self):
        with patch("httpx.AsyncClient", _mock_httpx_client(500)):
            with pytest.raises(RuntimeError, match="failed"):
                run(_fetch_google_doc_text(_GDOC_URL))

    def test_200_returns_text_content(self):
        with patch("httpx.AsyncClient", _mock_httpx_client(200, "Hello from Google Doc")):
            result = run(_fetch_google_doc_text(_GDOC_URL))
        assert result == "Hello from Google Doc"


# ---------------------------------------------------------------------------
# CLI: add command
# ---------------------------------------------------------------------------

class TestAddCommand:
    def test_no_args_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["add"])
        assert result.exit_code == 0
        assert "Usage" in result.output

    def test_single_url_succeeds_and_prints_checkmark(self):
        runner = CliRunner()
        with patch("speechify_add.cli._run", return_value=None):
            result = runner.invoke(cli, ["add", "https://example.com/article"])
        assert result.exit_code == 0
        assert "✓" in result.output
        assert "https://example.com/article" in result.output

    def test_single_url_failure_exits_1_and_prints_error(self):
        runner = CliRunner()
        with patch("speechify_add.cli._run", side_effect=Exception("browser crashed")):
            result = runner.invoke(cli, ["add", "https://example.com/bad"])
        assert result.exit_code == 1
        assert "✗" in result.output

    def test_multiple_urls_calls_batch_once(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text("https://a.com\nhttps://b.com\n")
        runner = CliRunner()
        with patch("speechify_add.cli._run") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(cli, ["add", "--file", str(url_file)])
        # batch: _run called once (not once-per-URL)
        assert mock_run.call_count == 1
        assert result.exit_code == 0

    def test_single_url_api_mode_calls_run(self):
        runner = CliRunner()
        with patch("speechify_add.cli._run", return_value=None) as mock_run:
            result = runner.invoke(cli, ["add", "--mode", "api", "https://example.com/article"])
        assert mock_run.called
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# CLI: text command
# ---------------------------------------------------------------------------

class TestTextCommand:
    def test_no_args_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["text"])
        assert result.exit_code == 0
        assert "Usage" in result.output

    def test_file_arg_prints_doc_url(self, tmp_path):
        content_file = tmp_path / "article.txt"
        content_file.write_text("Some article content for Speechify")
        runner = CliRunner()
        with patch("speechify_add.cli._run", return_value="https://app.speechify.com/item/abc-123"):
            result = runner.invoke(cli, ["text", "--file", str(content_file)])
        assert result.exit_code == 0
        assert "app.speechify.com" in result.output

    def test_browser_error_exits_1_with_error_message(self, tmp_path):
        content_file = tmp_path / "article.txt"
        content_file.write_text("Some content")
        runner = CliRunner()
        with patch("speechify_add.cli._run", side_effect=Exception("Playwright timeout")):
            result = runner.invoke(cli, ["text", "--file", str(content_file)])
        assert result.exit_code == 1
        assert "Error" in result.output


# ---------------------------------------------------------------------------
# CLI: delete command
# ---------------------------------------------------------------------------

_SAMPLE_UUID = "783247eb-59c9-4ade-9027-e01f8d77d959"


class TestDeleteCommand:
    def test_bare_uuid_deletes_and_prints_confirmation(self):
        runner = CliRunner()
        with patch("speechify_add.cli._run", return_value=None):
            result = runner.invoke(cli, ["delete", _SAMPLE_UUID])
        assert result.exit_code == 0
        assert "Deleted" in result.output
        assert _SAMPLE_UUID in result.output

    def test_full_url_extracts_uuid_and_deletes(self):
        runner = CliRunner()
        full_url = f"https://app.speechify.com/item/{_SAMPLE_UUID}"
        with patch("speechify_add.cli._run", return_value=None):
            result = runner.invoke(cli, ["delete", full_url])
        assert result.exit_code == 0
        assert _SAMPLE_UUID in result.output

    def test_api_error_exits_1(self):
        runner = CliRunner()
        with patch("speechify_add.cli._run", side_effect=Exception("API error")):
            result = runner.invoke(cli, ["delete", _SAMPLE_UUID])
        assert result.exit_code == 1
        assert "Error" in result.output


# ---------------------------------------------------------------------------
# CLI: progress command
# ---------------------------------------------------------------------------

class TestProgressCommand:
    def test_no_args_exits_1_with_usage_hint(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["progress"])
        assert result.exit_code == 1

    def test_single_title_calls_run(self):
        runner = CliRunner()
        with patch("speechify_add.cli._run", return_value=None) as mock_run:
            result = runner.invoke(cli, ["progress", "some article title"])
        assert mock_run.called
        assert result.exit_code == 0

    def test_batch_json_flag_calls_run(self):
        runner = CliRunner()
        batch = json.dumps([{"id": "abc-123", "title": "Article One"}])
        with patch("speechify_add.cli._run", return_value=None) as mock_run:
            result = runner.invoke(cli, ["progress", "--batch", batch])
        assert mock_run.called
        assert result.exit_code == 0

    def test_batch_file_flag_calls_run(self, tmp_path):
        batch_file = tmp_path / "batch.json"
        batch_file.write_text(json.dumps([{"id": "id-1", "title": "Title One"}]))
        runner = CliRunner()
        with patch("speechify_add.cli._run", return_value=None) as mock_run:
            result = runner.invoke(cli, ["progress", "--batch-file", str(batch_file)])
        assert mock_run.called
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# _do_progress_batch — JSON parsing and output format
# ---------------------------------------------------------------------------

class TestDoProgressBatch:
    def test_batch_json_outputs_correct_structure(self, capsys):
        batch = [
            {"id": "id-1", "title": "Title One"},
            {"id": "id-2", "title": "Title Two"},
        ]
        batch_str = json.dumps(batch)

        with patch("speechify_add.verify.search_library_batch", AsyncMock(return_value=[50, 100])):
            run(_do_progress_batch(batch_str, None))

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == [
            {"id": "id-1", "listen_pct": 50},
            {"id": "id-2", "listen_pct": 100},
        ]

    def test_batch_file_reads_and_outputs_json(self, tmp_path, capsys):
        batch = [{"id": "file-id", "title": "File Article"}]
        batch_file = tmp_path / "items.json"
        batch_file.write_text(json.dumps(batch))

        with patch("speechify_add.verify.search_library_batch", AsyncMock(return_value=[73])):
            run(_do_progress_batch(None, str(batch_file)))

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == [{"id": "file-id", "listen_pct": 73}]

    def test_missing_id_defaults_to_empty_string(self, capsys):
        batch = json.dumps([{"title": "No ID Here"}])

        with patch("speechify_add.verify.search_library_batch", AsyncMock(return_value=[25])):
            run(_do_progress_batch(batch, None))

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output[0]["id"] == ""
        assert output[0]["listen_pct"] == 25


# ---------------------------------------------------------------------------
# _do_verify — search term extraction
# ---------------------------------------------------------------------------

class TestDoVerify:
    def test_plain_query_searches_directly(self, capsys):
        with patch("speechify_add.verify.search_library", AsyncMock(return_value=[
            {"title": "Cosmic Distance Ladder", "meta": "ars.technica.com"}
        ])):
            run(_do_verify("cosmic distance ladder"))

        captured = capsys.readouterr()
        assert "Cosmic Distance Ladder" in captured.out

    def test_url_with_good_title_extracts_first_6_words(self, capsys):
        long_title = "The Quick Brown Fox Jumps Over The Lazy Dog Extra Words"
        with patch("speechify_add.verify.get_page_title", AsyncMock(return_value=long_title)):
            with patch("speechify_add.verify.search_library", AsyncMock(return_value=[
                {"title": "The Quick Brown Fox", "meta": ""}
            ])) as mock_search:
                run(_do_verify("https://example.com/article"))

        # Should use first 6 words longer than 2 chars
        call_args = mock_search.call_args[0][0]
        words = call_args.split()
        assert len(words) <= 6

    def test_url_with_404_title_raises_systemexit(self):
        with patch("speechify_add.verify.get_page_title", AsyncMock(return_value="404 Not Found")):
            with pytest.raises(SystemExit):
                run(_do_verify("https://example.com/gone"))

    def test_url_with_empty_title_falls_back_to_url_path(self, capsys):
        with patch("speechify_add.verify.get_page_title", AsyncMock(return_value=None)):
            with patch("speechify_add.verify.search_library", AsyncMock(return_value=[
                {"title": "Some Result", "meta": ""}
            ])) as mock_search:
                run(_do_verify("https://example.com/my-article-about-science"))

        call_args = mock_search.call_args[0][0]
        # Should contain words from the URL path
        assert "article" in call_args or "science" in call_args or "my" in call_args

    def test_no_results_raises_systemexit(self):
        with patch("speechify_add.verify.search_library", AsyncMock(return_value=[])):
            with pytest.raises(SystemExit):
                run(_do_verify("nothing found here"))


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegrationCollectUrlsFile:
    def test_file_with_comments_and_blanks(self, tmp_path):
        """_collect_urls from a real file skips comments and blank lines."""
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "# Header comment\n"
            "https://example.com/a\n"
            "\n"
            "  \n"
            "# Another comment\n"
            "https://example.com/b\n"
        )
        runner = CliRunner()
        with patch("speechify_add.cli._run") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(cli, ["add", "--file", str(url_file)])
        # Two URLs → batch mode: _run called once
        assert mock_run.call_count == 1
        assert result.exit_code == 0

    def test_integration_progress_batch_file_full_flow(self, tmp_path, capsys):
        """_do_progress_batch reads file, calls verify, and outputs valid JSON."""
        items = [
            {"id": "uuid-1", "title": "Article Alpha"},
            {"id": "uuid-2", "title": "Article Beta"},
        ]
        batch_file = tmp_path / "batch.json"
        batch_file.write_text(json.dumps(items))

        with patch("speechify_add.verify.search_library_batch", AsyncMock(return_value=[10, None])):
            run(_do_progress_batch(None, str(batch_file)))

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert len(output) == 2
        assert output[0] == {"id": "uuid-1", "listen_pct": 10}
        assert output[1] == {"id": "uuid-2", "listen_pct": None}

    def test_integration_add_single_url_failure_still_prints_error_url(self):
        """When a URL fails, the error output includes the URL that failed."""
        runner = CliRunner()
        with patch("speechify_add.cli._run", side_effect=Exception("network timeout")):
            result = runner.invoke(cli, ["add", "https://example.com/article"])
        assert "https://example.com/article" in result.output
        assert result.exit_code == 1
