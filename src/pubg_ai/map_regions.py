from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot, isfinite
from typing import Any, Literal

from pubg_ai.code_translator import translate_code
from pubg_ai.map_snapshot_renderer import MAP_WORLD_SIZE_CM


MAP_REGION_CATALOG_VERSION = "2024-10-28.api-assets-32b13b5.v1"
MAP_REGION_SOURCE_COMMIT = "32b13b51128b8d8909ae5e77f3b833e01230b24d"
MAP_REGION_SOURCE_REPOSITORY = "https://github.com/pubg/api-assets"
MAP_REGION_SOURCE_BASE_URL = (
    "https://raw.githubusercontent.com/pubg/api-assets/"
    f"{MAP_REGION_SOURCE_COMMIT}/Assets/Maps"
)

MapRegionPolicy = Literal["static", "dynamic"]
MapRegionStatus = Literal[
    "matched",
    "unmatched",
    "dynamic_map",
    "unsupported_map",
    "invalid_coordinate",
]


@dataclass(frozen=True)
class MapRegionDefinition:
    region_id: str
    name: str
    name_ko: str
    center_x_pct: float
    center_y_pct: float
    radius_pct: float
    geometry_type: str = "circle"

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapRegionResolution:
    status: MapRegionStatus
    map_name: str
    map_name_ko: str
    canonical_map_name: str | None
    catalog_version: str
    source_commit: str
    source_asset: str | None
    source_url: str | None
    source_sha256: str | None
    x_cm: float | None
    y_cm: float | None
    x_pct: float | None
    y_pct: float | None
    region_id: str | None = None
    region_name: str | None = None
    region_name_ko: str | None = None
    geometry_type: str | None = None
    distance_to_center_m: float | None = None
    radius_m: float | None = None

    @property
    def region_display_name_ko(self) -> str | None:
        return self.region_name_ko if self.status == "matched" else None

    def to_record(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "region_display_name_ko": self.region_display_name_ko,
        }


_CANONICAL_MAPS = {
    "Baltic_Main": "Erangel_Main",
    "Erangel_Main": "Erangel_Main",
    "Desert_Main": "Miramar_Main",
    "DihorOtok_Main": "Vikendi_Main",
    "Savage_Main": "Sanhok_Main",
    "Summerland_Main": "Karakin_Main",
    "Tiger_Main": "Taego_Main",
    "Chimera_Main": "Paramo_Main",
    "Neon_Main": "Rondo_Main",
    "Range_Main": "Camp_Jackal_Main",
    "Kiki_Main": "Deston_Main",
    "Heaven_Main": "Haven_Main",
}

_SOURCE_ASSETS = {
    "Erangel_Main": "Erangel_Main_Low_Res.png",
    "Miramar_Main": "Miramar_Main_Low_Res.png",
    "Vikendi_Main": "Vikendi_Main_Low_Res.png",
    "Sanhok_Main": "Sanhok_Main_Low_Res.png",
    "Karakin_Main": "Karakin_Main_Low_Res.png",
    "Taego_Main": "Taego_Main_Low_Res.png",
    "Paramo_Main": "Paramo_Main_Low_Res.png",
    "Rondo_Main": "Rondo_Main_Low_Res.png",
    "Camp_Jackal_Main": "Camp_Jackal_Main_Low_Res.png",
    "Deston_Main": "Deston_Main_Low_Res.png",
    "Haven_Main": "Haven_Main_Low_Res.png",
}

