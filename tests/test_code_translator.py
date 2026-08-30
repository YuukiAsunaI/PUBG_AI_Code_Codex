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

    def test_rpd_and_current_attachment_codes_are_translated(self) -> None:
        translator = CodeTranslator()

        self.assertEqual(translator.translate("Item_Weapon_RPD_C", "item").label, "RPD")
        self.assertEqual(translator.translate("WeapRPD_C", "damage_causer").label, "RPD")
        self.assertEqual(
            translator.translate("Item_Attach_Weapon_Lower_TiltedGrip_C", "item").label,
            "틸티드 그립",
        )
        self.assertEqual(
            translator.translate("Item_Attach_Weapon_Muzzle_AR_MuzzleBrake_C", "item").label,
            "총구 제동기",
        )
        self.assertEqual(
            translator.translate("Item_Attach_Weapon_SideRail_DotSight_RMR_C", "item").label,
            "캔티드 사이트",
        )

    def test_current_real_telemetry_codes_are_translated(self) -> None:
        translator = CodeTranslator()
        cases = {
            ("sdm-fpp", "game_mode"): "솔로 데스매치 (1인칭)",
            ("InstantRevivalKit_C", "item"): "긴급 소생 키트",
            ("Item_Back_B_01_StartParachutePack_C", "item"): "낙하산",
            ("Item_Back_BlueBlocker", "item"): "전파 방해 배낭",
            ("Item_BulletproofShield_C", "item"): "접이식 방패",
            ("Item_DihorOtok_Key_C", "item"): "비밀의 방 열쇠",
            ("Item_EmergencyPickup_C", "item"): "긴급 수송",
            ("Item_Secuity_KeyCard_C", "item"): "키 카드",
            ("Item_Secuity_Keycard_C", "item"): "키 카드",
            ("Item_Weapon_Ziplinegun_C", "item"): "집라인 건",
            ("WeapFamasG2_C", "damage_causer"): "FAMAS",
            ("TslGameModeBase_BattleRoyaleBP_C", "damage_causer"): "블루존",
            ("Buff_DecreaseBreathInApnea_C", "damage_causer"): "익사",
            ("BP_Niva_06_C", "damage_causer"): "지마",
            ("BP_Niva_06_C", "vehicle"): "지마",
        }

        for (code, category), expected in cases.items():
            with self.subTest(code=code, category=category):
                translated = translator.translate(code, category)
                self.assertTrue(translated.known)
                self.assertEqual(translated.label, expected)

    def test_recent_database_codes_and_display_dimensions_are_translated(self) -> None:
        translator = CodeTranslator()
        cases = {
            ("WeapGrenade_C", "damage_causer"): "수류탄",
            ("WeapWin94_C", "damage_causer"): "Win94",
            ("WeapDuncansHK416_C", "damage_causer"): "M416",
            ("Item_SpareTire_C", "item"): "스페어 타이어",
            ("Item_Rubberboat_C", "item"): "고무보트",
            ("Item_Weapon_Julies_Kar98k_C", "item"): "Kar98k",
            ("Item_Weapon_CamoNet_Taego_C", "item"): "무기 위장막 (태이고)",
            ("Item_Weapon_CamoNet_Desert_C", "item"): "차량 위장망",
            ("Item_RandomBox_DmrSr_C", "item"): "DMR·SR 무작위 상자",
            ("WeapJuliesKar98k_C", "damage_causer"): "Kar98k",
            ("BP_Panamera_ULT_C", "vehicle"): "포르쉐 파나메라",
            ("BP_Urus_LGD_C", "vehicle"): "람보르기니 우루스 S",
            ("DummyTransportAircraft_C", "vehicle"): "수송기",
            ("steam", "shard"): "스팀",
            ("official", "match_type"): "일반 매치",
            ("squad", "team_mode"): "스쿼드",
            ("fpp", "perspective"): "1인칭",
            ("Use", "item_category"): "소모품",
            ("Heal", "item_sub_category"): "회복",
            ("pickup_lootbox", "item_action"): "루트박스 획득",
            ("vehicle_ride", "activity_action"): "차량 탑승",
            ("WheeledVehicle", "vehicle_type"): "지상 차량",
            ("BP_RoadGlideST_LGD_C", "vehicle"): "CVO 로드 글라이드 ST",
            ("TransportAircraft_Chimera_C", "vehicle"): "헬리콥터",
            ("BP_Motorbike_04_Desert_C", "vehicle"): "오토바이",
            ("BP_Dirtbike_C", "vehicle"): "더트 바이크",
            ("MortarPawn_C", "vehicle"): "박격포",
            ("BP_PanigaleV4S_EP01_C", "vehicle"): "두카티 파니갈레 V4 S",
            ("BP_PanigaleV4S_LGD03_C", "vehicle"): "두카티 파니갈레 V4 S",
            ("BP_Scooter_04_A_C", "vehicle"): "스쿠터",
            ("BP_TukTukTuk_A_02_C", "vehicle"): "툭샤이",
            ("BP_Classic_01_C", "vehicle"): "클래식 차량",
            ("BP_Uaz2_C", "vehicle"): "UAZ",
            ("ibr", "game_mode"): "인텐스 배틀로얄",
            ("normal-squad", "game_mode"): "일반 스쿼드",
            ("BP_Snowmobile_02_C", "vehicle"): "스노우모빌",
            ("BP_Van_A_03_C", "vehicle"): "밴",
            ("IBRTransportAircraft_C", "vehicle"): "인텐스 배틀로얄 수송기",
            ("IBRTransportAircraft_Helicopter_C", "vehicle"): "인텐스 배틀로얄 헬리콥터",
            ("BP_McLarenGT_St_white_C", "vehicle"): "맥라렌 GT 스탠다드 (실리카 화이트)",
            ("BP_McLarenGT_Lx_Yellow_C", "vehicle"): "맥라렌 GT 엘리트 (볼케이노 옐로우)",
            ("BP_Mirado_Open_05_C", "vehicle"): "미라도 (오픈탑)",
            ("BP_RoadGlideST_ULT_C", "vehicle"): "CVO 로드 글라이드 ST",
            ("BP_PanigaleV4S_EP02_C", "vehicle"): "두카티 파니갈레 V4 S",
            ("Boardwalk_Main", "map"): "보드워크",
            ("Italy_TDM_Main", "map"): "이탈리아 (팀 데스매치)",
            ("unknown", "team_mode"): "알 수 없음",
        }

        for (code, category), expected in cases.items():
            with self.subTest(code=code, category=category):
                translated = translator.translate(code, category)
                self.assertTrue(translated.known)
                self.assertEqual(translated.label, expected)

    def test_damage_causer_falls_back_to_vehicle_dictionary(self) -> None:
        translated = CodeTranslator().translate("BP_Snowmobile_03_C", "damage_causer")

        self.assertTrue(translated.known)
        self.assertEqual(translated.label, "스노우모빌")

    def test_explicit_empty_tables_do_not_enable_defaults(self) -> None:
        translated = CodeTranslator({}).translate("Item_Weapon_BerylM762_C", "item")

        self.assertFalse(translated.known)
        self.assertEqual(translated.label, "Item_Weapon_BerylM762_C")

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

    def test_json_file_can_explicitly_disable_default_tables(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "translations.json"
            path.write_text("{}", encoding="utf-8")

            translated = CodeTranslator.from_json_file(
                path,
                include_defaults=False,
            ).translate("Item_Weapon_BerylM762_C", "item")

            self.assertFalse(translated.known)
            self.assertEqual(translated.label, "Item_Weapon_BerylM762_C")

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
