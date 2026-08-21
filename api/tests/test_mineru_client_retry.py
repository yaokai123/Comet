import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.knowledge.mineru_adapter import MinerUClient
from app.config import settings


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, request=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"content_list": []}
        self.headers = headers or {}
        self.request = request or httpx.Request("POST", "http://127.0.0.1:18080/parse")
        self.text = "temporary error"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=self.request, response=self
            )

    def json(self):
        return self._payload


class MinerUClientRetryTests(unittest.TestCase):
    def test_timeout_is_shared_by_all_retry_attempts(self):
        async def run():
            client = MinerUClient("http://127.0.0.1:18080/parse")

            async def slow_post(*args, **kwargs):
                await asyncio.sleep(0.05)
                return _FakeResponse(status_code=502)

            fake_http_client = AsyncMock()
            fake_http_client.post.side_effect = slow_post
            fake_http_client.__aenter__.return_value = fake_http_client
            fake_http_client.__aexit__.return_value = None

            with patch.object(settings, "mineru_timeout_seconds", 0.01), patch(
                "app.core.knowledge.mineru_adapter.httpx.AsyncClient",
                return_value=fake_http_client,
            ):
                with self.assertRaises(TimeoutError):
                    await client.parse("annual.pdf", b"pdf")

            self.assertEqual(fake_http_client.post.await_count, 1)

        asyncio.run(run())

    def test_retries_transient_failures_then_succeeds(self):
        async def run():
            client = MinerUClient("http://127.0.0.1:18080/parse")
            post = AsyncMock(
                side_effect=[
                    _FakeResponse(status_code=502),
                    httpx.ReadTimeout("timeout"),
                    _FakeResponse(
                        status_code=200,
                        payload={
                            "content_list": [{"type": "text", "text": "ok"}],
                            "version": "2.0",
                        },
                        headers={"x-mineru-version": "2.0"},
                    ),
                ]
            )
            fake_http_client = AsyncMock()
            fake_http_client.post = post
            fake_http_client.__aenter__.return_value = fake_http_client
            fake_http_client.__aexit__.return_value = None

            with patch("app.core.knowledge.mineru_adapter.httpx.AsyncClient", return_value=fake_http_client), patch(
                "app.core.knowledge.mineru_adapter.asyncio.sleep", new=AsyncMock()
            ) as sleep:
                content_list, version = await client.parse("annual.pdf", b"pdf")

            self.assertEqual(content_list, [{"type": "text", "text": "ok"}])
            self.assertEqual(version, "2.0")
            self.assertEqual(post.await_count, 3)
            self.assertEqual(sleep.await_count, 2)

        asyncio.run(run())

    def test_does_not_retry_non_transient_http_errors(self):
        async def run():
            client = MinerUClient("http://127.0.0.1:18080/parse")
            post = AsyncMock(side_effect=[_FakeResponse(status_code=400)])
            fake_http_client = AsyncMock()
            fake_http_client.post = post
            fake_http_client.__aenter__.return_value = fake_http_client
            fake_http_client.__aexit__.return_value = None

            with patch("app.core.knowledge.mineru_adapter.httpx.AsyncClient", return_value=fake_http_client), patch(
                "app.core.knowledge.mineru_adapter.asyncio.sleep", new=AsyncMock()
            ) as sleep:
                with self.assertRaises(httpx.HTTPStatusError):
                    await client.parse("annual.pdf", b"pdf")

            self.assertEqual(post.await_count, 1)
            self.assertEqual(sleep.await_count, 0)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
