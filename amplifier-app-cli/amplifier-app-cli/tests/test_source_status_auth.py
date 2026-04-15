"""Tests for GitHub auth handling in source_status update checks.

Verifies that auth headers are used proactively (on first request) to
avoid GitHub's 60 req/hr unauthenticated rate limit, and that error
messages correctly distinguish rate limiting from true 404s.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch


class TestGetGithubCommitShaUsesAuthProactively:
    """_get_github_commit_sha should send auth headers on the first request."""

    @pytest.mark.asyncio
    async def test_uses_auth_headers_on_first_request_when_available(self):
        """Auth headers should be sent on the first request, not only after a 404."""
        from amplifier_app_cli.utils.source_status import _get_github_commit_sha

        atom_content = """<?xml version="1.0"?>
<feed>
  <entry>
    <id>tag:github.com,2008:Grit::Commit/abcdef1234567890abcdef1234567890abcdef12</id>
  </entry>
</feed>"""

        captured_requests = []

        async def mock_get(url, **kwargs):
            captured_requests.append({"url": url, "headers": kwargs.get("headers", {})})
            response = MagicMock(spec=httpx.Response)
            response.status_code = 200
            response.text = atom_content
            response.raise_for_status = MagicMock()
            return response

        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=mock_get)

        with patch(
            "amplifier_app_cli.utils.source_status._get_github_auth_headers",
            return_value={"Authorization": "Bearer test-token"},
        ):
            result = await _get_github_commit_sha(
                mock_client,
                "https://github.com/microsoft/amplifier",
                "main",
            )

        assert result == "abcdef1234567890abcdef1234567890abcdef12"
        # Must have made exactly one request
        assert len(captured_requests) == 1
        # Auth header must be present on the FIRST (and only) request
        assert "Authorization" in captured_requests[0]["headers"], (
            "Expected auth headers on first request, "
            f"but headers were: {captured_requests[0]['headers']}"
        )
        assert captured_requests[0]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_proceeds_without_auth_when_unavailable(self):
        """Requests without auth should still work when no token is configured."""
        from amplifier_app_cli.utils.source_status import _get_github_commit_sha

        atom_content = """<?xml version="1.0"?>
<feed>
  <entry>
    <id>tag:github.com,2008:Grit::Commit/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef</id>
  </entry>
</feed>"""

        async def mock_get(url, **kwargs):
            response = MagicMock(spec=httpx.Response)
            response.status_code = 200
            response.text = atom_content
            response.raise_for_status = MagicMock()
            return response

        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=mock_get)

        with patch(
            "amplifier_app_cli.utils.source_status._get_github_auth_headers",
            return_value={},
        ):
            result = await _get_github_commit_sha(
                mock_client,
                "https://github.com/microsoft/amplifier",
                "main",
            )

        assert result == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        # Should make exactly one request (no retry needed)
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_no_second_unauthenticated_retry_after_404(self):
        """Old code retried without auth after 404; new code should not do a second retry."""
        from amplifier_app_cli.utils.source_status import _get_github_commit_sha

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            response = MagicMock(spec=httpx.Response)
            response.status_code = 200 if call_count == 2 else 404
            response.text = ""
            request = MagicMock()
            response.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "404", request=request, response=response
                )
                if response.status_code == 404
                else None
            )
            return response

        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=mock_get)

        with patch(
            "amplifier_app_cli.utils.source_status._get_github_auth_headers",
            return_value={"Authorization": "Bearer test-token"},
        ):
            # With auth provided, a 404 is a real 404 — no retry logic needed
            with pytest.raises(httpx.HTTPStatusError):
                await _get_github_commit_sha(
                    mock_client,
                    "https://github.com/microsoft/amplifier",
                    "main",
                )

        # Should only make ONE request — no second unauthenticated retry
        assert call_count == 1, (
            f"Expected 1 request (no retry), but made {call_count} requests. "
            "New code should not retry after 404 since auth is already sent."
        )


class TestErrorMessageDistinguishesRateLimitFromPrivateRepo:
    """Error messages should clearly distinguish rate limiting from private repos."""

    def test_error_message_mentions_rate_limiting_when_auth_available(self):
        """When auth IS set but 404 still occurs, message should say 'rate limited'."""
        import pathlib

        source = pathlib.Path("amplifier_app_cli/utils/source_status.py").read_text()
        assert "rate limit" in source.lower() or "rate limiting" in source.lower(), (
            "Source code should mention rate limiting in error handling, "
            "not just 'private repo or not found'"
        )

    def test_handles_403_status_code(self):
        """Error handler should explicitly handle 403 (explicit rate limit response)."""
        import pathlib

        source = pathlib.Path("amplifier_app_cli/utils/source_status.py").read_text()
        assert "403" in source, (
            "HTTPStatusError handler should check for 403 "
            "(GitHub's explicit rate limit response)"
        )

    def test_docstring_does_not_claim_no_rate_limits(self):
        """The function docstring should not claim there are no rate limits (it's wrong)."""
        from amplifier_app_cli.utils.source_status import _get_github_commit_sha
        import inspect

        doc = inspect.getdoc(_get_github_commit_sha) or ""
        assert "no rate limits" not in doc.lower(), (
            "Docstring falsely claims 'no rate limits' — GitHub Atom feeds "
            "DO have rate limits for unauthenticated requests"
        )
