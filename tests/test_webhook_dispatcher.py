"""Tests for webhook dispatcher — push tasks to remote agents."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.platform.webhook_dispatcher import WebhookDispatcher


@pytest.fixture
def dispatcher():
    return WebhookDispatcher(timeout=5)


class TestWebhookDispatcher:
    async def test_dispatch_success(self, dispatcher):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await dispatcher.dispatch(
                {"job_id": "j1", "task": "test"},
                "https://agent.example.com",
            )
            assert result is True
            mock_client.post.assert_called_once_with(
                "https://agent.example.com/a2a/tasks/receive",
                json={"job_id": "j1", "task": "test"},
            )

    async def test_dispatch_failure(self, dispatcher):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await dispatcher.dispatch(
                {"job_id": "j1"},
                "https://agent.example.com",
            )
            assert result is False

    async def test_dispatch_connection_error(self, dispatcher):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await dispatcher.dispatch(
                {"job_id": "j1"},
                "https://dead.example.com",
            )
            assert result is False

    async def test_dispatch_with_retry_succeeds_on_second(self, dispatcher):
        call_count = 0

        async def mock_dispatch(task_data, endpoint):
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        dispatcher.dispatch = mock_dispatch
        result = await dispatcher.dispatch_with_retry({"job_id": "j1"}, "https://x.com", max_retries=3)
        assert result is True
        assert call_count == 2

    async def test_dispatch_with_retry_exhausted(self, dispatcher):
        dispatcher.dispatch = AsyncMock(return_value=False)
        result = await dispatcher.dispatch_with_retry(
            {"job_id": "j1"}, "https://x.com", max_retries=1,
        )
        assert result is False

    async def test_dispatch_batch(self, dispatcher):
        dispatcher.dispatch_with_retry = AsyncMock(return_value=True)
        tasks = [
            ({"job_id": "j1"}, "https://a.com"),
            ({"job_id": "j2"}, "https://b.com"),
        ]
        results = await dispatcher.dispatch_batch(tasks)
        assert results == {"j1": True, "j2": True}
