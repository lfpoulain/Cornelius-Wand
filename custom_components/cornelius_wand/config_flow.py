"""Ajout de la baguette (découverte Bluetooth) et réglage des noms des sorts."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.util import slugify

from .const import (
    CONF_CAMERA_URL,
    CONF_SPELLS,
    CONF_TYPESAFE_API_KEY,
    DEFAULT_SPELLS,
    DOMAIN,
    LOCAL_NAME_PREFIX,
    MAX_SPELLS,
    UNKNOWN_SPELL,
)
from .wand import parse_spell_names

TITLE = "Baguette Cornelius"

# Champs texte nettoyés à l'enregistrement des options
TEXT_OPTIONS = (CONF_CAMERA_URL, CONF_TYPESAFE_API_KEY)


def _is_wand(info: BluetoothServiceInfoBleak) -> bool:
    return (info.name or "").startswith(LOCAL_NAME_PREFIX)


def _ambiguous(names: list[str]) -> bool:
    """Deux sorts au même type d'événement, ou un nom qui se confond avec « inconnu »."""
    types = [slugify(name) for name in names]
    return len(set(types)) < len(types) or UNKNOWN_SPELL in types or "unknown" in types


class CorneliusWandConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._candidates: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CorneliusOptionsFlow:
        return CorneliusOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            return self.async_create_entry(
                title=TITLE, data={CONF_ADDRESS: self._discovery.address}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery.name,
                "address": self._discovery.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=TITLE, data={CONF_ADDRESS: address})

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=False):
            if info.address not in configured and _is_wand(info):
                self._candidates[info.address] = f"{info.name} ({info.address})"
        if not self._candidates:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._candidates)}),
        )


class CorneliusOptionsFlow(OptionsFlow):
    """Sorts, caméra au plafond et vision (Jev, de TypeSafe)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            names = parse_spell_names(user_input[CONF_SPELLS])
            if not names:
                errors[CONF_SPELLS] = "no_spells"
            elif len(names) > MAX_SPELLS:
                errors[CONF_SPELLS] = "too_many_spells"
            elif _ambiguous(names):
                errors[CONF_SPELLS] = "duplicate_spells"
            else:
                text = {key: str(user_input.get(key, "")).strip() for key in TEXT_OPTIONS}
                return self.async_create_entry(data={**text, CONF_SPELLS: ", ".join(names)})

        options = self.config_entry.options
        # Champs facultatifs pré-remplis sans valeur par défaut : vidés, ils restent vides
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SPELLS, default=options.get(CONF_SPELLS, DEFAULT_SPELLS)
                ): str,
                vol.Optional(CONF_CAMERA_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Optional(CONF_TYPESAFE_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            # Après une erreur, on repropose la saisie telle quelle : un champ vidé reste vide
            data_schema=self.add_suggested_values_to_schema(schema, user_input if user_input is not None else options),
            errors=errors,
            # Pas d'URL dans les traductions (hassfest) : l'exemple passe en paramètre
            description_placeholders={"camera_example": "http://cornelius-camera.local"},
        )
