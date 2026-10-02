"""Registro dei worker QThread di un oggetto (finestra o motore di chat).

Tiene l'elenco dei worker attivi, per fermarli tutti alla chiusura, e di
quelli «in pensione»: annullati ma ancora bloccati su un socket, che la UI
non attende più.
"""
from __future__ import annotations


class WorkerRegistry:
    def __init__(self) -> None:
        self._running: list = []
        self._zombies: list = []   # worker in arresto, non bloccano la UI

    def track(self, w) -> None:
        """Tiene registro di un worker attivo per chiuderlo alla chiusura."""
        self._running.append(w)
        w.finished.connect(lambda: self._untrack(w))

    @staticmethod
    def clear_ref(owner, attr: str, w):
        """Slot per `finished`: azzera `owner.attr` solo se punta ancora a `w`.

        Un worker annullato può terminare DOPO che ne è partito uno nuovo:
        un azzeramento incondizionato scollegherebbe quello nuovo.
        """
        def clear() -> None:
            if getattr(owner, attr, None) is w:
                setattr(owner, attr, None)
        return clear

    def _untrack(self, w) -> None:
        try:
            self._running.remove(w)
        except ValueError:
            pass

    def retire(self, w) -> None:
        """Sgancia un worker in arresto: la UI non attende più la sua morte.

        Il thread può restare bloccato su un socket finché scatta il timeout
        di rete: gli signal emessi in ritardo vengono scartati e la UI è
        subito libera di fare nuove richieste.
        """
        if w is None:
            return
        self._untrack(w)
        self._zombies.append(w)
        w.finished.connect(lambda: self._forget_zombie(w))

    def _forget_zombie(self, w) -> None:
        try:
            self._zombies.remove(w)
        except ValueError:
            pass

    def all(self) -> list:
        """Worker attivi e in arresto, da passare a `shutdown_workers`."""
        return list(self._running) + list(self._zombies)
