from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from pubg_ai.code_translator import CodeTranslator, translate_code


class CodeTranslatorTests(unittest.TestCase):
    def test_known_item_code_returns_korean_label(self) -> None:
        translator = CodeTranslator()

        translated = translator.translate("Item_Weapon_BerylM762_C", "item")

        self.assertTrue(translated.known)
        self.assertEqual(translated.label, "베릴 M762")

    def test_known_damage_causer_code_returns_korean_label(self) -> None:
        translator = CodeTranslator()

        translated = translator.translate("WeapBerylM762_C", "damage_causer")

        self.assertTrue(translated.known)
        self.assertEqual(translated.label, "베릴 M762")

    def test_unknown_code_falls_back_to_original_code(self) -> None:
        translator = CodeTranslator()

        translated = translator.translate("Item_Weapon_NewThing_C", "item")

        self.assertFalse(translated.known)
        self.assertEqual(translated.label, "Item_Weapon_NewThing_C")

    def test_auto_translation_picks_category_from_code_shape(self) -> None:
        translator = CodeTranslator()

        self.assertEqual(translator.translate_auto("Erangel_Main").label, "에란겔")
        self.assertEqual(translator.translate_auto("WeapAK47_C").label, "AKM")
        self.assertEqual(translate_code("squad-fpp"), "1인칭 스쿼드")

    def test_item_object_translates_attached_items(self) -> None:
        translator = CodeTranslator()

        translated = translator.translate_item_object(
            {
                "itemId": "Item_Weapon_BerylM762_C",
                "attachedItems": [
                    "Item_Attach_Weapon_Lower_Foregrip_C",
                    "Item_Attach_Weapon_Upper_DotSight_01_C",
                    "Item_Attach_Weapon_NewPart_C",
                ],
            }
        )

        self.assertEqual(translated["itemNameKo"], "베릴 M762")
        self.assertEqual(
            translated["attachedItemsKo"],
            ["수직 손잡이", "레드 도트 사이트", "Item_Attach_Weapon_NewPart_C"],
        )

    def test_overrides_can_add_updated_codes_without_code_change(self) -> None:
        translator = CodeTranslator().with_overrides(
            {"item": {"Item_Weapon_NewThing_C": "새 무기"}}
        )

        translated = translator.translate("Item_Weapon_NewThing_C", "item")

        self.assertTrue(translated.known)
        self.assertEqual(translated.label, "새 무기")

    def test_legacy_dictionary_names_are_normalized(self) -> None:
        translator = CodeTranslator().with_overrides(
            {
                "item_id_list": {"Item_Weapon_Legacy_C": "레거시 아이템"},
                "weapon_id_list": {"WeapLegacy_C": "레거시 무기"},
                "map_name": {"Legacy_Main": "레거시 맵"},
                "deat_type": {"legacydeath": "레거시 사망"},
            }
        )

        self.assertEqual(
            translator.translate("Item_Weapon_Legacy_C", "item").label,
            "레거시 아이템",
        )
        self.assertEqual(
            translator.translate("WeapLegacy_C", "damage_causer").label,
            "레거시 무기",
        )
        self.assertEqual(translator.translate("Legacy_Main", "map").label, "레거시 맵")
        self.assertEqual(
            translator.translate("legacydeath", "death_type").label,
            "레거시 사망",
        )

    def test_event_codes_are_translated_without_losing_raw_codes(self) -> None:
        translator = CodeTranslator()

        translated = translator.translate_event_codes(
            {
                "damageCauserName": "WeapBerylM762_C",
                "mapName": "Erangel_Main",
                "item": {"itemId": "Item_Weapon_BerylM762_C"},
                "parentItem": {"itemId": "Item_Weapon_BerylM762_C"},
                "childItem": {"itemId": "Item_Attach_Weapon_Lower_Foregrip_C"},
            }
        )

        self.assertEqual(translated["damageCauserName"], "WeapBerylM762_C")
        self.assertEqual(translated["damageCauserNameKo"], "베릴 M762")
        self.assertTrue(translated["damageCauserNameKnown"])
        self.assertEqual(translated["mapNameKo"], "에란겔")
        self.assertEqual(translated["item"]["itemNameKo"], "베릴 M762")
        self.assertEqual(translated["parentItem"]["itemNameKo"], "베릴 M762")
        self.assertEqual(translated["childItem"]["itemNameKo"], "수직 손잡이")

    def test_can_load_translation_tables_from_json_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "translations.json"
            path.write_text(
                json.dumps({"item": {"Item_Custom_C": "커스텀 아이템"}}, ensure_ascii=False),
                encoding="utf-8",
            )

            translator = CodeTranslator.from_json_file(path)

            self.assertEqual(
                translator.translate("Item_Custom_C", "item").label,
                "커스텀 아이템",
            )
            self.assertEqual(
                translator.translate("Item_Weapon_BerylM762_C", "item").label,
                "베릴 M762",
            )

    def test_can_load_legacy_python_dictionary_file_safely(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "PUBG_Data.py"
            path.write_text(
                "\n".join(
                    [
                        "item_id_list = {'Item_Legacy_C': '레거시 아이템'}",
                        "weapon_id_list = {'WeapLegacy_C': '레거시 무기'}",
                        "map_name_number = {'Legacy_Main': 12}",
                    ]
                ),
                encoding="utf-8",
            )

            translator = CodeTranslator.from_python_file(path)

            self.assertEqual(
                translator.translate("Item_Legacy_C", "item").label,
                "레거시 아이템",
            )
            self.assertEqual(
                translator.translate("WeapLegacy_C", "damage_causer").label,
                "레거시 무기",
            )
            self.assertEqual(
                translator.translate("Item_Weapon_BerylM762_C", "item").label,
                "베릴 M762",
            )


if __name__ == "__main__":
    unittest.main()
