from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
import ast
import json
import re


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
        base_tables = DEFAULT_TRANSLATION_TABLES if tables is None else tables
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

        label = self._lookup_label(text, category)
        # Telemetry reports vehicle collisions through damageCauserName as well as
        # vehicleId. Reuse the vehicle dictionary before exposing a raw BP_* code.
        if label is None and category == "damage_causer":
            label = self._lookup_label(text, "vehicle")
        if label:
            return CodeTranslation(code=text, label=label, category=category, known=True)

        return CodeTranslation(code=text, label=text, category=category, known=False)

    def _lookup_label(self, text: str, category: TranslationCategory) -> str | None:
        table = self.tables.get(category, {})
        label = table.get(text)
        if label is None:
            alias = TRANSLATION_CODE_ALIASES.get((category, text))
            if alias is not None:
                label = table.get(alias)
        if label is None:
            for pattern, pattern_label in TRANSLATION_PATTERN_LABELS.get(category, ()):
                if pattern.fullmatch(text):
                    return pattern_label
        return label

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
    "shard": "shard",
    "platform": "shard",
    "match_type": "match_type",
    "team_mode": "team_mode",
    "perspective": "perspective",
    "season_state": "season_state",
    "item_category": "item_category",
    "item_sub_category": "item_sub_category",
    "item_action": "item_action",
    "activity_action": "activity_action",
    "vehicle_type": "vehicle_type",
}

EVENT_CODE_FIELDS = {
    "damageCauserName": "damage_causer",
    "mapName": "map",
    "gameMode": "game_mode",
    "vehicleId": "vehicle",
    "vehicleType": "vehicle",
}

ITEM_OBJECT_FIELDS = ["item", "parentItem", "childItem", "weapon", "victimWeapon"]

TRANSLATION_CODE_ALIASES = {
    ("damage_causer", "WeapFamasG2_C"): "WeapFAMASG2_C",
}


TRANSLATION_PATTERN_LABELS: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "vehicle": (
        (re.compile(r"Dacia_A_\d+_v2(?:_Esports)?_C"), "다시아"),
        (re.compile(r"Uaz_[ABC]_\d+(?:_esports)?_C"), "UAZ"),
        (re.compile(r"Buggy_A_\d+_C"), "버기"),
        (re.compile(r"AquaRail_A_\d+_C"), "아쿠아레일"),
        (re.compile(r"BP_Niva(?:_\d+|_Esports)?_C"), "지마"),
        (re.compile(r"BP_Mirado_A_\d+(?:_Esports)?_C"), "미라도"),
        (re.compile(r"BP_Motorbike_\d+(?:_SideCar)?_C"), "오토바이"),
        (re.compile(r"BP_Snowmobile_\d+_C"), "스노우모빌"),
        (re.compile(r"BP_Van_A_\d+_C"), "밴"),
        (re.compile(r"BP_Scooter_\d+_A_C"), "스쿠터"),
        (re.compile(r"BP_TukTukTuk_A_\d+_C"), "툭샤이"),
        (re.compile(r"BP_Mirado_Open_\d+_C"), "미라도 (오픈탑)"),
        (re.compile(r"BP_RoadGlideST_[A-Z0-9]+_C"), "CVO 로드 글라이드 ST"),
        (re.compile(r"BP_PanigaleV4S_(?:EP|LGD)\d+_C"), "두카티 파니갈레 V4 S"),
        (re.compile(r"BP_McLarenGT_[A-Za-z0-9_]+_C"), "맥라렌 GT"),
        (re.compile(r"BP_Classic_\d+_C"), "클래식 차량"),
        (re.compile(r"BP_PickupTruck_A_(?:\d+|esports)_C"), "픽업트럭"),
        (re.compile(r"BP_M_Rony_A_\d+_C"), "로니"),
        (re.compile(r"BP_Bicycle(?:_[A-Za-z0-9]+)?_C"), "산악 자전거"),
        (re.compile(r"BP_Blanc(?:_Esports)?_C"), "블랑 (쿠페 SUV)"),
        (re.compile(r"BP_Panamera_[A-Z]+_C"), "포르쉐 파나메라"),
        (re.compile(r"BP_Cayenne_[A-Z]+_C"), "포르쉐 카이엔"),
        (re.compile(r"BP_Carrera_[A-Z]+_C"), "포르쉐 911 카레라"),
        (re.compile(r"BP_Urus_[A-Z]+_C"), "람보르기니 우루스 S"),
        (re.compile(r"BP_Countach_[A-Z]+_C"), "람보르기니 쿤타치 LPI 800-4"),
        (re.compile(r"BP_DBX_[A-Z]+_C"), "애스턴 마틴 DBX707"),
        (re.compile(r"BP_Vantage_[A-Z]+_C"), "애스턴 마틴 V12 밴티지 로드스터"),
        (re.compile(r"BP_Chiron_[A-Z]+_C"), "부가티 Chiron"),
    ),
}


