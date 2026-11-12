"""Configuration des tests de l'intégration (pytest-homeassistant-custom-component)."""

from datetime import timedelta
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.components import bluetooth
from homeassistant.util import dt as dt_util

if sys.platform == "win32":
    # Sous Windows, la boucle asyncio a besoin d'une paire de sockets : on ne les bloque pas
    import pytest_socket

    pytest_socket.disable_socket = lambda *args, **kwargs: None
    pytest_socket.socket_allow_hosts = lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Autorise le chargement de custom_components/cornelius_wand."""
    yield


@pytest.fixture(autouse=True)
def no_bluetooth_scanner():
    """Pas de vrai Bluetooth : les annonces sont injectées directement dans la baguette.

    Sans balise récente, la baguette passe pour endormie, et aucun adaptateur ne s'y connecte.
    """
    with (
        patch("homeassistant.components.bluetooth.async_register_callback", return_value=lambda: None),
        patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None),
        patch("homeassistant.components.bluetooth.async_ble_device_from_address", return_value=None),
    ):
        yield


@pytest.fixture
def awake():
    """La baguette est réveillée : sa dernière balise vient d'arriver."""
    with patch(
        "homeassistant.components.bluetooth.async_last_service_info",
        side_effect=lambda *args, **kwargs: SimpleNamespace(time=bluetooth.MONOTONIC_TIME()),
    ):
        yield


@pytest.fixture
def clock():
    """Avance l'horloge de la baguette et déclenche les minuteries de Home Assistant échues.

    freezegun figerait la boucle asyncio : seule l'horloge lue par wand.py est décalée.
    """
    offset = 0.0
    with patch(
        "custom_components.cornelius_wand.wand.time",
        SimpleNamespace(monotonic=lambda: time.monotonic() + offset),
    ):

        def advance(hass, seconds: float) -> None:
            nonlocal offset
            offset += seconds
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=offset))

        yield advance


@pytest.fixture
def real_panel() -> bool:
    """Les tests HTTP du panneau peuvent garder les dépendances réelles de Home Assistant."""
    return False


@pytest.fixture(autouse=True)
def no_dependencies(real_panel: bool):
    """Pas d'adaptateur Bluetooth ni de serveur web dans les tests : on saute les dépendances."""
    if real_panel:
        yield
        return
    with (
        patch("homeassistant.loader.Integration.dependencies", new_callable=lambda: property(lambda self: [])),
        patch("custom_components.cornelius_wand.async_setup_panel"),
    ):
        yield
