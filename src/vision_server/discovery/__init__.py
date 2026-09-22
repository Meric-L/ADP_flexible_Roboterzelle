"""Auffindbarkeit des Servers im Netz: mDNS und Local Discovery Server.

Zwei Wege nebeneinander, weil sie verschiedene Leser bedienen:

* `mdns` kuendigt den Dienst im Subnetz an. Clients wie das WSC-Frontend
  finden uns darueber ohne Umweg.
* `lds` meldet den Server beim Local Discovery Server der Zelle an. **Nur**
  darueber nimmt der Aggregation-Server das Modul auf -- eine mDNS-Ankuendigung
  allein genuegt ihm nicht. Am 21.09.2026 im Labor gemessen, Begruendung im
  Modul.

Beide sind bewusst fehlertolerant: scheitert eine der beiden, laeuft der Server
weiter und loggt eine Warnung. Ein Server, den man per URL erreicht, ist mehr
wert als gar keiner.
"""

from . import lds, mdns

__all__ = ["lds", "mdns"]
