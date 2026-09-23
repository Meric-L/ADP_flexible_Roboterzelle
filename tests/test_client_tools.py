"""Tests fuer die gemeinsamen Teile der Kommandozeilen-Clients (`tools/_client.py`)."""

import argparse
import unittest

from vision_server.tools._client import add_connection_arguments, format_progress


class ConnectionArgumentsTest(unittest.TestCase):
    def test_vorgaben_bleiben_wie_sie_waren(self):
        parser = argparse.ArgumentParser()
        add_connection_arguments(parser)
        args = parser.parse_args([])
        self.assertEqual(args.url, "opc.tcp://127.0.0.1:4840/raspi/server/")
        self.assertEqual(args.namespace, "http://launch-rm.de/vision")
        self.assertEqual(args.vision_system, "VisionMachine")


class FormatProgressTest(unittest.TestCase):
    def test_volle_angaben(self):
        progress = {"samples": 12, "minSamples": 15, "coverageX": 0.84, "coverageY": 0.9}
        self.assertEqual(format_progress(progress), "12/15, Abdeckung x 84% y 90%")
        self.assertEqual(
            format_progress(progress, separator="   "), "12/15   Abdeckung x 84% y 90%"
        )

    def test_ruhezustand(self):
        self.assertEqual(format_progress({"running": False}), "0/?, Abdeckung x 0% y 0%")


if __name__ == "__main__":
    unittest.main()
