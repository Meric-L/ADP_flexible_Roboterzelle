"""Tests fuer die Anmeldung beim Local Discovery Server (`discovery.lds`).

Die Anmeldung selbst macht `asyncua.Server.register_to_discovery()`. Getestet
wird deshalb die Klammer darum: dass die angekuendigte Adresse stimmt, dass ein
nicht erreichbarer LDS den Server nicht mitreisst und dass beim Verlassen
abgemeldet wird.
"""

import unittest
from unittest import mock
from urllib.parse import urlparse

from vision_server.discovery import lds


class FakeServer:
    """Minimaler Ersatz fuer `asyncua.Server` mit dem, was `register()` nutzt."""

    def __init__(self, endpoint: str, fail_register: bool = False) -> None:
        self.endpoint = urlparse(endpoint)
        self.fail_register = fail_register
        self.registriert: list[tuple[str, int]] = []
        self.abgemeldet: list[str] = []

    async def register_to_discovery(self, url: str, period: int = 60) -> None:
        if self.fail_register:
            raise ConnectionError("LDS nicht erreichbar")
        self.registriert.append((url, period))

    async def unregister_from_discovery(self, url: str) -> None:
        self.abgemeldet.append(url)


class LdsUrlTest(unittest.TestCase):
    def test_default_meldet_nicht_an(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(lds.lds_url(), "")

    def test_env_schaltet_den_lds_der_zelle_ein(self):
        with mock.patch.dict("os.environ", {"OPCUA_LDS_URL": lds.CELL_LDS_URL}):
            self.assertEqual(lds.lds_url(), "opc.tcp://10.10.38.27:4840/")

    def test_env_ueberschreibt(self):
        with mock.patch.dict("os.environ", {"OPCUA_LDS_URL": "opc.tcp://host:4840/"}):
            self.assertEqual(lds.lds_url(), "opc.tcp://host:4840/")

    def test_leerer_wert_schaltet_ab(self):
        with mock.patch.dict("os.environ", {"OPCUA_LDS_URL": "  "}):
            self.assertEqual(lds.lds_url(), "")


class AdvertisedEndpointTest(unittest.TestCase):
    def test_traegt_die_lan_ip_nicht_0_0_0_0(self):
        """Der Kern: aus diesem Endpoint wird die DiscoveryUrl, die der
        Aggregation-Server spaeter anwaehlt."""
        with mock.patch.object(
            lds.mdns, "detect_lan_ipv4", return_value="10.10.38.104"
        ):
            url = lds.advertised_endpoint(4840, "/raspi/server/")
        self.assertEqual(url, "opc.tcp://10.10.38.104:4840/raspi/server/")
        self.assertNotIn("0.0.0.0", url)

    def test_adresse_kann_vorgegeben_werden(self):
        url = lds.advertised_endpoint(4840, "/raspi/server/", address="10.0.0.9")
        self.assertEqual(url, "opc.tcp://10.0.0.9:4840/raspi/server/")

    def test_ohne_lan_ip_keine_url(self):
        with mock.patch.object(lds.mdns, "detect_lan_ipv4", return_value=None):
            self.assertIsNone(lds.advertised_endpoint(4840, "/raspi/server/"))


class RegisterTest(unittest.IsolatedAsyncioTestCase):
    async def test_meldet_an_und_wieder_ab(self):
        server = FakeServer("opc.tcp://10.10.38.104:4840/raspi/server/")
        async with lds.register(
            server, url="opc.tcp://lds:4840/", renew_seconds=60
        ) as url:
            self.assertEqual(url, "opc.tcp://10.10.38.104:4840/raspi/server/")
            self.assertEqual(server.registriert, [("opc.tcp://lds:4840/", 60)])
            self.assertEqual(server.abgemeldet, [])
        self.assertEqual(server.abgemeldet, ["opc.tcp://lds:4840/"])

    async def test_verweigert_anmeldung_mit_0_0_0_0(self):
        """Ohne diese Bremse traegt `register_to_discovery()` `0.0.0.0` ein,
        und der Aggregation-Server verbindet ins Leere."""
        server = FakeServer("opc.tcp://0.0.0.0:4840/raspi/server/")
        async with lds.register(server, url="opc.tcp://lds:4840/") as url:
            self.assertIsNone(url)
        self.assertEqual(server.registriert, [])
        self.assertEqual(server.abgemeldet, [])

    async def test_ohne_lds_keine_anmeldung(self):
        server = FakeServer("opc.tcp://10.0.0.9:4840/raspi/server/")
        async with lds.register(server, url="") as url:
            self.assertIsNone(url)
        self.assertEqual(server.registriert, [])

    async def test_fehlgeschlagene_anmeldung_beendet_den_server_nicht(self):
        """Ein Server, den man per URL erreicht, ist mehr wert als gar keiner."""
        server = FakeServer(
            "opc.tcp://10.0.0.9:4840/raspi/server/", fail_register=True
        )
        async with lds.register(server, url="opc.tcp://lds:4840/") as url:
            self.assertIsNone(url)
        # Nie angemeldet -> auch nicht abmelden, sonst KeyError in asyncua.
        self.assertEqual(server.abgemeldet, [])

    async def test_fehler_beim_abmelden_schlaegt_nicht_durch(self):
        """Beim Herunterfahren darf ein toter LDS kein Hindernis sein."""
        server = FakeServer("opc.tcp://10.0.0.9:4840/raspi/server/")

        async def kaputt(url):
            raise ConnectionError("LDS weg")

        server.unregister_from_discovery = kaputt
        async with lds.register(server, url="opc.tcp://lds:4840/") as url:
            self.assertIsNotNone(url)

    async def test_erneuerungsabstand_wird_durchgereicht(self):
        server = FakeServer("opc.tcp://10.0.0.9:4840/raspi/server/")
        async with lds.register(
            server, url="opc.tcp://lds:4840/", renew_seconds=30
        ):
            pass
        self.assertEqual(server.registriert[0][1], 30)


if __name__ == "__main__":
    unittest.main()
