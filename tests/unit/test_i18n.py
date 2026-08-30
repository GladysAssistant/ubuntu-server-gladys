from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[2] / "payload" / "usr" / "lib" / "gladys-installer"
sys.path.insert(0, str(RUNTIME))

import i18n  # noqa: E402


class InstallerLocalizationTests(unittest.TestCase):
    def test_installed_locale_selects_the_matching_subiquity_language(self) -> None:
        cases = {
            "en_US.UTF-8": "en",
            "en_GB.UTF-8": "en",
            "fr_FR.UTF-8": "fr",
            "de_DE.UTF-8": "de",
            "zh_CN.UTF-8": "zh_CN",
            "zh_TW.UTF-8": "zh_TW",
            "nb": "nb",
            "sr_RS": "sr",
        }
        with tempfile.TemporaryDirectory() as directory:
            locale_path = Path(directory) / "locale"
            for locale_name, expected in cases.items():
                with self.subTest(locale_name=locale_name):
                    locale_path.write_text(
                        f'LANG="{locale_name}"\nLANGUAGE="ignored"\n', encoding="utf-8"
                    )
                    self.assertEqual(i18n.read_installed_language(locale_path), expected)

    def test_missing_or_untrusted_locale_falls_back_to_english(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            locale_path = Path(directory) / "locale"
            self.assertEqual(i18n.read_installed_language(locale_path), "en")

            for value in ("", "LANG=../../fr\n", "LANG=$(touch /tmp/no)\n", "LC_ALL=fr_FR.UTF-8\n"):
                with self.subTest(value=value):
                    locale_path.write_text(value, encoding="utf-8")
                    self.assertEqual(i18n.read_installed_language(locale_path), "en")

    def test_every_subiquity_language_has_complete_interface_text(self) -> None:
        expected_languages = {
            "ar",
            "ast",
            "be",
            "bo",
            "ca",
            "cs",
            "de",
            "el",
            "en",
            "es",
            "fi",
            "fr",
            "gl",
            "he",
            "hr",
            "hu",
            "id",
            "ja",
            "kab",
            "lt",
            "lv",
            "nb",
            "nl",
            "oc",
            "pl",
            "pt",
            "ru",
            "sr",
            "sv",
            "uk",
            "zh_CN",
            "zh_TW",
        }
        self.assertEqual(set(i18n.supported_languages()), expected_languages)

        english_keys = set(i18n.catalog("en")["ui"])
        english_phases = set(i18n.catalog("en")["phases"])
        for language in expected_languages:
            with self.subTest(language=language):
                translated = i18n.catalog(language)
                self.assertEqual(set(translated["ui"]), english_keys)
                self.assertEqual(set(translated["phases"]), english_phases)
                self.assertTrue(all(str(value).strip() for value in translated["ui"].values()))
                self.assertTrue(
                    all(str(value).strip() for value in translated["phases"].values())
                )

    def test_visible_text_is_translated_and_unknown_languages_fall_back(self) -> None:
        self.assertEqual(
            i18n.text("fr", "preparing_title"),
            "Préparation de votre maison connectée",
        )
        self.assertEqual(i18n.text("de", "step_download"), "Gladys Assistant herunterladen")
        self.assertEqual(i18n.text("unknown", "ready_title"), "Gladys Assistant is ready")

    def test_non_english_catalogues_are_translated_without_filler_copy(self) -> None:
        english = i18n.catalog("en")
        for language in set(i18n.supported_languages()) - {"en"}:
            with self.subTest(language=language):
                translated = i18n.catalog(language)
                translated_ui_count = sum(
                    translated["ui"][key] != english_value
                    for key, english_value in english["ui"].items()
                )
                translated_phase_count = sum(
                    translated["phases"][phase] != english_value
                    for phase, english_value in english["phases"].items()
                )
                self.assertGreaterEqual(translated_ui_count, 20)
                self.assertGreaterEqual(translated_phase_count, 10)
                self.assertGreaterEqual(len(set(translated["ui"].values())), 24)
                self.assertGreaterEqual(len(set(translated["phases"].values())), 11)

    def test_catalogues_never_advertise_the_retired_local_name(self) -> None:
        for language in i18n.supported_languages():
            with self.subTest(language=language):
                visible_text = " ".join(i18n.catalog(language)["ui"].values())
                self.assertNotIn("gladys.local", visible_text)

    def test_right_to_left_direction_is_explicit(self) -> None:
        self.assertEqual(i18n.direction("ar"), "rtl")
        self.assertEqual(i18n.direction("he"), "rtl")
        self.assertEqual(i18n.direction("fr"), "ltr")

    def test_linux_console_falls_back_for_scripts_it_cannot_render(self) -> None:
        for language in ("ar", "he", "bo", "ja", "zh_CN", "zh_TW"):
            with self.subTest(language=language):
                self.assertEqual(i18n.console_language(language), "en")

        for language in ("fr", "el", "ru", "kab"):
            with self.subTest(language=language):
                self.assertEqual(i18n.console_language(language), language)


if __name__ == "__main__":
    unittest.main()