_SOURCE_SHA256 = {
    "Camp_Jackal_Main_Low_Res.png": "acd5e86e095680aec13830a8e26622cba4dc787cfc675ae9b4674d569203449d",
    "Deston_Main_Low_Res.png": "be35aeb55781edd47ac547fa51b2d9c84244b55dcbc2fd621198b60aeb4da0b7",
    "Erangel_Main_Low_Res.png": "56bd4bf0bdcd5e902ef4c232bc54d9546ae0a6cc8b881833f45f0f0a9e93aea9",
    "Haven_Main_Low_Res.png": "287435cb7b9e5f92862ac79f65c86839f9a4dcbf61c7287342caa3c85907c5fa",
    "Karakin_Main_Low_Res.png": "87fd5c4f956a1a05858cc069fdd4da33d8ffa56d31eb9182b6038a0fbe6f216b",
    "Miramar_Main_Low_Res.png": "25e47254abf6a1b339f4a030ed323f8947fcbd0628c530eba7c0973d5d45ea14",
    "Paramo_Main_Low_Res.png": "dd40f9ede3225abc07421c46148f8fc76a4f87997612e68536c6c6a89c4ac4b1",
    "Rondo_Main_Low_Res.png": "f5d8e3a390f9d979396f8055eb9a555157d12e94a57b62452c69f21f661eee14",
    "Sanhok_Main_Low_Res.png": "184561634b90ab2e981df6edd4813f2ac6e165d5d62ed3cbe684eec079b03855",
    "Taego_Main_Low_Res.png": "dcf2679fa735b0b112d69783fc022b7699ee2523d6e765640b8a0b279bfe4c45",
    "Vikendi_Main_Low_Res.png": "a2fcd39d6ae87d82fc5747389f946e1f03ddb946ea9ac67e9944a0dcd3c05516",
}

_DYNAMIC_MAPS = {"Paramo_Main"}


def _regions(
    map_slug: str,
    rows: tuple[tuple[str, str, str, float, float, float], ...],
) -> tuple[MapRegionDefinition, ...]:
    return tuple(
        MapRegionDefinition(
            region_id=f"{map_slug}.{key}",
            name=name,
            name_ko=name_ko,
            center_x_pct=x_pct,
            center_y_pct=y_pct,
            radius_pct=radius_pct,
        )
        for key, name, name_ko, x_pct, y_pct, radius_pct in rows
    )


