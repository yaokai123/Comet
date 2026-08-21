import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from cryptography.fernet import InvalidToken

from app.core.llm.resolver import get_optional_client_for_type


class OptionalResolverFallbackTests(unittest.TestCase):
    def test_optional_client_returns_none_on_invalid_encrypted_secret(self):
        async def run():
            config = SimpleNamespace(
                is_default=True,
                base_url="https://example.com",
                api_key_encrypted="broken",
                extra_headers_encrypted=None,
                model_name="gpt-test",
                wire_api="chat_completions",
                reasoning_effort=None,
                store_responses=False,
            )
            fake_repo = SimpleNamespace(list_by_user=AsyncMock(return_value=[config]))
            with patch("app.core.llm.resolver.ModelConfigRepository", return_value=fake_repo), patch(
                "app.core.llm.resolver.decrypt_secret", side_effect=InvalidToken()
            ):
                client = await get_optional_client_for_type(object(), uuid4(), "chat")
            self.assertIsNone(client)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