DEATH_TYPE_KO = {
    "alive": "생존",
    "byplayer": "유저",
    "byzone": "블루존",
    "suicide": "자살",
    "logout": "로그아웃",
}

SHARD_KO = {
    "steam": "스팀",
    "kakao": "카카오",
}

MATCH_TYPE_KO = {
    "official": "일반 매치",
    "competitive": "경쟁전",
    "airoyale": "AI 배틀로얄",
    "arcade": "아케이드",
    "event": "이벤트 모드",
    "trainingroom": "훈련장",
    "custom": "사용자 지정 매치",
    "tutorialatoz": "튜토리얼",
}

TEAM_MODE_KO = {
    "solo": "솔로",
    "duo": "듀오",
    "squad": "스쿼드",
    "unknown": "알 수 없음",
}

PERSPECTIVE_KO = {
    "fpp": "1인칭",
    "tpp": "3인칭",
}

SEASON_STATE_KO = {
    "progress": "진행 시즌",
    "in_progress": "진행 시즌",
    "closed": "종료 시즌",
    "offseason": "비시즌",
}

ITEM_CATEGORY_KO = {
    "Equipment": "장비",
    "Attachment": "부착물",
    "Ammunition": "탄약",
    "Use": "소모품",
    "Weapon": "무기",
    "None": "기타",
}

ITEM_SUB_CATEGORY_KO = {
    "None": "기타",
    "Throwable": "투척물",
    "Main": "주무기",
    "backpack": "배낭",
    "Boost": "부스트",
    "Heal": "회복",
    "Vest": "조끼",
    "Headgear": "헬멧",
    "Sight": "조준경",
    "Melee": "근접 무기",
    "Handgun": "권총",
    "Parachute": "낙하산",
    "Ascender": "등강 장비",
    "BlueChip": "블루칩",
    "Fuel": "연료",
    "Gadget": "전술 장비",
    "CamoNetting": "위장막",
    "Revive": "부활 장비",
}

ITEM_ACTION_KO = {
    "pickup": "획득",
    "equip": "장착",
    "unequip": "장착 해제",
    "use": "사용",
    "drop": "버림",
    "attach": "부착",
    "detach": "부착 해제",
    "pickup_lootbox": "루트박스 획득",
    "put_vehicle_trunk": "차량 트렁크 보관",
    "pickup_vehicle_trunk": "차량 트렁크 획득",
    "pickup_carepackage": "보급 상자 획득",
    "pickup_custom_package": "커스텀 패키지 획득",
}

ACTIVITY_ACTION_KO = {
    "heal_passive": "지속 회복",
    "heal_item": "회복 아이템 사용",
    "vehicle_damage_caused": "차량 피해",
    "object_interaction": "오브젝트 상호작용",
    "vehicle_ride": "차량 탑승",
    "vehicle_leave": "차량 하차",
    "throwable_use": "투척물 사용",
    "vault": "볼팅",
    "object_destroy": "오브젝트 파괴",
    "armor_destroy_caused": "상대 방어구 파괴",
    "armor_destroy_taken": "내 방어구 파괴",
    "revive_received": "부활 받음",
    "revive_caused": "팀원 부활",
    "wheel_destroy_caused": "바퀴 파괴",
    "carry_event": "기절 플레이어 운반",
    "vehicle_destroy_caused": "차량 파괴",
    "swim_start": "수영 시작",
    "swim_end": "수영 종료",
    "prop_destroy": "구조물 파괴",
    "flare_use": "플레어 사용",
    "breachable_wall_destroy": "파괴 가능 벽 파괴",
}

