"""扫描装置及记录塔的新截图定位、消失确认与后续阶段回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from module.os.event_recovery import MapEventRecovery, PENDING_EVENT_PATH


class TestMissingScanningEvent(unittest.TestCase):
    def handler(self):
        h = SimpleNamespace(
            config=SimpleNamespace(cross_set=Mock()),
            focus_to=Mock(),
            map_init=Mock(),
            map=SimpleNamespace(shape=(7, 12)),
            camera=(0, 0),
            map_swipe=Mock(),
            device=SimpleNamespace(screenshot=Mock()),
            update=Mock(),
            is_in_map=Mock(return_value=True),
            _event_local_grid=Mock(return_value=SimpleNamespace()),
            _pending_event_visible=Mock(return_value=False),
        )
        state = dict(kind="is_scanning_device", phase="interact", target=[2, 8])
        return h, state

    def test_missing_scanner_cancels_tracking(self):
        h, state = self.handler()
        self.assertTrue(MapEventRecovery._cancel_missing_scanning_event(h, state))
        self.assertEqual(h.device.screenshot.call_count, 2)
        h.focus_to.assert_not_called()
        h.config.cross_set.assert_called_once_with(PENDING_EVENT_PATH, None)

    def test_visible_scanner_or_radar_question_keeps_tracking(self):
        h, state = self.handler()
        h._pending_event_visible.return_value = True
        self.assertFalse(MapEventRecovery._cancel_missing_scanning_event(h, state))
        h.config.cross_set.assert_not_called()

    def test_unseen_or_fleet_occluded_grid_keeps_tracking(self):
        for grid in (
            None,
            SimpleNamespace(is_current_fleet=True),
            SimpleNamespace(is_fleet=True),
        ):
            h, state = self.handler()
            h._event_local_grid.return_value = grid
            self.assertFalse(MapEventRecovery._cancel_missing_scanning_event(h, state))
            h.config.cross_set.assert_not_called()

    def test_detection_failure_keeps_tracking(self):
        from module.exception import MapDetectionError

        h, state = self.handler()
        h.update.side_effect = MapDetectionError("bad frame")
        self.assertFalse(MapEventRecovery._cancel_missing_scanning_event(h, state))
        h.config.cross_set.assert_not_called()

    def test_other_events_and_followup_are_unchanged(self):
        for changes in ({"kind": "is_akashi"}, {"phase": "followup"}, {"target": None}):
            h, state = self.handler()
            state.update(changes)
            self.assertFalse(MapEventRecovery._cancel_missing_scanning_event(h, state))
            h.focus_to.assert_not_called()
            h.config.cross_set.assert_not_called()

    def test_second_frame_detects_target_keeps_tracking(self):
        h, state = self.handler()
        h._pending_event_visible.side_effect = [False, True]
        self.assertFalse(MapEventRecovery._cancel_missing_scanning_event(h, state))
        h.config.cross_set.assert_not_called()

    def test_outside_map_coordinate_is_cancelled_without_swiping(self):
        h, state = self.handler()
        state["target"] = [5, 13]
        self.assertTrue(MapEventRecovery._cancel_missing_scanning_event(h, state))
        h.map_swipe.assert_not_called()
        h.config.cross_set.assert_called_once_with(PENDING_EVENT_PATH, None)

    def test_unseen_target_has_bounded_camera_relocation(self):
        h, state = self.handler()
        h._event_local_grid.return_value = None
        self.assertFalse(MapEventRecovery._cancel_missing_scanning_event(h, state))
        self.assertEqual(h.map_swipe.call_count, 2)
        h.config.cross_set.assert_not_called()


class TestFreshScannerClick(unittest.TestCase):
    def handler(self, visible):
        state = dict(kind="is_scanning_device", target=[1, 7])
        return SimpleNamespace(
            update=Mock(),
            is_in_map=Mock(return_value=True),
            appear=Mock(return_value=False),
            fleet_selector=SimpleNamespace(bar_opened=lambda: False),
            view=SimpleNamespace(select=Mock(return_value=visible)),
            _get_pending_event=Mock(return_value=state),
            _event_global_location=lambda grid: grid.global_location,
            _event_local_grid=Mock(
                return_value=SimpleNamespace(button=(300, 400, 360, 460))
            ),
            _event_safe_click_button=Mock(return_value="fresh-button"),
            config=SimpleNamespace(cross_set=Mock()),
            camera=(0, 0),
            map_swipe=Mock(),
        )

    def test_fresh_detection_replaces_wrong_record_before_click(self):
        grid = SimpleNamespace(global_location=(1, 2), button=(550, 170, 610, 225))
        h = self.handler([grid])
        self.assertEqual(
            MapEventRecovery._prepare_event_click(h, (1, 7)), "fresh-button"
        )
        h._event_safe_click_button.assert_called_once_with(grid)
        self.assertEqual(h.config.cross_set.call_args.args[1]["target"], [1, 2])
        h._event_local_grid.assert_not_called()

    def test_rescan_accepts_unique_current_tower_after_origin_changed(self):
        grid = SimpleNamespace(
            global_location=(2, 5), button=(380, 420, 450, 475), is_logging_tower=True
        )
        h = self.handler([grid])
        h._get_pending_event.return_value.update(kind="is_logging_tower", target=[2, 6])
        self.assertTrue(
            MapEventRecovery._event_matches_pending(h, "is_logging_tower", grid)
        )
        self.assertEqual(h.config.cross_set.call_args.args[1]["target"], [2, 5])

    def test_rescan_keeps_ambiguous_towers_unmatched(self):
        grid = SimpleNamespace(global_location=(2, 5), is_logging_tower=True)
        h = self.handler([grid, SimpleNamespace(global_location=(3, 5))])
        h._get_pending_event.return_value.update(kind="is_logging_tower", target=[2, 6])
        self.assertFalse(
            MapEventRecovery._event_matches_pending(h, "is_logging_tower", grid)
        )
        h.config.cross_set.assert_not_called()

    def test_logging_tower_one_tile_above_old_target_uses_fresh_grid(self):
        grid = SimpleNamespace(global_location=(2, 5), button=(380, 420, 450, 475))
        h = self.handler([grid])
        h._get_pending_event.return_value.update(kind="is_logging_tower", target=[2, 6])
        self.assertEqual(
            MapEventRecovery._prepare_event_click(h, (2, 6)), "fresh-button"
        )
        h.view.select.assert_called_with(is_logging_tower=True)
        h._event_safe_click_button.assert_called_once_with(grid)
        h._event_local_grid.assert_not_called()
        self.assertEqual(h.config.cross_set.call_args.args[1]["target"], [2, 5])

    def test_logging_tower_missing_or_ambiguous_never_clicks_old_lower_tile(self):
        for visible in (
            [],
            [
                SimpleNamespace(global_location=(1, 1)),
                SimpleNamespace(global_location=(2, 2)),
            ],
        ):
            h = self.handler(visible)
            h._get_pending_event.return_value["kind"] = "is_logging_tower"
            self.assertIsNone(MapEventRecovery._prepare_event_click(h, (1, 7)))
            h._event_safe_click_button.assert_not_called()
            h._event_local_grid.assert_not_called()

    def test_missing_device_never_clicks_old_empty_tile(self):
        h = self.handler([])
        self.assertIsNone(MapEventRecovery._prepare_event_click(h, (1, 7)))
        h._event_safe_click_button.assert_not_called()
        self.assertEqual(h.map_swipe.call_count, 2)

    def test_ambiguous_detections_do_not_retarget_to_another_device(self):
        h = self.handler(
            [
                SimpleNamespace(global_location=(2, 2)),
                SimpleNamespace(global_location=(3, 3)),
            ]
        )
        self.assertIsNone(MapEventRecovery._prepare_event_click(h, (1, 7)))
        h._event_safe_click_button.assert_not_called()


class TestResourceFollowupPhase(unittest.TestCase):
    def handler(self, phase="interact"):
        state = dict(kind="is_scanning_device", target=[1, 7], phase=phase)
        if phase == "followup":
            state.update(mode="resource", remaining=1)
        h = SimpleNamespace(
            _get_pending_event=Mock(return_value=state),
            config=SimpleNamespace(cross_set=Mock()),
            _event_recovery_active=False,
            _event_recovery_attempted=False,
            is_in_map=Mock(return_value=True),
            _complete_siren_event=Mock(return_value=True),
            _goto_pending_event_with_other_fleets=Mock(return_value=False),
            _execute_fixed_patrol_scan=Mock(),
        )
        return h, state

    @patch("module.os.event_recovery.record_siren_research_device")
    def test_resource_option_immediately_records_followup(self, stats):
        h, state = self.handler()
        self.assertTrue(MapEventRecovery._record_siren_followup(h, "resource"))
        self.assertEqual(state["phase"], "followup")
        self.assertEqual(state["mode"], "resource")
        self.assertEqual(state["remaining"], 1)
        h.config.cross_set.assert_called_once()

    def test_followup_goes_directly_to_auto_search_without_relocating(self):
        h, state = self.handler("followup")
        self.assertTrue(MapEventRecovery._recover_pending_event(h))
        h._complete_siren_event.assert_called_once_with(state, drop=None)
        h._goto_pending_event_with_other_fleets.assert_not_called()
        h._execute_fixed_patrol_scan.assert_not_called()

    def test_option_chosen_during_fleet_recovery_stops_recovery(self):
        h, state = self.handler()

        def fleet_recovery(**kwargs):
            state.update(phase="followup", mode="resource", remaining=1)
            return False

        h._goto_pending_event_with_other_fleets.side_effect = fleet_recovery
        self.assertTrue(MapEventRecovery._recover_pending_event(h))
        h._complete_siren_event.assert_called_once_with(state, drop=None)
        h._execute_fixed_patrol_scan.assert_not_called()

    def test_resource_pickup_story_does_not_reset_remaining_progress(self):
        h, state = self.handler("followup")
        state["remaining"] = 0
        self.assertFalse(MapEventRecovery._record_siren_followup(h, "collected"))
        self.assertEqual(state["mode"], "resource")
        self.assertEqual(state["remaining"], 0)
        h.config.cross_set.assert_not_called()