_REGIONS_BY_CANONICAL_MAP = {
    "Erangel_Main": _regions(
        "erangel",
        (
            ("zharki", "Zharki", "자르키", 0.13, 0.15, 0.055),
            ("severny", "Severny", "세베르니", 0.47, 0.14, 0.055),
            ("kameshki", "Kameshki", "카메시키", 0.80, 0.13, 0.055),
            ("shooting_range", "Shooting Range", "사격장", 0.41, 0.22, 0.055),
            ("stalber", "Stalber", "스탈베르", 0.70, 0.16, 0.060),
            ("georgopol", "Georgopol", "게오르고폴", 0.24, 0.31, 0.080),
            ("hospital", "Hospital", "병원", 0.18, 0.40, 0.050),
            ("gatka", "Gatka", "갓카", 0.26, 0.47, 0.050),
            ("ruins", "Ruins", "유적", 0.39, 0.42, 0.050),
            ("water_town", "Water Town", "워터 타운", 0.44, 0.38, 0.045),
            ("rozhok", "Rozhok", "로족", 0.49, 0.35, 0.050),
            ("school", "School", "학교", 0.51, 0.40, 0.045),
            ("yasnaya_polyana", "Yasnaya Polyana", "야스나야 폴랴나", 0.63, 0.29, 0.080),
            ("mansion", "Mansion", "맨션", 0.72, 0.39, 0.045),
            ("lipovka", "Lipovka", "리포브카", 0.86, 0.42, 0.055),
            ("shelter", "Shelter", "쉘터", 0.70, 0.47, 0.040),
            ("prison", "Prison", "교도소", 0.78, 0.47, 0.045),
            ("pochinki", "Pochinki", "포친키", 0.48, 0.49, 0.060),
            ("farm", "Farm", "농장", 0.66, 0.55, 0.045),
            ("mylta", "Mylta", "밀타", 0.75, 0.57, 0.060),
            ("mylta_power", "Mylta Power", "밀타 발전소", 0.87, 0.54, 0.060),
            ("quarry", "Quarry", "채석장", 0.20, 0.68, 0.055),
            ("ferry_pier", "Ferry Pier", "페리 선착장", 0.35, 0.72, 0.050),
            ("primorsk", "Primorsk", "프리모스크", 0.20, 0.75, 0.060),
            (
                "sosnovka_military_base",
                "Sosnovka Military Base",
                "소스노브카 군사기지",
                0.56,
                0.80,
                0.110,
            ),
            ("novorepnoye", "Novorepnoye", "노보레프노예", 0.75, 0.74, 0.070),
        ),
    ),
    "Miramar_Main": _regions(
        "miramar",
        (
            ("oasis", "Oasis", "오아시스", 0.46, 0.10, 0.055),
            ("campo_militar", "Campo Militar", "캄포 밀리타르", 0.71, 0.11, 0.070),
            ("tierra_bronca", "Tierra Bronca", "티에라 브롱카", 0.81, 0.15, 0.060),
            ("la_cobreria", "La Cobreria", "라 코브레리아", 0.31, 0.17, 0.060),
            ("cruz_del_valle", "Cruz del Valle", "크루스 델 바예", 0.67, 0.18, 0.060),
            ("alcantara", "Alcantara", "알칸타라", 0.10, 0.25, 0.060),
            ("crater_fields", "Crater Fields", "크레이터 필즈", 0.24, 0.25, 0.055),
            ("water_treatment", "Water Treatment", "정수장", 0.52, 0.25, 0.055),
            ("el_azahar", "El Azahar", "엘 아자르", 0.78, 0.32, 0.065),
            ("el_pozo", "El Pozo", "엘 포조", 0.20, 0.35, 0.080),
            ("san_martin", "San Martin", "산 마르틴", 0.47, 0.36, 0.055),
            (
                "hacienda_del_patron",
                "Hacienda del Patron",
                "아시엔다 델 파트론",
                0.55,
                0.36,
                0.045,
            ),
            ("power_grid", "Power Grid", "파워 그리드", 0.39, 0.45, 0.050),
            ("graveyard", "Graveyard", "묘지", 0.55, 0.47, 0.045),
            ("minas_generales", "Minas Generales", "미나스 제네랄레스", 0.63, 0.47, 0.060),
            ("truck_stop", "Truck Stop", "트럭 정류장", 0.74, 0.42, 0.045),
            ("monte_nuevo", "Monte Nuevo", "몬테 누에보", 0.27, 0.48, 0.065),
            ("pecado", "Pecado", "페카도", 0.45, 0.53, 0.055),
            ("cantera", "Cantera", "칸테라", 0.62, 0.51, 0.055),
            ("impala", "Impala", "임팔라", 0.77, 0.56, 0.060),
            ("brick_yard", "Brick Yard", "벽돌 공장", 0.24, 0.58, 0.050),
            ("chumacera", "Chumacera", "추마세라", 0.34, 0.64, 0.065),
            ("los_leones", "Los Leones", "로스 레오네스", 0.56, 0.66, 0.095),
            ("valle_del_mar", "Valle del Mar", "바예 델 마르", 0.16, 0.76, 0.065),
            ("puerto_paraiso", "Puerto Paraiso", "푸에르토 파라이소", 0.76, 0.77, 0.070),
            ("resort", "Resort", "리조트", 0.56, 0.82, 0.050),
            ("prison", "Prison", "교도소", 0.15, 0.88, 0.060),
            ("partona", "Partona", "파르토나", 0.42, 0.90, 0.055),
        ),
    ),
    "Vikendi_Main": _regions(
        "vikendi",
        (
            ("coal_mine", "Coal Mine", "석탄 광산", 0.20, 0.25, 0.065),
            ("observatory", "Observatory", "천문대", 0.47, 0.19, 0.060),
            ("cosmodrome", "Cosmodrome", "코스모드롬", 0.75, 0.17, 0.075),
            ("naros", "Naros", "나로스", 0.43, 0.37, 0.055),
            ("laveni", "Laveni", "라베니", 0.65, 0.35, 0.060),
            ("dinoland", "Dinoland", "다이노랜드", 0.23, 0.44, 0.065),
            ("villa", "Villa", "빌라", 0.84, 0.42, 0.050),
            ("lumber_yard", "Lumber Yard", "제재소", 0.30, 0.56, 0.060),
            ("train_station", "Train Station", "기차역", 0.60, 0.51, 0.070),
            ("naznova", "Naznova", "나즈노바", 0.11, 0.63, 0.060),
            ("tika", "Tika", "티카", 0.86, 0.60, 0.055),
            ("castle", "Castle", "성", 0.66, 0.65, 0.055),
            ("deka_mesto", "Deka Mesto", "데카 메스토", 0.47, 0.70, 0.075),
            ("pavlovo", "Pavlovo", "파블로보", 0.23, 0.79, 0.060),
            ("winery", "Winery", "와이너리", 0.39, 0.84, 0.060),
            ("kranik", "Kranik", "크라닉", 0.75, 0.82, 0.060),
        ),
    ),
    "Sanhok_Main": _regions(
        "sanhok",
        (
            ("khao", "Khao", "카오", 0.57, 0.21, 0.055),
            ("mongnai", "Mongnai", "몽나이", 0.77, 0.21, 0.055),
            ("tat_mok", "Tat Mok", "탓목", 0.53, 0.26, 0.050),
            ("ha_tinh", "Ha Tinh", "하띤", 0.30, 0.30, 0.060),
            ("paradise_resort", "Paradise Resort", "파라다이스 리조트", 0.61, 0.36, 0.065),
            ("camp_bravo", "Camp Bravo", "캠프 브라보", 0.83, 0.40, 0.065),
            ("camp_alpha", "Camp Alpha", "캠프 알파", 0.19, 0.42, 0.070),
            ("bootcamp", "Bootcamp", "부트캠프", 0.48, 0.51, 0.065),
            ("bhan", "Bhan", "반", 0.72, 0.50, 0.050),
            ("lakawi", "Lakawi", "라카위", 0.85, 0.56, 0.055),
            ("quarry", "Quarry", "쿼리", 0.65, 0.62, 0.060),
            ("ruins", "Ruins", "루인스", 0.29, 0.64, 0.060),
            ("kampong", "Kampong", "캄퐁", 0.82, 0.66, 0.060),
            ("pai_nan", "Pai Nan", "파이난", 0.44, 0.69, 0.060),
            ("tambang", "Tambang", "탐방", 0.21, 0.72, 0.055),
            ("cave", "Cave", "케이브", 0.65, 0.76, 0.055),
            ("na_kham", "Na Kham", "나캄", 0.27, 0.82, 0.060),
            ("camp_charlie", "Camp Charlie", "캠프 찰리", 0.59, 0.87, 0.075),
            ("docks", "Docks", "도크", 0.81, 0.86, 0.060),
            ("sahmee", "Sahmee", "사미", 0.36, 0.90, 0.060),
            ("ban_tai", "Ban Tai", "반타이", 0.57, 0.92, 0.055),
        ),
    ),
    "Karakin_Main": _regions(
        "karakin",
        (
            ("bahr_sahir", "Bahr Sahir", "바르 사히르", 0.36, 0.27, 0.080),
            ("al_habar", "Al Habar", "알 하바르", 0.73, 0.29, 0.085),
            ("bashara", "Bashara", "바샤라", 0.21, 0.54, 0.080),
            ("hadiqa_nemo", "Hadiqa Nemo", "하디카 네모", 0.72, 0.66, 0.080),
            ("cargo_ship", "Cargo Ship", "화물선", 0.10, 0.86, 0.060),
            ("al_hayik", "Al Hayik", "알 하이크", 0.48, 0.86, 0.075),
        ),
    ),
    "Taego_Main": _regions(
        "taego",
        (
            ("army_base", "Army Base", "군사기지", 0.47, 0.13, 0.070),
            ("shipyard", "Shipyard", "조선소", 0.70, 0.18, 0.075),
            ("wol_song", "Wol Song", "월송", 0.19, 0.20, 0.060),
            ("hae_moo_sa", "Hae Moo Sa", "해무사", 0.12, 0.30, 0.060),
            ("go_dok", "Go Dok", "고독", 0.31, 0.30, 0.060),
            ("yong_cheon", "Yong Cheon", "용천", 0.56, 0.30, 0.080),
            ("airport", "Airport", "공항", 0.91, 0.36, 0.080),
            ("palace", "Palace", "궁전", 0.37, 0.44, 0.060),
            ("terminal", "Terminal", "터미널", 0.58, 0.44, 0.070),
            ("fishing_camp", "Fishing Camp", "낚시터", 0.30, 0.52, 0.055),
            ("ha_po", "Ha Po", "하포", 0.14, 0.54, 0.065),
            ("kang_neung", "Kang Neung", "강릉", 0.80, 0.55, 0.075),
            ("ho_san", "Ho San", "호산", 0.47, 0.62, 0.070),
            ("buk_san_sa", "Buk San Sa", "북산사", 0.62, 0.63, 0.055),
            ("ho_san_prison", "Ho San Prison", "호산 교도소", 0.21, 0.72, 0.065),
            ("oh_hyang", "Oh Hyang", "오향", 0.69, 0.73, 0.065),
            ("school", "School", "학교", 0.38, 0.79, 0.055),
            ("song_am", "Song Am", "송암", 0.50, 0.84, 0.065),
            ("hospital", "Hospital", "병원", 0.68, 0.84, 0.055),
        ),
    ),
    "Rondo_Main": _regions(
        "rondo",
        (
            ("jadena_city", "Jadena City", "자데나 시티", 0.86, 0.68, 0.100),
            ("stadium", "Stadium", "스타디움", 0.36, 0.31, 0.070),
            ("jao_tin", "Jao Tin", "자오틴", 0.21, 0.39, 0.085),
            ("rin_jiang", "Rin Jiang", "린장", 0.35, 0.84, 0.085),
            ("tin_long_garden", "Tin Long Garden", "틴롱 가든", 0.63, 0.86, 0.085),
            ("yu_lin", "Yu Lin", "유린", 0.37, 0.54, 0.065),
            ("neox_factory", "NEOX Factory", "네오엑스 팩토리", 0.60, 0.46, 0.070),
            ("test_track", "Test Track", "테스트 트랙", 0.55, 0.41, 0.070),
            ("mey_ran", "Mey Ran", "메이란", 0.78, 0.40, 0.070),
            ("lo_hua_xing", "Lo Hua Xing", "로화싱", 0.14, 0.86, 0.070),
            ("bei_li", "Bei Li", "베이리", 0.12, 0.24, 0.070),
            ("hung_shan", "Hung Shan", "훙산", 0.45, 0.77, 0.070),
            ("fong_tun", "Fong Tun", "퐁툰", 0.13, 0.61, 0.075),
        ),
    ),
    "Camp_Jackal_Main": _regions(
        "camp_jackal",
        (
            ("race_track", "Race Track", "레이스 트랙", 0.22, 0.40, 0.140),
            ("docks", "Docks", "부두", 0.52, 0.43, 0.085),
            ("bridge", "Bridge", "다리", 0.51, 0.26, 0.060),
            ("gas_station", "Gas Station", "주유소", 0.80, 0.34, 0.060),
            ("range_200m", "200m Range", "200m 사격장", 0.65, 0.38, 0.065),
            ("urban_combat", "Urban Combat", "도심 전투장", 0.58, 0.56, 0.080),
            ("range_800m", "800m Range", "800m 사격장", 0.71, 0.55, 0.060),
            ("range_400m", "400m Range", "400m 사격장", 0.46, 0.70, 0.065),
            ("cqc_range", "CQC Range", "근접 전투장", 0.66, 0.65, 0.060),
            (
                "movement_grenade",
                "Movement and Grenade Range",
                "이동·수류탄 훈련장",
                0.68,
                0.72,
                0.065,
            ),
        ),
    ),
    "Deston_Main": _regions(
        "deston",
        (
            ("ten_forts", "Ten Forts", "텐 포츠", 0.38, 0.05, 0.060),
            ("carpenters_end", "Carpenter's End", "카펜터스 엔드", 0.69, 0.10, 0.065),
            ("swamp", "Swamp", "늪지", 0.43, 0.14, 0.070),
            ("concert", "Concert", "콘서트", 0.62, 0.21, 0.055),
            ("assembly", "Assembly", "어셈블리", 0.77, 0.25, 0.070),
            ("los_arcos", "Los Arcos", "로스 아르코스", 0.25, 0.26, 0.075),
            ("buxley", "Buxley", "벅슬리", 0.52, 0.33, 0.075),
            ("construction_site", "Construction Site", "건설 현장", 0.36, 0.39, 0.060),
            ("barclift", "Barclift", "바클리프트", 0.20, 0.42, 0.060),
            ("turrita", "Turrita", "투리타", 0.35, 0.45, 0.060),
            ("wind_farm", "Wind Farm", "풍력 발전소", 0.88, 0.42, 0.070),
            ("cavala", "Cavala", "카발라", 0.23, 0.55, 0.070),
            ("arena", "Arena", "아레나", 0.57, 0.49, 0.060),
            ("lodge", "Lodge", "로지", 0.46, 0.56, 0.060),
            ("hydroelectric_dam", "Hydroelectric Dam", "수력 발전 댐", 0.29, 0.62, 0.065),
            ("ripton", "Ripton", "립톤", 0.72, 0.62, 0.105),
            ("sancarna", "Sancarna", "산카르나", 0.40, 0.70, 0.060),
            ("el_koro", "El Koro", "엘 코로", 0.15, 0.74, 0.065),
            ("holston_meadows", "Holston Meadows", "홀스턴 메도스", 0.69, 0.82, 0.085),
        ),
    ),
    "Haven_Main": _regions(
        "haven",
        (
            ("industrial_park", "Industrial Park", "산업 단지", 0.41, 0.22, 0.130),
            ("rail_yard", "Rail Yard", "철도 차량기지", 0.26, 0.43, 0.120),
            ("coal_yards", "Coal Yards", "석탄 야적장", 0.73, 0.50, 0.120),
            ("steel_mill", "Steel Mill", "제철소", 0.56, 0.53, 0.130),
            ("docks", "Docks", "부두", 0.16, 0.69, 0.110),
            ("residential", "Residential", "주거 지역", 0.65, 0.81, 0.140),
        ),
    ),
}


