"""Entrées MIDI du bridge : claviers maîtres et téléphones branchés en USB.

Le bridge écoute toutes les entrées MIDI (sauf son propre port). Une entrée qui envoie le SysEx de
contrôle StageKontrol est reconnue comme un téléphone (câble USB) : son MIDI est retransmis sur la
sortie « stageKontrol » et ses messages de contrôle sont traités comme en Wi-Fi. Les autres entrées
sont des claviers (toutes, ou seulement celle choisie avec --keyboard). La liste des entrées est
relue régulièrement : un câble branché en cours de route est pris en compte.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

import mido

from .usb import decode_control, encode_control

OnMessage = Callable[[mido.Message], None]


class PhoneLink:
    """Téléphone branché en USB : son entrée (côté bridge) et, si elle existe, sa sortie de retour."""

    def __init__(self, name: str, output: mido.ports.BaseOutput | None) -> None:
        self.name = name
        self._output = output
        self._lock = threading.Lock()

    def send_control(self, value: Any) -> None:
        if self._output is None:
            return
        with self._lock:
            try:
                self._output.send(encode_control(value))
            except Exception:  # port disparu entre-temps
                pass

    def close(self) -> None:
        if self._output is not None:
            self._output.close()


class InputHub:
    def __init__(
        self,
        own_port: str,
        keyboard: str | None,
        on_keyboard: OnMessage | None,
        on_phone_midi: Callable[[PhoneLink, mido.Message], None],
        on_phone_control: Callable[[PhoneLink, Any], None],
        log: Callable[[str], None],
        list_inputs: Callable[[], list[str]] = mido.get_input_names,
        list_outputs: Callable[[], list[str]] = mido.get_output_names,
        open_input: Callable[..., Any] = mido.open_input,
        open_output: Callable[..., Any] = mido.open_output,
    ) -> None:
        self.own_port = own_port
        self.keyboard = keyboard
        self.on_keyboard = on_keyboard
        self.on_phone_midi = on_phone_midi
        self.on_phone_control = on_phone_control
        self.log = log
        self._list_inputs = list_inputs
        self._list_outputs = list_outputs
        self._open_input = open_input
        self._open_output = open_output
        self._inputs: dict[str, Any] = {}
        self.phones: dict[str, PhoneLink] = {}
        self._lock = threading.Lock()

    def is_keyboard(self, name: str) -> bool:
        if name in self.phones or self.on_keyboard is None:
            return False
        return self.keyboard is None or name == self.keyboard or name.startswith(self.keyboard)

    def keyboards(self) -> list[str]:
        """Entrées MIDI traitées comme claviers maîtres (pour la page d'état)."""
        with self._lock:
            return [n for n in self._inputs if self.is_keyboard(n)]

    def refresh(self) -> None:
        """Ouvre les nouvelles entrées MIDI et oublie celles qui ont disparu."""
        names = [n for n in self._list_inputs() if not n.startswith(self.own_port)]
        with self._lock:
            for name in list(self._inputs):
                if name not in names:
                    self._inputs.pop(name).close()
                    phone = self.phones.pop(name, None)
                    if phone:
                        phone.close()
                        self.log(f"- téléphone USB débranché ({name})")
            for name in names:
                if name not in self._inputs:
                    try:
                        self._inputs[name] = self._open_input(name, callback=self._callback(name))
                    except Exception as e:
                        self.log(f"? entrée MIDI « {name} » inaccessible : {e}")

    def _callback(self, name: str) -> OnMessage:
        def handle(msg: mido.Message) -> None:
            control = decode_control(msg)
            if control is not None:
                self.on_phone_control(self._phone(name), control)
            elif name in self.phones:
                if msg.type != "sysex":
                    self.on_phone_midi(self.phones[name], msg)
            elif self.on_keyboard is not None and self.is_keyboard(name):
                self.on_keyboard(msg)

        return handle

    def _phone(self, name: str) -> PhoneLink:
        """Téléphone reconnu sur cette entrée : on ouvre la sortie du même nom pour lui répondre."""
        with self._lock:
            phone = self.phones.get(name)
            if phone:
                return phone
            outputs = self._list_outputs()
            match = next((o for o in outputs if o == name), None) or next(
                (o for o in outputs if o.startswith(name) or name.startswith(o)), None
            )
            output = None
            if match:
                try:
                    output = self._open_output(match)
                except Exception:
                    output = None
            phone = PhoneLink(name, output)
            self.phones[name] = phone
        self.log(f"+ téléphone connecté en USB ({name})")
        return phone

    def close(self) -> None:
        with self._lock:
            for port in self._inputs.values():
                port.close()
            for phone in self.phones.values():
                phone.close()
            self._inputs.clear()
            self.phones.clear()
