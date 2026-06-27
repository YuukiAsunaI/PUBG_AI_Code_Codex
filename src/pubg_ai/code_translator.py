from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
import ast
import json


TranslationCategory = str


@dataclass(frozen=True)
class CodeTranslation:
    code: str
    label: str
    category: TranslationCategory
    known: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class CodeTranslator:
    def __init__(
        self,
        tables: Mapping[TranslationCategory, Mapping[str, str]] | None = None,
    ) -> None:
        base_tables = tables or DEFAULT_TRANSLATION_TABLES
        self.tables: dict[str, dict[str, str]] = {
            category: dict(values)
            for category, values in base_tables.items()
        }

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        include_defaults: bool = True,
    ) -> "CodeTranslator":
        payload = _load_translation_json(path)
        if include_defaults:
            return cls().with_overrides(payload)
        return cls(_validate_tables(payload))

    def with_json_overrides(self, path: str | Path) -> "CodeTranslator":
        return self.with_overrides(_load_translation_json(path))

    @classmethod
    def from_python_file(
        cls,
        path: str | Path,
        *,
        include_defaults: bool = True,
    ) -> "CodeTranslator":
        payload = _load_python_translation_file(path)
        if include_defaults:
            return cls().with_overrides(payload)
        return cls(_validate_tables(payload))

    def with_python_overrides(self, path: str | Path) -> "CodeTranslator":
        return self.with_overrides(_load_python_translation_file(path))

    def with_overrides(
        self,
        overrides: Mapping[TranslationCategory, Mapping[str, str]],
    ) -> "CodeTranslator":
        validated_overrides = _validate_tables(overrides)
        tables = {
            category: dict(values)
            for category, values in self.tables.items()
        }
        for category, values in validated_overrides.items():
            tables.setdefault(category, {}).update(values)
        return CodeTranslator(tables)

    def translate(self, code: Any, category: TranslationCategory) -> CodeTranslation:
        text = _string_code(code)
        if text is None:
            text = ""

        label = self.tables.get(category, {}).get(text)
        if label:
            return CodeTranslation(code=text, label=label, category=category, known=True)

        return CodeTranslation(code=text, label=text, category=category, known=False)

    def translate_auto(self, code: Any) -> CodeTranslation:
        text = _string_code(code)
        if text is None:
            return CodeTranslation(code="", label="", category="unknown", known=False)

        for category in _candidate_categories(text):
            translated = self.translate(text, category)
            if translated.known:
                return translated

        return CodeTranslation(code=text, label=text, category="unknown", known=False)

    def translate_item_object(self, item: Mapping[str, Any]) -> dict[str, Any]:
        item_id = _string_code(item.get("itemId"))
        translated = self.translate(item_id, "item") if item_id else None
        attached_items = item.get("attachedItems")

        record: dict[str, Any] = dict(item)
        if translated is not None:
            record["itemNameKo"] = translated.label
            record["itemNameKnown"] = translated.known

        if isinstance(attached_items, list):
            record["attachedItemsKo"] = [
                self.translate(attachment, "item").label
                for attachment in attached_items
            ]

        return record

    def translate_event_codes(self, event: Mapping[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = dict(event)

        for field, category in EVENT_CODE_FIELDS.items():
            if field in record:
                translated = self.translate(record[field], category)
                record[f"{field}Ko"] = translated.label
                record[f"{field}Known"] = translated.known

        for field in ITEM_OBJECT_FIELDS:
            item = record.get(field)
            if isinstance(item, Mapping):
                record[field] = self.translate_item_object(item)

        return record


def translate_code(code: Any, category: TranslationCategory = "auto") -> str:
    translator = CodeTranslator()
    if category == "auto":
        return translator.translate_auto(code).label
    return translator.translate(code, category).label


def _candidate_categories(code: str) -> list[str]:
    candidates: list[str] = []
    if code.startswith("Item_") or code in ITEM_ID_KO:
        candidates.append("item")
    if (
        code.startswith("Weap")
        or code.startswith("Proj")
        or code.endswith("_Projectile_C")
    ):
        candidates.append("damage_causer")
    if code.endswith("_Main"):
        candidates.append("map")
    if code.startswith("BP_") or code in VEHICLE_ID_KO:
        candidates.append("vehicle")
    candidates.extend(
        ["game_mode", "death_type", "item", "damage_causer", "map", "vehicle"]
    )
    return list(dict.fromkeys(candidates))


def _string_code(code: Any) -> str | None:
    if isinstance(code, str):
        return code
    if code is None:
        return None
    return str(code)


def _load_translation_json(path: str | Path) -> Mapping[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("translation dictionary root must be an object.")
    return payload


def _load_python_translation_file(path: str | Path) -> Mapping[str, Mapping[str, str]]:
    source = Path(path).read_text(encoding="utf-8")
    module = ast.parse(source)
    tables: dict[str, dict[str, str]] = {}

    for node in module.body:
        if (
            not isinstance(node, ast.Assign)
            or len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
        ):
            continue

        try:
            value = ast.literal_eval(node.value)
        except (SyntaxError, ValueError):
            continue

        if not isinstance(value, dict):
            continue
        if not all(
            isinstance(code, str) and isinstance(label, str)
            for code, label in value.items()
        ):
            continue

        tables[node.targets[0].id] = dict(value)

    return tables


def _validate_tables(payload: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    for category, values in payload.items():
        if not isinstance(category, str) or not isinstance(values, dict):
            raise ValueError("translation dictionary must be category -> object.")
        normalized_category = CATEGORY_ALIASES.get(category, category)
        table: dict[str, str] = {}
        for code, label in values.items():
            if not isinstance(code, str) or not isinstance(label, str):
                raise ValueError("translation codes and labels must be strings.")
            table[code] = label
        tables.setdefault(normalized_category, {}).update(table)
    return tables


CATEGORY_ALIASES = {
    "deat_type": "death_type",
    "death_type": "death_type",
    "game_mode": "game_mode",
    "map_name": "map",
    "map": "map",
    "item_id_list": "item",
    "all_item_id_list": "item",
    "item": "item",
    "weapon_id_list": "damage_causer",
    "weapon_id_list_2": "damage_causer",
    "weapon": "damage_causer",
    "damage_causer": "damage_causer",
    "vehicle_id_list": "vehicle",
    "vehicle": "vehicle",
}

EVENT_CODE_FIELDS = {
    "damageCauserName": "damage_causer",
    "mapName": "map",
    "gameMode": "game_mode",
    "vehicleId": "vehicle",
    "vehicleType": "vehicle",
}

ITEM_OBJECT_FIELDS = ["item", "parentItem", "childItem", "weapon", "victimWeapon"]


DEATH_TYPE_KO = {
    "alive": "생존",
    "byplayer": "유저",
    "byzone": "블루존",
    "suicide": "자살",
    "logout": "로그아웃",
}

GAME_MODE_KO = {
    "solo": "솔로",
    "solo-fpp": "1인칭 솔로",
    "duo": "듀오",
    "duo-fpp": "1인칭 듀오",
    "squad": "스쿼드",
    "squad-fpp": "1인칭 스쿼드",
    "tdm": "팀 데스매치",
}

MAP_NAME_KO = {
    "Baltic_Main": "에란겔 리마스터",
    "Chimera_Main": "파라모",
    "Desert_Main": "미라마",
    "DihorOtok_Main": "비켄디",
    "Erangel_Main": "에란겔",
    "Heaven_Main": "헤이븐",
    "Kiki_Main": "데스턴",
    "Neon_Main": "론도",
    "Range_Main": "캠프 자칼",
    "Savage_Main": "사녹",
    "Summerland_Main": "카라킨",
    "Tiger_Main": "태이고",
}

ITEM_ID_KO = {
    "Item_Ammo_12GuageSlug_C": "12게이지 슬러그탄",
    "Item_Ammo_12Guage_C": "12게이지",
    "Item_Ammo_300Magnum_C": ".300 매그넘",
    "Item_Ammo_40mm_C": "40mm 유탄",
    "Item_Ammo_45ACP_C": ".45 ACP",
    "Item_Ammo_556mm_C": "5.56mm",
    "Item_Ammo_57mm_C": "5.7mm",
    "Item_Ammo_762mm_C": "7.62mm",
    "Item_Ammo_9mm_C": "9mm",
    "Item_Ammo_Bolt_C": "석궁용 볼트",
    "Item_Ammo_Flare_C": "플레어건 탄약",
    "Item_Ammo_Mortar_C": "60mm 박격포탄",
    "Item_Armor_C_01_Lv3_C": "군용 조끼 Lv.3",
    "Item_Armor_C_00_Lv3_C": "군용 조끼 Lv.3",
    "Item_Armor_D_01_Lv2_C": "경찰 조끼 Lv.2",
    "Item_Armor_D_00_Lv2_C": "경찰 조끼 Lv.2",
    "Item_Armor_E_01_Lv1_C": "경찰 조끼 Lv.1",
    "Item_Armor_E_00_Lv1_C": "경찰 조끼 Lv.1",
    "Item_Back_C_01_Lv3_C": "배낭 Lv.3",
    "Item_Back_C_00_Lv3_C": "배낭 Lv.3",
    "Item_Back_E_01_Lv1_C": "배낭 Lv.1",
    "Item_Back_E_00_Lv1_C": "배낭 Lv.1",
    "Item_Back_F_01_Lv2_C": "배낭 Lv.2",
    "Item_Back_F_00_Lv2_C": "배낭 Lv.2",
    "Item_Boost_AdrenalineSyringe_C": "아드레날린 주사기",
    "Item_Boost_EnergyDrink_C": "에너지 드링크",
    "Item_Boost_PainKiller_C": "진통제",
    "Item_Heal_Bandage_C": "붕대",
    "Item_Heal_FirstAid_C": "구급상자",
    "Item_Heal_MedKit_C": "의료용 키트",
    "Item_Head_E_01_Lv1_C": "헬멧 Lv.1",
    "Item_Head_E_00_Lv1_C": "헬멧 Lv.1",
    "Item_Head_F_01_Lv2_C": "헬멧 Lv.2",
    "Item_Head_F_00_Lv2_C": "헬멧 Lv.2",
    "Item_Head_G_01_Lv3_C": "헬멧 Lv.3",
    "Item_Head_G_00_Lv3_C": "헬멧 Lv.3",
    "Item_JerryCan_C": "연료통",
    "Item_Tiger_SelfRevive_C": "자가제세동기",
    "Item_Bluechip_C": "블루칩",
    "Item_Revival_Transmitter_C": "부활 송신기",
    "Item_Weapon_ACE32_C": "ACE32",
    "Item_Weapon_AK47_C": "AKM",
    "Item_Weapon_AUG_C": "AUG",
    "Item_Weapon_AWM_C": "AWM",
    "Item_Weapon_Berreta686_C": "S686",
    "Item_Weapon_BerylM762_C": "베릴 M762",
    "Item_Weapon_BizonPP19_C": "PP-19 비존",
    "Item_Weapon_BluezoneGrenade_C": "블루존 수류탄",
    "Item_Weapon_C4_C": "C4",
    "Item_Weapon_Cowbar_C": "빠루",
    "Item_Weapon_Crossbow_C": "석궁",
    "Item_Weapon_DP12_C": "DBS",
    "Item_Weapon_DP28_C": "DP-28",
    "Item_Weapon_DecoyGrenade_C": "교란 수류탄",
    "Item_Weapon_DesertEagle_C": "Deagle",
    "Item_Weapon_Dragunov_C": "드라구노프",
    "Item_Weapon_FAMASG2_C": "FAMAS",
    "Item_Weapon_FNFal_C": "SLR",
    "Item_Weapon_FlareGun_C": "플레어건",
    "Item_Weapon_FlashBang_C": "섬광탄",
    "Item_Weapon_G18_C": "P18C",
    "Item_Weapon_G36C_C": "G36C",
    "Item_Weapon_Grenade_C": "수류탄",
    "Item_Weapon_Groza_C": "그로자",
    "Item_Weapon_HK416_C": "M416",
    "Item_Weapon_JS9_C": "JS9",
    "Item_Weapon_K2_C": "K2",
    "Item_Weapon_Kar98k_C": "Kar98k",
    "Item_Weapon_L6_C": "링스 AMR",
    "Item_Weapon_M16A4_C": "M16A4",
    "Item_Weapon_M1911_C": "P1911",
    "Item_Weapon_M249_C": "M249",
    "Item_Weapon_M24_C": "M24",
    "Item_Weapon_M79_C": "M79",
    "Item_Weapon_M9_C": "P92",
    "Item_Weapon_MG3_C": "MG3",
    "Item_Weapon_MP5K_C": "MP5K",
    "Item_Weapon_MP9_C": "MP9",
    "Item_Weapon_Machete_C": "마체테",
    "Item_Weapon_Mads_QBU88_C": "QBU",
    "Item_Weapon_Mini14_C": "Mini14",
    "Item_Weapon_Mk12_C": "Mk12",
    "Item_Weapon_Mk14_C": "Mk14",
    "Item_Weapon_Mk47Mutant_C": "Mk47 뮤턴트",
    "Item_Weapon_Molotov_C": "화염병",
    "Item_Weapon_Mortar_C": "박격포",
    "Item_Weapon_Mosin_C": "모신 나강",
    "Item_Weapon_NagantM1895_C": "R1895",
    "Item_Weapon_OriginS12_C": "O12",
    "Item_Weapon_P90_C": "P90",
    "Item_Weapon_Pan_C": "프라이팬",
    "Item_Weapon_PanzerFaust100M_C": "판처파우스트",
    "Item_Weapon_QBU88_C": "QBU",
    "Item_Weapon_QBZ95_C": "QBZ",
    "Item_Weapon_Rhino_C": "R45",
    "Item_Weapon_SCAR-L_C": "SCAR-L",
    "Item_Weapon_SKS_C": "SKS",
    "Item_Weapon_Saiga12_C": "S12K",
    "Item_Weapon_Sawnoff_C": "소드오프",
    "Item_Weapon_Sickle_C": "낫",
    "Item_Weapon_SmokeBomb_C": "연막탄",
    "Item_Weapon_SpikeTrap_C": "스파이크 트랩",
    "Item_Weapon_Spotter_Scope_C": "스포팅 스코프",
    "Item_Weapon_StickyGrenade_C": "점착 폭탄",
    "Item_Weapon_StunGun_C": "스턴건",
    "Item_Weapon_Thompson_C": "토미건",
    "Item_Weapon_TraumaBag_C": "트라우마 백",
    "Item_Weapon_UMP_C": "UMP9",
    "Item_Weapon_UZI_C": "Micro UZI",
    "Item_Weapon_VSS_C": "VSS",
    "Item_Weapon_Vector_C": "Vector",
    "Item_Weapon_Win1894_C": "Win94",
    "Item_Weapon_Winchester_C": "S1897",
    "Item_Weapon_vz61Skorpion_C": "스콜피온",
    "Item_Attach_Weapon_Lower_AngledForeGrip_C": "앵글 손잡이",
    "Item_Attach_Weapon_Lower_Foregrip_C": "수직 손잡이",
    "Item_Attach_Weapon_Lower_HalfGrip_C": "하프 그립",
    "Item_Attach_Weapon_Lower_LaserPointer_C": "레이저 사이트",
    "Item_Attach_Weapon_Lower_LightweightForeGrip_C": "라이트 그립",
    "Item_Attach_Weapon_Lower_ThumbGrip_C": "엄지 그립",
    "Item_Attach_Weapon_Magazine_ExtendedQuickDraw_Large_C": "대용량 퀵드로우 탄창",
    "Item_Attach_Weapon_Magazine_Extended_Large_C": "대용량 탄창",
    "Item_Attach_Weapon_Magazine_QuickDraw_Large_C": "퀵드로우 탄창",
    "Item_Attach_Weapon_Muzzle_Choke_C": "초크",
    "Item_Attach_Weapon_Muzzle_Compensator_Large_C": "보정기",
    "Item_Attach_Weapon_Muzzle_Compensator_SniperRifle_C": "저격소총 보정기",
    "Item_Attach_Weapon_Muzzle_Duckbill_C": "덕빌",
    "Item_Attach_Weapon_Muzzle_FlashHider_Large_C": "소염기",
    "Item_Attach_Weapon_Muzzle_Suppressor_Large_C": "소음기",
    "Item_Attach_Weapon_Muzzle_Suppressor_SniperRifle_C": "저격소총 소음기",
    "Item_Attach_Weapon_Stock_AR_Composite_C": "전술 개머리판",
    "Item_Attach_Weapon_Stock_AR_HeavyStock_C": "중량형 개머리판",
    "Item_Attach_Weapon_Stock_Shotgun_BulletLoops_C": "탄띠",
    "Item_Attach_Weapon_Stock_SniperRifle_CheekPad_C": "칙패드",
    "Item_Attach_Weapon_Stock_UZI_C": "UZI 개머리판",
    "Item_Attach_Weapon_Upper_ACOG_01_C": "4배율 스코프",
    "Item_Attach_Weapon_Upper_Aimpoint_C": "2배율 스코프",
    "Item_Attach_Weapon_Upper_CQBSS_C": "8배율 스코프",
    "Item_Attach_Weapon_Upper_DotSight_01_C": "레드 도트 사이트",
    "Item_Attach_Weapon_Upper_Holosight_C": "홀로그램 조준기",
    "Item_Attach_Weapon_Upper_PM2_01_C": "15배율 스코프",
    "Item_Attach_Weapon_Upper_Scope3x_C": "3배율 스코프",
    "Item_Attach_Weapon_Upper_Scope6x_C": "6배율 스코프",
    "Item_Attach_Weapon_Upper_Thermal_C": "열화상 스코프",
}

DAMAGE_CAUSER_KO = {
    "WeapACE32_C": "ACE32",
    "WeapAK47_C": "AKM",
    "WeapAUG_C": "AUG",
    "WeapAWM_C": "AWM",
    "WeapBerreta686_C": "S686",
    "WeapBerylM762_C": "베릴 M762",
    "WeapBizonPP19_C": "PP-19 비존",
    "WeapBluezoneGrenade_C": "블루존 수류탄",
    "WeapC4_C": "C4",
    "WeapCowbar_C": "빠루",
    "WeapCrossbow_C": "석궁",
    "WeapDP12_C": "DBS",
    "WeapDP28_C": "DP-28",
    "WeapDecoyGrenade_C": "교란 수류탄",
    "WeapDesertEagle_C": "Deagle",
    "WeapDragunov_C": "드라구노프",
    "WeapFAMASG2_C": "FAMAS",
    "WeapFNFal_C": "SLR",
    "WeapFlareGun_C": "플레어건",
    "WeapFlashBang_C": "섬광탄",
    "WeapG18_C": "P18C",
    "WeapG36C_C": "G36C",
    "WeapGroza_C": "그로자",
    "WeapHK416_C": "M416",
    "WeapJS9_C": "JS9",
    "WeapK2_C": "K2",
    "WeapKar98k_C": "Kar98k",
    "WeapL6_C": "링스 AMR",
    "WeapM16A4_C": "M16A4",
    "WeapM1911_C": "P1911",
    "WeapM249_C": "M249",
    "WeapM24_C": "M24",
    "WeapM79_C": "M79",
    "WeapM9_C": "P92",
    "WeapMG3_C": "MG3",
    "WeapMP5K_C": "MP5K",
    "WeapMP9_C": "MP9",
    "WeapMachete_C": "마체테",
    "WeapMads_QBU88_C": "QBU",
    "WeapMini14_C": "Mini14",
    "WeapMk12_C": "Mk12",
    "WeapMk14_C": "Mk14",
    "WeapMk47Mutant_C": "Mk47 뮤턴트",
    "WeapMolotov_C": "화염병",
    "WeapMortar_C": "박격포",
    "WeapMosin_C": "모신 나강",
    "WeapNagantM1895_C": "R1895",
    "WeapOriginS12_C": "O12",
    "WeapP90_C": "P90",
    "WeapPan_C": "프라이팬",
    "WeapQBU88_C": "QBU",
    "WeapQBZ95_C": "QBZ",
    "WeapRhino_C": "R45",
    "WeapSCAR-L_C": "SCAR-L",
    "WeapSKS_C": "SKS",
    "WeapSaiga12_C": "S12K",
    "WeapSawnoff_C": "소드오프",
    "WeapSickle_C": "낫",
    "WeapSmokeBomb_C": "연막탄",
    "WeapSpikeTrap_C": "스파이크 트랩",
    "WeapSpotter_Scope_C": "스포팅 스코프",
    "WeapStickyGrenade_C": "점착 폭탄",
    "WeapStunGun_C": "스턴건",
    "WeapThompson_C": "토미건",
    "WeapTraumaBag_C": "트라우마 백",
    "WeapUMP_C": "UMP9",
    "WeapUZI_C": "Micro UZI",
    "WeapVSS_C": "VSS",
    "WeapVector_C": "Vector",
    "WeapWin1894_C": "Win94",
    "WeapWinchester_C": "S1897",
    "Weapvz61Skorpion_C": "스콜피온",
    "ProjGrenade_C": "수류탄",
    "ProjGrenade_Warmode_C": "수류탄",
    "PanzerFaust100M_Projectile_C": "판처파우스트",
}

VEHICLE_ID_KO = {
    "AquaRail_A_00_C": "아쿠아레일",
    "Boat_PG117_C": "보트",
    "Buggy_A_00_C": "버기",
    "Dacia_A_00_v2_C": "다시아",
    "Uaz_A_00_C": "UAZ",
    "Uaz_B_00_C": "UAZ",
    "Uaz_C_00_C": "UAZ",
    "BP_Mirado_A_00_C": "미라도",
    "BP_Motorbike_00_C": "오토바이",
    "BP_Motorbike_00_SideCar_C": "사이드카 오토바이",
    "BP_PickupTruck_A_00_C": "픽업트럭",
    "BP_BRDM_C": "BRDM",
    "BP_Motorglider_C": "모터글라이더",
    "BP_CoupeRB_C": "쿠페 RB",
    "BP_ATV_C": "ATV",
    "ParachutePlayer_C": "낙하산",
}

DEFAULT_TRANSLATION_TABLES = {
    "death_type": DEATH_TYPE_KO,
    "game_mode": GAME_MODE_KO,
    "map": MAP_NAME_KO,
    "item": ITEM_ID_KO,
    "damage_causer": DAMAGE_CAUSER_KO,
    "vehicle": VEHICLE_ID_KO,
}