def resolve_map_region(map_name: str, x_cm: float, y_cm: float) -> MapRegionResolution:
    normalized_map_name = str(map_name or "").strip()
    map_name_ko = translate_code(normalized_map_name, "map") if normalized_map_name else "unknown"
    canonical_map_name = _CANONICAL_MAPS.get(normalized_map_name)
    source_asset = _SOURCE_ASSETS.get(canonical_map_name or "")
    source_url = f"{MAP_REGION_SOURCE_BASE_URL}/{source_asset}" if source_asset else None
    world_size_cm = MAP_WORLD_SIZE_CM.get(normalized_map_name)
    x_value = _finite_float_or_none(x_cm)
    y_value = _finite_float_or_none(y_cm)

    if canonical_map_name is None or world_size_cm is None:
        return _resolution(
            status="unsupported_map",
            map_name=normalized_map_name,
            map_name_ko=map_name_ko,
            canonical_map_name=canonical_map_name,
            source_asset=source_asset,
            source_url=source_url,
            source_sha256=_SOURCE_SHA256.get(source_asset or ""),
            x_cm=x_value,
            y_cm=y_value,
        )

    x_pct = x_value / world_size_cm if x_value is not None else None
    y_pct = y_value / world_size_cm if y_value is not None else None
    if (
        x_pct is None
        or y_pct is None
        or not 0.0 <= x_pct <= 1.0
        or not 0.0 <= y_pct <= 1.0
    ):
        return _resolution(
            status="invalid_coordinate",
            map_name=normalized_map_name,
            map_name_ko=map_name_ko,
            canonical_map_name=canonical_map_name,
            source_asset=source_asset,
            source_url=source_url,
            source_sha256=_SOURCE_SHA256.get(source_asset or ""),
            x_cm=x_value,
            y_cm=y_value,
            x_pct=x_pct,
            y_pct=y_pct,
        )

    if canonical_map_name in _DYNAMIC_MAPS:
        return _resolution(
            status="dynamic_map",
            map_name=normalized_map_name,
            map_name_ko=map_name_ko,
            canonical_map_name=canonical_map_name,
            source_asset=source_asset,
            source_url=source_url,
            source_sha256=_SOURCE_SHA256.get(source_asset or ""),
            x_cm=x_value,
            y_cm=y_value,
            x_pct=x_pct,
            y_pct=y_pct,
        )

    candidates: list[tuple[float, float, MapRegionDefinition]] = []
    for region in _REGIONS_BY_CANONICAL_MAP.get(canonical_map_name, ()):
        distance_pct = hypot(x_pct - region.center_x_pct, y_pct - region.center_y_pct)
        if distance_pct <= region.radius_pct:
            candidates.append((distance_pct / region.radius_pct, distance_pct, region))

    if not candidates:
        return _resolution(
            status="unmatched",
            map_name=normalized_map_name,
            map_name_ko=map_name_ko,
            canonical_map_name=canonical_map_name,
            source_asset=source_asset,
            source_url=source_url,
            source_sha256=_SOURCE_SHA256.get(source_asset or ""),
            x_cm=x_value,
            y_cm=y_value,
            x_pct=x_pct,
            y_pct=y_pct,
        )

    _, distance_pct, region = min(
        candidates,
        key=lambda candidate: (candidate[0], candidate[1], candidate[2].region_id),
    )
    return _resolution(
        status="matched",
        map_name=normalized_map_name,
        map_name_ko=map_name_ko,
        canonical_map_name=canonical_map_name,
        source_asset=source_asset,
        source_url=source_url,
        source_sha256=_SOURCE_SHA256.get(source_asset or ""),
        x_cm=x_value,
        y_cm=y_value,
        x_pct=x_pct,
        y_pct=y_pct,
        region=region,
        distance_to_center_m=distance_pct * world_size_cm / 100.0,
        radius_m=region.radius_pct * world_size_cm / 100.0,
    )


