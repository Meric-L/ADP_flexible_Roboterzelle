"""Tests fuer die Anmeldung beim Local Discovery Server (`ua_lds`).

Ohne laufenden Discovery-Server: `_send` wird ersetzt und mitgeschrieben.
Geprueft wird das, was im Labor schiefgehen kann -- vor allem die
DiscoveryUrl, denn `0.0.0.0` darf dort nie landen.
"""

import asyncio
import unittest
from unittest import mock

from asyncua import ua

import ua_lds


class Recorder:
    """Ersetzt `ua_lds._send` und merkt sich jede Anfrage."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, ua.RegisteredServer]] = []
        self.fail = fail

    async def __call__(self, url, registered):
        if self.fail:
            raise ConnectionError("LDS nicht erreichbar")
        self.calls.append((url, registered))

    @property
    def online_flags(self) -> list[bool]:
        return [call[1].IsOnline for call in self.calls]

    @property
    def discovery_urls(self) -> list[str]:
        return [call[1].DiscoveryUrls[0] for call in self.calls]


class FakeUaClient:
    def __init__(self) -> None:
        self.aufrufe: list[str] = []

    async def register_server(self, registered):
        self.aufrufe.append("register_server")

    async def register_server2(self, params):
        self.aufrufe.append("register_server2")


class FakeClient:
    """Minimaler Ersatz fuer `asyncua.Client` in `_send`."""

    letzte: "FakeClient | None" = None

    def __init__(self, url: str, timeout: int = 10) -> None:
        self.url = url
        self.uaclient = FakeUaClient()
        self.verbunden = False
        FakeClient.letzte = self

    async def connect_sessionless(self) -> None:
        self.verbunden = True

    async def disconnect_sessionless(self) -> None:
        self.verbunden = False


class LdsUrlTest(unittest.TestCase):
    def test_default_ist_der_lds_der_zelle(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(ua_lds.lds_url(), ua_lds.DEFAULT_LDS_URL)

    def test_env_ueberschreibt(self):
        with mock.patch.dict("os.environ", {"OPCUA_LDS_URL": "opc.tcp://host:4840/"}):
            self.assertEqual(ua_lds.lds_url(), "opc.tcp://host:4840/")

    def test_leerer_wert_schaltet_ab(self):
        with mock.patch.dict("os.environ", {"OPCUA_LDS_URL": "  "}):
            self.assertEqual(ua_lds.lds_url(), "")


class RegisteredServerTest(unittest.TestCase):
    def test_felder_entsprechen_den_uebrigen_modulen(self):
        """Conveyor und Co. stehen als ClientAndServer mit freeopcua-ProductUri
        im LDS; weicht unser Datensatz davon ab, faellt das erst im Labor auf."""
        served = ua_lds._registered_server(
            "urn:plcm:camera-server:ceiling-01",
            "Raspberry Pi OPC UA Server",
            "opc.tcp://10.10.38.104:4840/raspi/server/",
            True,
        )
        self.assertEqual(served.ServerUri, "urn:plcm:camera-server:ceiling-01")
        self.assertEqual(served.ProductUri, ua_lds.PRODUCT_URI)
        self.assertEqual(served.ServerType, ua.ApplicationType.ClientAndServer)
        self.assertEqual(
            served.DiscoveryUrls, ["opc.tcp://10.10.38.104:4840/raspi/server/"]
        )
        self.assertEqual(served.ServerNames[0].Text, "Raspberry Pi OPC UA Server")
        self.assertTrue(served.IsOnline)

    def test_abmeldung_setzt_is_online_false(self):
        served = ua_lds._registered_server("urn:x", "name", "opc.tcp://ip:4840/", False)
        self.assertFalse(served.IsOnline)


class SendTest(unittest.IsolatedAsyncioTestCase):
    async def test_meldet_ohne_mdns_konfiguration_an(self):
        """Mit `MdnsDiscoveryConfiguration` baut der open62541-LDS einen eigenen
        mDNS-Datensatz und haengt `.local` an die IP der DiscoveryUrl --
        `opc.tcp://10.10.38.104.local:4840/...` loest nicht auf. Deshalb das
        schlichte `RegisterServer`, wie es auch die Nachbarmodule benutzen."""
        eintrag = ua_lds._registered_server(
            "urn:x", "name", "opc.tcp://10.0.0.9:4840/raspi/server/", True
        )
        with mock.patch.object(ua_lds, "Client", FakeClient):
            await ua_lds._send("opc.tcp://lds:4840/", eintrag)

        self.assertEqual(FakeClient.letzte.uaclient.aufrufe, ["register_server"])
        self.assertNotIn("register_server2", FakeClient.letzte.uaclient.aufrufe)

    async def test_kanal_wird_wieder_geschlossen(self):
        eintrag = ua_lds._registered_server("urn:x", "name", "opc.tcp://ip:4840/", True)
        with mock.patch.object(ua_lds, "Client", FakeClient):
            await ua_lds._send("opc.tcp://lds:4840/", eintrag)
        self.assertFalse(FakeClient.letzte.verbunden)


class RegisterTest(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_url_traegt_die_lan_ip_nicht_0_0_0_0(self):
        """Der Kern des Moduls: `asyncua` wuerde hier den Endpoint eintragen,
        also `0.0.0.0` -- der Aggregation-Server verbaende damit ins Leere."""
        recorder = Recorder()
        with (
            mock.patch.object(ua_lds, "_send", recorder),
            mock.patch.object(
                ua_lds.ua_mdns, "detect_lan_ipv4", return_value="10.10.38.104"
            ),
        ):
            async with ua_lds.register(
                application_uri="urn:plcm:camera-server:ceiling-01",
                server_name="Raspberry Pi OPC UA Server",
                port=4840,
                path="/raspi/server/",
                url="opc.tcp://lds:4840/",
                renew_seconds=0,
            ) as url:
                self.assertEqual(url, "opc.tcp://10.10.38.104:4840/raspi/server/")
                self.assertNotIn("0.0.0.0", url)

        self.assertEqual(
            recorder.discovery_urls,
            [
                "opc.tcp://10.10.38.104:4840/raspi/server/",
                "opc.tcp://10.10.38.104:4840/raspi/server/",
            ],
        )

    async def test_adresse_kann_vorgegeben_werden(self):
        recorder = Recorder()
        with mock.patch.object(ua_lds, "_send", recorder):
            async with ua_lds.register(
                application_uri="urn:x",
                server_name="name",
                port=4840,
                path="/raspi/server/",
                address="10.0.0.9",
                url="opc.tcp://lds:4840/",
                renew_seconds=0,
            ) as url:
                self.assertEqual(url, "opc.tcp://10.0.0.9:4840/raspi/server/")

    async def test_meldet_beim_verlassen_ab(self):
        recorder = Recorder()
        with mock.patch.object(ua_lds, "_send", recorder):
            async with ua_lds.register(
                application_uri="urn:x",
                server_name="name",
                port=4840,
                path="/raspi/server/",
                address="10.0.0.9",
                url="opc.tcp://lds:4840/",
                renew_seconds=0,
            ):
                self.assertEqual(recorder.online_flags, [True])
        self.assertEqual(recorder.online_flags, [True, False])

    async def test_ohne_lds_keine_anmeldung(self):
        recorder = Recorder()
        with mock.patch.object(ua_lds, "_send", recorder):
            async with ua_lds.register(
                application_uri="urn:x",
                server_name="name",
                port=4840,
                path="/raspi/server/",
                address="10.0.0.9",
                url="",
            ) as url:
                self.assertIsNone(url)
        self.assertEqual(recorder.calls, [])

    async def test_ohne_lan_ip_keine_anmeldung(self):
        recorder = Recorder()
        with (
            mock.patch.object(ua_lds, "_send", recorder),
            mock.patch.object(ua_lds.ua_mdns, "detect_lan_ipv4", return_value=None),
        ):
            async with ua_lds.register(
                application_uri="urn:x",
                server_name="name",
                port=4840,
                path="/raspi/server/",
                url="opc.tcp://lds:4840/",
            ) as url:
                self.assertIsNone(url)
        self.assertEqual(recorder.calls, [])

    async def test_fehlgeschlagene_anmeldung_beendet_den_server_nicht(self):
        """Ein Server, den man per URL erreicht, ist mehr wert als gar keiner."""
        with mock.patch.object(ua_lds, "_send", Recorder(fail=True)):
            async with ua_lds.register(
                application_uri="urn:x",
                server_name="name",
                port=4840,
                path="/raspi/server/",
                address="10.0.0.9",
                url="opc.tcp://lds:4840/",
            ) as url:
                self.assertIsNone(url)

    async def test_erneuerung_wird_beim_verlassen_beendet(self):
        """Bleibt die Schleife stehen, haengt der Prozess beim Herunterfahren."""
        recorder = Recorder()
        with mock.patch.object(ua_lds, "_send", recorder):
            async with ua_lds.register(
                application_uri="urn:x",
                server_name="name",
                port=4840,
                path="/raspi/server/",
                address="10.0.0.9",
                url="opc.tcp://lds:4840/",
                renew_seconds=3600,
            ):
                laufende = len([t for t in asyncio.all_tasks() if not t.done()])
                self.assertGreater(laufende, 1)
        rest = [
            t
            for t in asyncio.all_tasks()
            if not t.done() and t is not asyncio.current_task()
        ]
        self.assertEqual(rest, [])

    async def test_erneuerung_wiederholt_die_anmeldung(self):
        recorder = Recorder()
        with mock.patch.object(ua_lds, "_send", recorder):
            async with ua_lds.register(
                application_uri="urn:x",
                server_name="name",
                port=4840,
                path="/raspi/server/",
                address="10.0.0.9",
                url="opc.tcp://lds:4840/",
                renew_seconds=0.01,
            ):
                await asyncio.sleep(0.05)
                self.assertGreaterEqual(len(recorder.calls), 2)
                self.assertTrue(all(recorder.online_flags))


if __name__ == "__main__":
    unittest.main()
