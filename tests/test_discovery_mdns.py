"""Tests fuer den Schalter der mDNS-Ankuendigung (`discovery.mdns`)."""

import unittest
from unittest import mock

from vision_server.discovery import mdns


class EnabledTest(unittest.TestCase):
    def test_default_ist_aus(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(mdns.enabled())

    def test_env_schaltet_ein(self):
        for wert in ("1", "true", "Yes", " on "):
            with mock.patch.dict("os.environ", {"OPCUA_MDNS": wert}):
                self.assertTrue(mdns.enabled(), wert)

    def test_andere_werte_bleiben_aus(self):
        for wert in ("", "0", "false", "off"):
            with mock.patch.dict("os.environ", {"OPCUA_MDNS": wert}):
                self.assertFalse(mdns.enabled(), wert)


class AnnounceTest(unittest.IsolatedAsyncioTestCase):
    async def test_abgeschaltet_kuendigt_nicht_an(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
            mdns, "detect_lan_ipv4"
        ) as detect:
            async with mdns.announce("x", 4840, "/raspi/server/") as ip:
                self.assertIsNone(ip)
            detect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
