import socket
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.config import Settings
from app.core.rag.web_crawler import _is_safe_url


class ProductionConfigTests(unittest.TestCase):
    def test_production_rejects_development_defaults(self):
        settings = Settings(app_env="production")

        with self.assertRaisesRegex(RuntimeError, "Unsafe production configuration"):
            settings.validate_runtime()

    def test_production_accepts_explicit_secure_settings(self):
        settings = Settings(
            app_env="production",
            app_debug=False,
            jwt_secret="a" * 48,
            fernet_key=Fernet.generate_key().decode(),
            postgres_password="postgres-production-secret",
            neo4j_password="neo4j-production-secret",
            cors_origins="https://comet.example.com",
        )

        settings.validate_runtime()


class UrlSafetyTests(unittest.TestCase):
    def test_private_resolved_address_is_rejected(self):
        result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0))]
        with patch("app.core.rag.web_crawler.socket.getaddrinfo", return_value=result):
            self.assertFalse(_is_safe_url("https://public.example.com/article"))

    def test_non_http_scheme_is_rejected_without_dns_lookup(self):
        self.assertFalse(_is_safe_url("file:///etc/passwd"))


if __name__ == "__main__":
    unittest.main()
