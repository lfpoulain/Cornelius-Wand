"""Tests du décodage BTHome, sur des annonces construites comme le firmware."""

import pytest

from custom_components.cornelius_wand.parser import BTHomeParseError, NO_AIM_PITCH, parse_bthome

from .helpers import wand_advert


def test_spell_with_aim():
    packet = parse_bthome(wand_advert(7, 2, pitch=10.4, aim_age_ms=850))
    assert packet.packet_id == 7
    assert packet.voltage == pytest.approx(2.95)
    assert packet.pressed_button == 2
    assert packet.preparing_button is None
    assert packet.pitch == pytest.approx(10.4)
    assert packet.aim_age_ms == 850
    assert packet.has_aim


def test_preparation_is_not_a_spell():
    packet = parse_bthome(wand_advert(8, 1, pitch=-45.5, preparing=True))
    assert packet.preparing_button == 1
    assert packet.pressed_button is None
    assert packet.pitch == pytest.approx(-45.5)


def test_spell_without_aim():
    packet = parse_bthome(wand_advert(9, 1, pitch=NO_AIM_PITCH, aim_age_ms=0))
    assert packet.pressed_button == 1
    assert not packet.has_aim


def test_presence_announcement_has_no_button():
    packet = parse_bthome(wand_advert(9, 0, pitch=NO_AIM_PITCH, aim_age_ms=0))
    assert packet.pressed_button is None and packet.preparing_button is None
    assert not packet.is_aim


def test_aim_announcement_has_an_aim_but_no_button():
    packet = parse_bthome(wand_advert(10, 0, pitch=12.5, aim_age_ms=40))
    assert packet.is_aim
    assert packet.aim_age_ms == 40
    assert not parse_bthome(wand_advert(11, 1, pitch=12.5)).is_aim  # un sort n'est pas une visée


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "vide"),
        (bytes([0x45, 0x00, 0x01]), "chiffrée"),
        (bytes([0x24, 0x00, 0x01]), "version"),
        (bytes([0x44, 0x0C, 0x01]), "tronqué"),
    ],
)
def test_invalid_announcements(data, message):
    with pytest.raises(BTHomeParseError, match=message):
        parse_bthome(data)
