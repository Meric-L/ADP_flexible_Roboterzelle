"""Tests fuer den Schalter der mDNS-Ankuendigung (`discovery.mdns`)."""

import unittest
from unittest import mock

from vision_server.discovery import mdns


class EnabledTest(unittest.TestCase):
    def test_default_ist_an(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(mdns.enabled())

    def test_env_schaltet_ab(self):
        for wert in ("0", "false", "No", " off "):
            with mock.patch.dict("os.environ", {"OPCUA_MDNS": wert}):
                self.assertFalse(mdns.enabled(), wert)

    def test_andere_werte_bleiben_an(self):
        for wert in ("", "1", "true", "on"):
            with mock.patch.dict("os.environ", {"OPCUA_MDNS": wert}):
                self.assertTrue(mdns.enabled(), wert)


class AnnounceTest(unittest.IsolatedAsyncioTestCase):
    async def test_abgeschaltet_kuendigt_nicht_an(self):
        with mock.patch.dict("os.environ", {"OPCUA_MDNS": "0"}), mock.patch.object(
            mdns, "detect_lan_ipv4"
        ) as detect:
            async with mdns.announce("x", 4840, "/raspi/server/") as ip:
                self.assertIsNone(ip)
            detect.assert_not_called()

    async def test_ohne_lan_adresse_laeuft_es_ohne_ankuendigung_weiter(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
            mdns, "detect_lan_ipv4", return_value=None
        ):
            async with mdns.announce("x", 4840, "/raspi/server/") as ip:
                self.assertIsNone(ip)


if __name__ == "__main__":
    unittest.main()