def map_region_catalog_record(map_name: str | None = None) -> dict[str, Any]:
    requested = str(map_name or "").strip()
    map_names = [requested] if requested else sorted(_CANONICAL_MAPS)
    records = []
    for api_map_name in map_names:
        canonical_map_name = _CANONICAL_MAPS.get(api_map_name)
        if canonical_map_name is None:
            records.append(
                {
                    "map_name": api_map_name,
                    "map_name_ko": translate_code(api_map_name, "map"),
                    "canonical_map_name": None,
                    "policy": "unsupported",
                    "world_size_cm": MAP_WORLD_SIZE_CM.get(api_map_name),
                    "source_asset": None,
                    "source_url": None,
                    "source_sha256": None,
                    "region_count": 0,
                    "regions": [],
                }
            )
            continue
        source_asset = _SOURCE_ASSETS[canonical_map_name]
        policy: MapRegionPolicy = "dynamic" if canonical_map_name in _DYNAMIC_MAPS else "static"
        regions = _REGIONS_BY_CANONICAL_MAP.get(canonical_map_name, ())
        records.append(
            {
                "map_name": api_map_name,
                "map_name_ko": translate_code(api_map_name, "map"),
                "canonical_map_name": canonical_map_name,
                "policy": policy,
                "world_size_cm": MAP_WORLD_SIZE_CM.get(api_map_name),
                "source_asset": source_asset,
                "source_url": f"{MAP_REGION_SOURCE_BASE_URL}/{source_asset}",
                "source_sha256": _SOURCE_SHA256[source_asset],
                "region_count": len(regions),
                "regions": [region.to_record() for region in regions],
            }
        )
    return {
        "catalog_version": MAP_REGION_CATALOG_VERSION,
        "source_repository": MAP_REGION_SOURCE_REPOSITORY,
        "source_commit": MAP_REGION_SOURCE_COMMIT,
        "coordinate_origin": "top-left",
        "coordinate_unit": "centimeter",
        "geometry_interpretation": "project-maintained circles around official map label centers",
        "maps": records,
    }


