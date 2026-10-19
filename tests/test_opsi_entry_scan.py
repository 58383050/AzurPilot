"""进入海域后的自律、续查与补扫顺序回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tests.test_opsi_task_context as task_context_tests
from module.os.event_recovery import MapEventRecovery
from module.os.map import OSMap
from module.exception import GameStuckError, MapDetectionError


class TestCurrentSeaEntryScan(unittest.TestCase):
    def runner(self):
        r = task_context_tests.TestOpsiTaskContext().make_runner("OpsiScheduling")
        r.zone = SimpleNamespace(zone_id=44, is_port=False)
        r.is_in_special_zone = Mock(return_value=False)
        r._get_pending_event = Mock(return_value=None)
        r.map_rescan = Mock(return_value=True)
        return r

    @patch("module.os.event_recovery.reset_resource_caches")
    def test_entry_scans_without_checkpoint_and_keeps_current_task(self, reset):
        r = self.runner()

        def scan(**kwargs):
            self.assertTrue(r._entry_event_scan_active)
            self.assertTrue(r.config._disable_task_switch)
            self.assertEqual(r.config.task.command, "OpsiScheduling")
            self.assertEqual(kwargs, {"rescan_mode": "full"})
            return True

        r.map_rescan.side_effect = scan
        self.assertTrue(MapEventRecovery._scan_current_sea_on_entry(r))
        self.assertFalse(r._entry_event_scan_active)
        self.assertEqual(r.config.task.command, "OpsiScheduling")
        self.assertFalse(r.config.modified)

    def test_failed_scan_blocks_subsequent_work(self):
        for outcome in (False, MapDetectionError("bad frame")):
            r = self.runner()
            if isinstance(outcome, Exception):
                r.map_rescan.side_effect = outcome
            else:
                r.map_rescan.return_value = outcome
            with self.assertRaises((GameStuckError, MapDetectionError)):
                MapEventRecovery._scan_current_sea_on_entry(r)
            self.assertFalse(r._entry_event_scan_active)

    def test_unfinished_pending_event_blocks_new_work(self):
        r = self.runner()
        r._get_pending_event.return_value = {"kind": "is_scanning_device"}
        with self.assertRaises(GameStuckError):
            MapEventRecovery._scan_current_sea_on_entry(r)

    def test_ports_and_special_zones_keep_their_own_flow(self):
        for port, special in ((True, False), (False, True)):
            r = self.runner()
            r.zone.is_port = port
            r.is_in_special_zone.return_value = special
            self.assertFalse(MapEventRecovery._scan_current_sea_on_entry(r))
            r.map_rescan.assert_not_called()

    def test_full_scan_does_not_stop_after_first_event(self):
        r = SimpleNamespace(
            zone=SimpleNamespace(is_port=False),
            _solved_fleet_mechanism=False,
            config=SimpleNamespace(OpsiFleet_Fleet=1),
            fleet_set=Mock(),
            is_in_task_explore=False,
            _entry_event_scan_active=True,
            _solved_map_event={"is_akashi"},
            map_rescan_once=Mock(side_effect=[True, False]),
        )
        self.assertTrue(OSMap.map_rescan(r))
        self.assertEqual(r.map_rescan_once.call_count, 2)

    def test_bad_map_frame_is_not_completed_scan(self):
        r = SimpleNamespace(
            _entry_event_scan_active=True,
            _opsi_meowfficer_cleanup=False,
            map_data_init=Mock(),
            handle_info_bar=Mock(),
            update=Mock(side_effect=MapDetectionError()),
        )
        with self.assertRaises(MapDetectionError):
            OSMap.map_rescan_once(r)

    def test_entry_runs_auto_search_before_event_recovery_and_scan(self):
        r = self.runner()
        order = []
        r.run_auto_search = Mock(side_effect=lambda **kwargs: order.append("auto"))
        r._resume_pending_event = Mock(side_effect=lambda: order.append("pending"))
        r._scan_current_sea_on_entry = Mock(side_effect=lambda: order.append("scan"))
        MapEventRecovery._prepare_current_sea_entry(r)
        self.assertEqual(order, ["auto", "pending", "scan"])
        r.run_auto_search.assert_called_once_with(
            question=False, rescan=False, after_auto_search=False
        )
        self.assertTrue(r._entry_initial_auto_search_done)

    def test_entry_resumes_followup_without_extra_initial_search(self):
        r = self.runner()
        order = []
        r._get_pending_event.return_value = {
            "kind": "is_scanning_device",
            "phase": "followup",
            "mode": "resource",
            "remaining": 1,
        }
        r.run_auto_search = Mock()
        r._resume_pending_event = Mock(
            side_effect=lambda: order.append("followup_auto")
        )
        r._scan_current_sea_on_entry = Mock(side_effect=lambda: order.append("scan"))
        MapEventRecovery._prepare_current_sea_entry(r)
        self.assertEqual(order, ["followup_auto", "scan"])
        r.run_auto_search.assert_not_called()
