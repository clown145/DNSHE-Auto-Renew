"""Offline regression tests: no DNSHE or SMTP requests are made."""
import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

# This harness does not require requests to be installed.
sys.modules.setdefault('requests', types.ModuleType('requests'))
import renew_domains as renew

def response(data=None, error=None):
    result = Mock()
    result.json.return_value = data
    result.raise_for_status.side_effect = error
    return result

def domain(days=1, **extra):
    result = dict(id=1, full_domain='example.invalid',
                  expires_at=(datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S'))
    result.update(extra)
    return result

class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(renew, 'API_KEY', 'test-only'),
            patch.object(renew, 'API_SECRET', 'test-only'),
            patch.object(renew, 'requests', Mock()),
            patch.object(renew, 'send_smtp', return_value=True),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        renew.requests.get.return_value = response({'subdomains': []})

    def test_empty_valid_list(self):
        self.assertEqual(renew.main(), 0)
        renew.requests.post.assert_not_called()

    def test_missing_credentials(self):
        renew.API_KEY = None
        self.assertEqual(renew.main(), 1)
        renew.requests.get.assert_not_called()

    def test_list_failures(self):
        for data in ({'success': False}, {}, {'subdomains': None}, []):
            with self.subTest(data=data):
                renew.requests.get.return_value = response(data)
                self.assertEqual(renew.main(), 1)

    def test_list_http_failure(self):
        renew.requests.get.return_value = response(error=RuntimeError('HTTP 401'))
        self.assertEqual(renew.main(), 1)

    def test_list_network_failure(self):
        renew.requests.get.side_effect = RuntimeError('timeout')
        self.assertEqual(renew.main(), 1)

    def test_skips_long_lived_and_permanent(self):
        renew.requests.get.return_value = response({'subdomains': [domain(181), domain(never_expires=1)]})
        self.assertEqual(renew.main(), 0)
        renew.requests.post.assert_not_called()

    def test_renewal_success(self):
        renew.requests.get.return_value = response({'subdomains': [domain()]})
        renew.requests.post.return_value = response({'success': True})
        self.assertEqual(renew.main(), 0)
        self.assertEqual(renew.requests.post.call_args.kwargs['timeout'], 30)

    def test_renewal_failure_continues_other_domains(self):
        renew.requests.get.return_value = response({'subdomains': [domain(), domain()]})
        renew.requests.post.side_effect = [response({'success': False}), response({'success': True})]
        self.assertEqual(renew.main(), 1)
        self.assertEqual(renew.requests.post.call_count, 2)

    def test_renewal_http_or_network_failure(self):
        renew.requests.get.return_value = response({'subdomains': [domain()]})
        renew.requests.post.return_value = response(error=RuntimeError('HTTP 500'))
        self.assertEqual(renew.main(), 1)
        renew.requests.post.side_effect = RuntimeError('timeout')
        self.assertEqual(renew.main(), 1)

    def test_notification_failure(self):
        renew.send_smtp.return_value = False
        self.assertEqual(renew.main(), 1)

class SmtpTests(unittest.TestCase):
    def test_unconfigured_optional_notification(self):
        with patch.multiple(renew, SMTP_HOST=None, SMTP_TO=None):
            self.assertTrue(renew.send_smtp('test'))

    def test_configured_notification_failure(self):
        with patch.multiple(renew, SMTP_HOST='smtp.invalid', SMTP_TO='to@example.invalid',
                            SMTP_FROM='from@example.invalid', SMTP_USER=None, SMTP_PASSWORD=None), \
             patch.object(renew.smtplib, 'SMTP_SSL', side_effect=RuntimeError('offline')):
            self.assertFalse(renew.send_smtp('test'))

if __name__ == '__main__':
    unittest.main()
