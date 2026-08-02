"""Focused regression checks for the P1/P2 auth and task-resilience controls."""
import asyncio
import unittest
from uuid import uuid4

from app.core.security import create_access_token, create_refresh_token, decode_token
from app.core.task_lock import redis_task_lock


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, keys, key, token):
        if self.values.get(key) == token:
            self.values.pop(key)
            return 1
        return 0


class P1P2ResilienceTests(unittest.TestCase):
    def test_tokens_carry_type_version_and_unique_id(self):
        access = decode_token(create_access_token(str(uuid4()), token_version=7))
        refresh = decode_token(create_refresh_token(str(uuid4()), token_version=7))
        self.assertEqual(access["type"], "access")
        self.assertEqual(refresh["type"], "refresh")
        self.assertEqual(access["tv"], 7)
        self.assertIn("jti", access)

    def test_task_lock_rejects_second_delivery_until_first_releases(self):
        import app.core.task_lock as lock_module

        fake = _FakeRedis()
        original = lock_module.get_redis
        lock_module.get_redis = lambda: fake

        async def run():
            async with redis_task_lock("document:1") as first:
                self.assertTrue(first)
                async with redis_task_lock("document:1") as second:
                    self.assertFalse(second)
            async with redis_task_lock("document:1") as third:
                self.assertTrue(third)

        try:
            asyncio.run(run())
        finally:
            lock_module.get_redis = original


if __name__ == "__main__":
    unittest.main()
