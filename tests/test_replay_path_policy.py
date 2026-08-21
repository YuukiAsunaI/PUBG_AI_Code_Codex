from __future__ import annotations

import unittest

from pubg_ai.replay_path_policy import ReplayPathSampleState, select_replay_path_samples


class ReplayPathPolicyTests(unittest.TestCase):
    def test_trims_low_altitude_tutorial_waiting_area_and_initial_aircraft(self) -> None:
        samples = [
            ReplayPathSampleState(12, 128.72, False),
            ReplayPathSampleState(22, 232.24, False),
            ReplayPathSampleState(28, 40208.0, True),
            ReplayPathSampleState(32, 20494.5, False),
            ReplayPathSampleState(35, 6561.1, False),
        ]

        selected = select_replay_path_samples(
            samples,
            plane_start_event_index=23,
            plane_end_event_index=31,
            transport_aircraft_altitude_cm=100000.0,
            drop_start_altitude_cm=20000.0,
        )

        self.assertEqual([item.source_index for item in selected], [3, 4])
        self.assertFalse(selected[0].force_segment_break)

    def test_marks_path_after_respawn_aircraft_as_a_new_segment(self) -> None:
        samples = [
            ReplayPathSampleState(100, 1000.0, False),
            ReplayPathSampleState(200, 120000.0, True),
            ReplayPathSampleState(210, 80000.0, False),
        ]

        selected = select_replay_path_samples(
            samples,
            plane_start_event_index=None,
            plane_end_event_index=None,
            transport_aircraft_altitude_cm=100000.0,
            drop_start_altitude_cm=20000.0,
        )

        self.assertEqual([item.source_index for item in selected], [0, 2])
        self.assertTrue(selected[1].force_segment_break)

    def test_uses_first_airborne_sample_when_player_jumps_before_aircraft_sampling(self) -> None:
        samples = [
            ReplayPathSampleState(10, 100.0, False),
            ReplayPathSampleState(25, 75000.0, False),
            ReplayPathSampleState(30, 50000.0, False),
        ]

        selected = select_replay_path_samples(
            samples,
            plane_start_event_index=20,
            plane_end_event_index=80,
            transport_aircraft_altitude_cm=100000.0,
            drop_start_altitude_cm=20000.0,
        )

        self.assertEqual([item.source_index for item in selected], [1, 2])


if __name__ == "__main__":
    unittest.main()
