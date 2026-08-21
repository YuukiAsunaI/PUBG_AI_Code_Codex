from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ReplayPathSampleState:
    event_index: int
    z_cm: float | None
    is_in_vehicle: bool


@dataclass(frozen=True)
class ReplayPathSampleSelection:
    source_index: int
    force_segment_break: bool


def select_replay_path_samples(
    samples: Sequence[ReplayPathSampleState],
    *,
    plane_start_event_index: int | None,
    plane_end_event_index: int | None,
    transport_aircraft_altitude_cm: float,
    drop_start_altitude_cm: float,
) -> list[ReplayPathSampleSelection]:
    """Remove pre-flight/aircraft points while preserving post-respawn path breaks."""

    if not samples:
        return []

    def inside_initial_plane_window(sample: ReplayPathSampleState) -> bool:
        return (
            plane_start_event_index is not None
            and plane_end_event_index is not None
            and plane_start_event_index <= sample.event_index <= plane_end_event_index
        )

    def is_transport_aircraft(sample: ReplayPathSampleState) -> bool:
        return sample.is_in_vehicle and (
            inside_initial_plane_window(sample)
            or (sample.z_cm is not None and sample.z_cm >= transport_aircraft_altitude_cm)
        )

    initial_aircraft_indices = [
        index
        for index, sample in enumerate(samples)
        if sample.is_in_vehicle and inside_initial_plane_window(sample)
    ]
    start_index = 0
    if initial_aircraft_indices:
        start_index = initial_aircraft_indices[-1] + 1
    elif plane_start_event_index is not None:
        first_airborne = next(
            (
                index
                for index, sample in enumerate(samples)
                if sample.event_index >= plane_start_event_index
                and not is_transport_aircraft(sample)
                and sample.z_cm is not None
                and sample.z_cm >= drop_start_altitude_cm
            ),
            None,
        )
        if first_airborne is not None:
            start_index = first_airborne
        elif plane_end_event_index is not None:
            first_after_flight = next(
                (
                    index
                    for index, sample in enumerate(samples)
                    if sample.event_index > plane_end_event_index
                    and not is_transport_aircraft(sample)
                ),
                None,
            )
            if first_after_flight is not None:
                start_index = first_after_flight

    selected: list[ReplayPathSampleSelection] = []
    skipped_transport = False
    for index in range(start_index, len(samples)):
        sample = samples[index]
        if is_transport_aircraft(sample):
            skipped_transport = True
            continue
        selected.append(
            ReplayPathSampleSelection(
                source_index=index,
                force_segment_break=skipped_transport and bool(selected),
            )
        )
        skipped_transport = False
    return selected