VEHICLE_TYPE_KO = {
    "WheeledVehicle": "지상 차량",
    "TransportAircraft": "수송기",
    "FlyingVehicle": "비행 탈것",
    "EmergencyPickup": "긴급 수송",
    "FloatingVehicle": "수상 탈것",
    "Mortar": "박격포",
}

GAME_MODE_KO = {
    "solo": "솔로",
    "solo-fpp": "1인칭 솔로",
    "duo": "듀오",
    "duo-fpp": "1인칭 듀오",
    "squad": "스쿼드",
    "squad-fpp": "1인칭 스쿼드",
    "normal-squad": "일반 스쿼드",
    "ibr": "인텐스 배틀로얄",
    "tdm": "팀 데스매치",
    "sdm-fpp": "솔로 데스매치 (1인칭)",
}

MAP_NAME_KO = {
    "Baltic_Main": "에란겔 리마스터",
    "Chimera_Main": "파라모",
    "Desert_Main": "미라마",
    "DihorOtok_Main": "비켄디",
    "Erangel_Main": "에란겔",
    "Heaven_Main": "헤이븐",
    "Boardwalk_Main": "보드워크",
    "Italy_TDM_Main": "이탈리아 (팀 데스매치)",
    "Kiki_Main": "데스턴",
    "Neon_Main": "론도",
    "Range_Main": "캠프 자칼",
    "Savage_Main": "사녹",
    "Summerland_Main": "카라킨",
    "Tiger_Main": "태이고",
}