def _resolution(
    *,
    status: MapRegionStatus,
    map_name: str,
    map_name_ko: str,
    canonical_map_name: str | None,
    source_asset: str | None,
    source_url: str | None,
    source_sha256: str | None,
    x_cm: float | None,
    y_cm: float | None,
    x_pct: float | None = None,
    y_pct: float | None = None,
    region: MapRegionDefinition | None = None,
    distance_to_center_m: float | None = None,
    radius_m: float | None = None,
) -> MapRegionResolution:
    return MapRegionResolution(
        status=status,
        map_name=map_name,
        map_name_ko=map_name_ko,
        canonical_map_name=canonical_map_name,
        catalog_version=MAP_REGION_CATALOG_VERSION,
        source_commit=MAP_REGION_SOURCE_COMMIT,
        source_asset=source_asset,
        source_url=source_url,
        source_sha256=source_sha256,
        x_cm=x_cm,
        y_cm=y_cm,
        x_pct=x_pct,
        y_pct=y_pct,
        region_id=region.region_id if region else None,
        region_name=region.name if region else None,
        region_name_ko=region.name_ko if region else None,
        geometry_type=region.geometry_type if region else None,
        distance_to_center_m=distance_to_center_m,
        radius_m=radius_m,
    )


def _finite_float_or_none(value: Any) -> float | None:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return None
    return candidate if isfinite(candidate) else None
