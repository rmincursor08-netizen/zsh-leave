from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.core.management import call_command
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle


DATABASE_CACHE_SETTINGS = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "zsh_cache_provisioning_test",
    },
}


class CacheTableProvisioningTests(TransactionTestCase):
    @override_settings(CACHES=DATABASE_CACHE_SETTINGS)
    def test_provisioning_creates_table_and_is_safe_to_repeat(self):
        table_name = caches["default"]._table
        self.assertNotIn(table_name, connection.introspection.table_names())

        call_command("provision_cache_table", verbosity=0)
        self.assertIn(table_name, connection.introspection.table_names())

        caches["default"].set("existing-entry", "preserved", timeout=60)
        call_command("provision_cache_table", verbosity=0)
        self.assertEqual(caches["default"].get("existing-entry"), "preserved")

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL advisory locks.")
    @override_settings(CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "zsh_cache_concurrent_provisioning_test",
        },
    })
    def test_concurrent_provisioning_is_serialized(self):
        def provision():
            connections.close_all()
            try:
                call_command("provision_cache_table", verbosity=0)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: provision(), range(2)))

        self.assertEqual(results, [None, None])


class CacheReadinessTests(TestCase):
    def test_missing_database_cache_table_is_not_ready(self):
        with override_settings(CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": "zsh_cache_readiness_missing_test",
            },
        }):
            table_name = caches["default"]._table
            self.assertNotIn(table_name, connection.introspection.table_names())

            response = APIClient().get("/api/v1/ready/")

        self.assertEqual(response.status_code, 503)
        checks = {check["name"]: check["ok"] for check in response.json()["checks"]}
        self.assertFalse(checks["cache"])

    def test_unavailable_cache_is_not_ready(self):
        with patch("leave.views.cache") as unavailable_cache:
            unavailable_cache.set.side_effect = ConnectionError("cache unavailable")
            response = APIClient().get("/api/v1/ready/")

        self.assertEqual(response.status_code, 503)
        checks = {check["name"]: check["ok"] for check in response.json()["checks"]}
        self.assertFalse(checks["cache"])

    def test_readiness_cache_probe_succeeds_with_shared_cache_backend(self):
        response = APIClient().get("/api/v1/ready/")

        self.assertEqual(response.status_code, 200)
        checks = {check["name"]: check["ok"] for check in response.json()["checks"]}
        self.assertTrue(checks["cache"])

    def test_required_attachment_scanner_configuration_affects_readiness(self):
        client = APIClient()
        with override_settings(
            ZSH_ATTACHMENT_SCANNER_REQUIRED=True,
            ZSH_ATTACHMENT_SCANNER_COMMAND=[],
        ):
            unavailable = client.get("/api/v1/ready/")
        self.assertEqual(unavailable.status_code, 503)
        checks = {check["name"]: check["ok"] for check in unavailable.json()["checks"]}
        self.assertFalse(checks["attachment_scanner"])

        with override_settings(
            ZSH_ATTACHMENT_SCANNER_REQUIRED=True,
            ZSH_ATTACHMENT_SCANNER_COMMAND=["/usr/bin/true"],
        ):
            configured = client.get("/api/v1/ready/")
        self.assertEqual(configured.status_code, 200)
        checks = {check["name"]: check["ok"] for check in configured.json()["checks"]}
        self.assertTrue(checks["attachment_scanner"])


class SharedDatabaseThrottleTests(TestCase):
    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL.")
    @override_settings(CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "zsh_cache_worker_throttle_test",
        },
    })
    def test_rate_limit_is_shared_by_independent_cache_instances(self):
        call_command("provision_cache_table", verbosity=0)
        table_name = caches["default"]._table
        worker_one_cache = DatabaseCache(table_name, {})
        worker_two_cache = DatabaseCache(table_name, {})
        first_worker = APIClient()
        second_worker = APIClient()
        first_worker.defaults["REMOTE_ADDR"] = "203.0.113.45"
        second_worker.defaults["REMOTE_ADDR"] = "203.0.113.45"

        with patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"anon": "1/min"}):
            with patch.object(SimpleRateThrottle, "cache", worker_one_cache):
                first_response = first_worker.get("/api/v1/auth/me")
            with patch.object(SimpleRateThrottle, "cache", worker_two_cache):
                second_response = second_worker.get("/api/v1/auth/me")

        self.assertEqual(first_response.status_code, 401)
        self.assertEqual(second_response.status_code, 429)