ITEM_ID_KO = {
    "InstantRevivalKit_C": "긴급 소생 키트",
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
    "Item_Ammo_ZiplinegunHook_C": "집라인 건 후크",
    "Item_Armor_C_01_Lv3_C": "군용 조끼 Lv.3",
    "Item_Armor_C_00_Lv3_C": "군용 조끼 Lv.3",
    "Item_Armor_D_01_Lv2_C": "경찰 조끼 Lv.2",
    "Item_Armor_D_00_Lv2_C": "경찰 조끼 Lv.2",
    "Item_Armor_E_01_Lv1_C": "경찰 조끼 Lv.1",
    "Item_Armor_E_00_Lv1_C": "경찰 조끼 Lv.1",
    "Item_Back_C_01_Lv3_C": "배낭 Lv.3",
    "Item_Back_C_00_Lv3_C": "배낭 Lv.3",
    "Item_Back_C_02_Lv3_C": "배낭 Lv.3",
    "Item_Back_E_01_Lv1_C": "배낭 Lv.1",
    "Item_Back_E_00_Lv1_C": "배낭 Lv.1",
    "Item_Back_E_02_Lv1_C": "배낭 Lv.1",
    "Item_Back_F_01_Lv2_C": "배낭 Lv.2",
    "Item_Back_F_00_Lv2_C": "배낭 Lv.2",
    "Item_Back_F_02_Lv2_C": "배낭 Lv.2",
    "Item_Back_B_01_StartParachutePack_C": "낙하산",
    "Item_Back_BlueBlocker": "전파 방해 배낭",
    "Item_Back_BlueBlocker_Lv1": "전파 방해 배낭 Lv.1",
    "Item_Back_BlueBlocker_Lv3": "전파 방해 배낭 Lv.3",
    "Item_BulletproofShield_C": "접이식 방패",
    "Item_Boost_AdrenalineSyringe_C": "아드레날린 주사기",
    "Item_Boost_EnergyDrink_C": "에너지 드링크",
    "Item_Boost_PainKiller_C": "진통제",
    "Item_Heal_Bandage_C": "붕대",
    "Item_Heal_FirstAid_C": "구급상자",
    "Item_Heal_MedKit_C": "의료용 키트",
    "Item_Head_E_01_Lv1_C": "헬멧 Lv.1",
    "Item_Head_E_00_Lv1_C": "헬멧 Lv.1",
    "Item_Head_E_02_Lv1_C": "헬멧 Lv.1",
    "Item_Head_F_01_Lv2_C": "헬멧 Lv.2",
    "Item_Head_F_00_Lv2_C": "헬멧 Lv.2",
    "Item_Head_F_02_Lv2_C": "헬멧 Lv.2",
    "Item_Head_G_01_Lv3_C": "헬멧 Lv.3",
    "Item_Head_G_00_Lv3_C": "헬멧 Lv.3",
    "Item_JerryCan_C": "연료통",
    "Item_Tiger_SelfRevive_C": "자가제세동기",
    "Item_Bluechip_C": "블루칩",
    "Item_Revival_Transmitter_C": "부활 송신기",
    "Item_BTSecretRoom_Key_C": "비밀의 방 열쇠",
    "Item_Chimera_Key_C": "비밀의 방 열쇠",
    "Item_Desert_Key_C": "비밀의 방 열쇠",
    "Item_DihorOtok_Key_C": "비밀의 방 열쇠",
    "Item_EmergencyPickup_C": "긴급 수송",
    "Item_Neon_Key_C": "비밀의 방 열쇠",
    "Item_Tiger_Key_C": "비밀의 방 열쇠",
    "Item_Mountainbike_C": "산악 자전거",
    "Item_Rubberboat_C": "고무보트",
    "Item_SpareTire_C": "스페어 타이어",
    "Item_Special_Ascender_NoChicken_C": "등강기",
    "Item_Special_BackupParachute_C": "비상 낙하산",
    "Item_Special_Bluechip_C": "블루칩",
    "Item_Weapon_ACE32_C": "ACE32",
    "Item_Weapon_AK47_C": "AKM",
    "Item_Weapon_AUG_C": "AUG",
    "Item_Weapon_AWM_C": "AWM",
    "Item_Weapon_Apple_C": "사과",
    "Item_Weapon_Berreta686_C": "S686",
    "Item_Weapon_BerylM762_C": "베릴 M762",
    "Item_Weapon_BizonPP19_C": "PP-19 비존",
    "Item_Weapon_BluezoneGrenade_C": "블루존 수류탄",
    "Item_Weapon_C4_C": "C4",
    "Item_Weapon_Cowbar_C": "빠루",
    "Item_Weapon_CoverStructDropHandFlare_C": "비상 엄폐물 플레어",
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
    "Item_Weapon_IntegratedRepair_C": "올인원 수리 키트",
    "Item_Weapon_K2_C": "K2",
    "Item_Weapon_Kar98k_C": "Kar98k",
    "Item_Weapon_Julies_Kar98k_C": "Kar98k",
    "Item_Weapon_L6_C": "링스 AMR",
    "Item_Weapon_M16A4_C": "M16A4",
    "Item_Weapon_M1911_C": "P1911",
    "Item_Weapon_M249_C": "M249",
    "Item_Weapon_M24_C": "M24",
    "Item_Weapon_M79_C": "M79",
    "Item_Weapon_M9_C": "P92",
    "Item_Weapon_MG3_C": "MG3",
    "Item_Weapon_RPD_C": "RPD",
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
    "Item_Weapon_PackageFlare_C": "보급 플레어",
    "Item_Weapon_PackageFlare_nonDest_C": "보급 플레어",
    "Item_Weapon_Pickaxe_C": "곡괭이",
    "Item_Weapon_QBU88_C": "QBU",
    "Item_Weapon_QBZ95_C": "QBZ",
    "Item_Weapon_Rhino_C": "R45",
    "Item_Weapon_Rock_C": "돌",
    "Item_Weapon_SCAR-L_C": "SCAR-L",
    "Item_Weapon_SKS_C": "SKS",
    "Item_Weapon_Saiga12_C": "S12K",
    "Item_Weapon_Sawnoff_C": "소드오프",
    "Item_Weapon_Sickle_C": "낫",
    "Item_Weapon_SmokeBomb_C": "연막탄",
    "Item_Weapon_Snowball_C": "눈덩이",
    "Item_Weapon_SpikeTrap_C": "스파이크 트랩",
    "Item_Weapon_Spotter_Scope_C": "스포팅 스코프",
    "Item_Weapon_StickyGrenade_C": "점착 폭탄",
    "Item_Weapon_StunGun_C": "스턴건",
    "Item_Weapon_TacPack_C": "전술 가방",
    "Item_Weapon_Thompson_C": "토미건",
    "Item_Weapon_TraumaBag_C": "트라우마 백",
    "Item_Weapon_UMP_C": "UMP9",
    "Item_Weapon_UZI_C": "Micro UZI",
    "Item_Weapon_VSS_C": "VSS",
    "Item_Weapon_Vector_C": "Vector",
    "Item_Weapon_Win1894_C": "Win94",
    "Item_Weapon_Winchester_C": "S1897",
    "Item_Weapon_Ziplinegun_C": "집라인 건",
    "Item_Weapon_vz61Skorpion_C": "스콜피온",
    "Item_Attach_Weapon_Lower_AngledForeGrip_C": "앵글 손잡이",
    "Item_Attach_Weapon_Lower_Foregrip_C": "수직 손잡이",
    "Item_Attach_Weapon_Lower_HalfGrip_C": "하프 그립",
    "Item_Attach_Weapon_Lower_LaserPointer_C": "레이저 사이트",
    "Item_Attach_Weapon_Lower_LightweightForeGrip_C": "라이트 그립",
    "Item_Attach_Weapon_Lower_TiltedGrip_C": "틸티드 그립",
    "Item_Attach_Weapon_Lower_QuickDraw_Large_Crossbow_C": "석궁용 퀵드로우 화살통",
    "Item_Attach_Weapon_Lower_ThumbGrip_C": "엄지 그립",
    "Item_Attach_Weapon_Magazine_ExtendedQuickDraw_Large_C": "대용량 퀵드로우 탄창",
    "Item_Attach_Weapon_Magazine_ExtendedQuickDraw_Medium_C": "대용량 퀵드로우 탄창 (권총, SMG)",
    "Item_Attach_Weapon_Magazine_ExtendedQuickDraw_SniperRifle_C": "대용량 퀵드로우 탄창 (DMR, SR)",
    "Item_Attach_Weapon_Magazine_Extended_Large_C": "대용량 탄창",
    "Item_Attach_Weapon_Magazine_Extended_Medium_C": "대용량 탄창 (권총, SMG)",
    "Item_Attach_Weapon_Magazine_Extended_SniperRifle_C": "대용량 탄창 (DMR, SR)",
    "Item_Attach_Weapon_Magazine_QuickDraw_Large_C": "퀵드로우 탄창",
    "Item_Attach_Weapon_Magazine_QuickDraw_Medium_C": "퀵드로우 탄창 (권총, SMG)",
    "Item_Attach_Weapon_Muzzle_AR_MuzzleBrake_C": "총구 제동기",
    "Item_Attach_Weapon_Muzzle_Choke_C": "초크",
    "Item_Attach_Weapon_Muzzle_Compensator_Large_C": "보정기",
    "Item_Attach_Weapon_Muzzle_Compensator_Medium_C": "보정기 (권총, SMG)",
    "Item_Attach_Weapon_Muzzle_Compensator_SniperRifle_C": "저격소총 보정기",
    "Item_Attach_Weapon_Muzzle_Duckbill_C": "덕빌",
    "Item_Attach_Weapon_Muzzle_FlashHider_Large_C": "소염기",
    "Item_Attach_Weapon_Muzzle_FlashHider_Medium_C": "소염기 (권총, SMG)",
    "Item_Attach_Weapon_Muzzle_FlashHider_SniperRifle_C": "소염기 (DMR, SR)",
    "Item_Attach_Weapon_Muzzle_Suppressor_Large_C": "소음기",
    "Item_Attach_Weapon_Muzzle_Suppressor_Medium_C": "소음기 (권총, SMG)",
    "Item_Attach_Weapon_Muzzle_Suppressor_SniperRifle_C": "저격소총 소음기",
    "Item_Attach_Weapon_Stock_AR_Composite_C": "전술 개머리판",
    "Item_Attach_Weapon_Stock_AR_HeavyStock_C": "중량형 개머리판",
    "Item_Attach_Weapon_Stock_Shotgun_BulletLoops_C": "탄띠",
    "Item_Attach_Weapon_Stock_SniperRifle_BulletLoops_C": "탄띠 (SG, SR, Win94)",
    "Item_Attach_Weapon_Stock_SniperRifle_CheekPad_C": "칙패드",
    "Item_Attach_Weapon_Stock_UZI_C": "UZI 개머리판",
    "Item_Attach_Weapon_Upper_ACOG_01_C": "4배율 스코프",
    "Item_Attach_Weapon_Upper_Aimpoint_C": "2배율 스코프",
    "Item_Attach_Weapon_Upper_CQBSS_C": "8배율 스코프",
    "Item_Attach_Weapon_Upper_DotSight_01_C": "레드 도트 사이트",
    "Item_Attach_Weapon_Upper_DualOptic_4x1x_C": "하이브리드 스코프",
    "Item_Attach_Weapon_Upper_Holosight_C": "홀로그램 조준기",
    "Item_Attach_Weapon_Upper_PM2_01_C": "15배율 스코프",
    "Item_Attach_Weapon_Upper_Scope3x_C": "3배율 스코프",
    "Item_Attach_Weapon_Upper_Scope6x_C": "6배율 스코프",
    "Item_Attach_Weapon_Upper_Thermal_C": "열화상 스코프",
    "Item_Weapon_CamoNet_C": "무기 위장막",
    "Item_Weapon_CamoNet_Desert_C": "차량 위장망",
    "Item_Weapon_CamoNet_Taego_C": "무기 위장막 (태이고)",
    "Item_RandomBox_AR_C": "AR 무작위 상자",
    "Item_RandomBox_DmrSr_C": "DMR·SR 무작위 상자",
}

DAMAGE_CAUSER_KO = {
    "BP_BRDM_C": "BRDM-2",
    "BP_BearV2_C": "북극곰",
    "BP_CarePackageDrop_nonDest_C": "보급 상자 낙하",
    "BP_FireEffectController_C": "화염병 불길",
    "BP_MolotovFireDebuff_C": "화염병 화상",
    "BP_Niva_06_C": "지마",
    "BP_PonyCoupe_C": "포니 쿠페",
    "Buff_DecreaseBreathInApnea_C": "익사",
    "Dacia_A_02_v2_C": "다시아",
    "Dacia_A_03_v2_Esports_C": "다시아",
    "None": "없음",
    "PlayerFemale_A_C": "플레이어",
    "PlayerMale_A_C": "플레이어",
    "TslGameModeBase_BattleRoyaleBP_C": "블루존",
    "Uaz_B_01_esports_C": "UAZ",
    "UltAIPawn_Base_Female_C": "AI 플레이어",
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
    "WeapCrossbow_1_C": "석궁",
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
    "WeapGrenade_C": "수류탄",
    "WeapIntegratedRepair_C": "올인원 수리 키트",
    "WeapHK416_C": "M416",
    "WeapDuncansHK416_C": "M416",
    "WeapJS9_C": "JS9",
    "WeapJuliesKar98k_C": "Kar98k",
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
    "WeapRPD_C": "RPD",
    "WeapPickaxe_C": "곡괭이",
    "WeapZiplinegun_C": "집라인 건",
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
    "WeapPanzerFaust100M_C": "판처파우스트",
    "WeapQBU88_C": "QBU",
    "WeapQBZ95_C": "QBZ",
    "WeapRhino_C": "R45",
    "WeapSCAR-L_C": "SCAR-L",
    "WeapSKS_C": "SKS",
    "WeapSaiga12_C": "S12K",
    "WeapSawnoff_C": "소드오프",
    "WeapSickle_C": "낫",
    "WeapSickleProjectile_C": "낫 투사체",
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
    "WeapWin94_C": "Win94",
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
    "BP_Niva_06_C": "지마",
    "BP_ATV_C": "ATV",
    "ParachutePlayer_C": "낙하산",
    "DummyTransportAircraft_C": "수송기",
    "RedeployAircraft_Tiger_C": "복귀 수송기",
    "BP_EmergencyPickupVehicle_C": "긴급 수송기",
    "BP_PicoBus_C": "피코 버스",
    "BP_LootTruck_C": "루트 트럭",
    "BP_Porter_C": "포터",
    "BP_Blanc_C": "블랑 (쿠페 SUV)",
    "BP_Blanc_Esports_C": "블랑 (쿠페 SUV)",
    "BP_PonyCoupe_C": "포니 쿠페",
    "Dacia_A_02_v2_C": "다시아",
    "Dacia_A_03_v2_Esports_C": "다시아",
    "Uaz_B_01_C": "UAZ",
    "Uaz_B_01_esports_C": "UAZ",
    "Uaz_C_01_C": "UAZ",
    "BP_Uaz2_C": "UAZ",
    "BP_Dirtbike_C": "더트 바이크",
    "BP_Motorbike_04_Desert_C": "오토바이",
    "BP_Scooter_03_A_C": "스쿠터",
    "BP_Scooter_04_A_C": "스쿠터",
    "BP_TukTukTuk_A_02_C": "툭샤이",
    "MortarPawn_C": "박격포",
    "TransportAircraft_Chimera_C": "헬리콥터",
    "BP_RoadGlideST_LGD_C": "CVO 로드 글라이드 ST",
    "BP_PanigaleV4S_EP01_C": "두카티 파니갈레 V4 S",
    "BP_PanigaleV4S_LGD03_C": "두카티 파니갈레 V4 S",
    "BP_Classic_01_C": "클래식 차량",
    "BP_Classic_02_C": "클래식 차량",
    "BP_RoadGlideST_ULT_C": "CVO 로드 글라이드 ST",
    "BP_PanigaleV4S_EP02_C": "두카티 파니갈레 V4 S",
    "BP_PanigaleV4S_LGD02_C": "두카티 파니갈레 V4 S",
    "BP_McLarenGT_St_white_C": "맥라렌 GT 스탠다드 (실리카 화이트)",
    "BP_McLarenGT_St_black_C": "맥라렌 GT 스탠다드 (블랙)",
    "BP_McLarenGT_Lx_Yellow_C": "맥라렌 GT 엘리트 (볼케이노 옐로우)",
    "IBRTransportAircraft_C": "인텐스 배틀로얄 수송기",
    "IBRTransportAircraft_Helicopter_C": "인텐스 배틀로얄 헬리콥터",
}

DEFAULT_TRANSLATION_TABLES = {
    "death_type": DEATH_TYPE_KO,
    "game_mode": GAME_MODE_KO,
    "map": MAP_NAME_KO,
    "item": ITEM_ID_KO,
    "damage_causer": DAMAGE_CAUSER_KO,
    "vehicle": VEHICLE_ID_KO,
    "shard": SHARD_KO,
    "match_type": MATCH_TYPE_KO,
    "team_mode": TEAM_MODE_KO,
    "perspective": PERSPECTIVE_KO,
    "season_state": SEASON_STATE_KO,
    "item_category": ITEM_CATEGORY_KO,
    "item_sub_category": ITEM_SUB_CATEGORY_KO,
    "item_action": ITEM_ACTION_KO,
    "activity_action": ACTIVITY_ACTION_KO,
    "vehicle_type": VEHICLE_TYPE_KO,
}
