"""LEVEL column tags: session rows (CRON, SPAWN, LEAD) and workers (SUB, WORKER) from collect._co_level_tags."""
import unittest

import collect
from model import LEVEL_TAGS, Snapshot, Stage, Worker


def stage(**fields: object) -> Stage:
    return Stage(path='/stage', label='stage', **fields)


def worker(linkage: str = 'unknown') -> Worker:
    return Worker(name='w', state='RUNNING', linkage=linkage)


class StageLevelTags(unittest.TestCase):
    def tag_of(self, row: Stage) -> str:
        collect._co_level_tags([row])
        return row.level_tag

    def test_cron_cluster_is_cron(self) -> None:
        self.assertEqual(self.tag_of(stage(cluster='cron')), 'CRON')

    def test_cron_wins_over_spawn(self) -> None:
        self.assertEqual(self.tag_of(stage(cluster='cron', parent='s1', launch_depth=2)), 'CRON')

    def test_parent_session_is_spawn(self) -> None:
        self.assertEqual(self.tag_of(stage(parent='s1')), 'SPAWN')

    def test_launch_depth_is_spawn(self) -> None:
        self.assertEqual(self.tag_of(stage(launch_depth=1)), 'SPAWN')

    def test_top_level_is_lead(self) -> None:
        self.assertEqual(self.tag_of(stage()), 'LEAD')

    def test_other_cluster_and_zero_depth_is_lead(self) -> None:
        self.assertEqual(self.tag_of(stage(cluster='group', launch_depth=0)), 'LEAD')

    def test_odd_launch_depth_is_lead(self) -> None:
        self.assertEqual(self.tag_of(stage(launch_depth=None)), 'LEAD')

    def test_every_tag_is_a_level_tag(self) -> None:
        rows = [stage(cluster='cron'), stage(parent='s1'), stage(), stage(launch_depth=3)]
        collect._co_level_tags(rows)
        self.assertTrue({row.level_tag for row in rows} <= set(LEVEL_TAGS))


class WorkerLevelTags(unittest.TestCase):
    def tag_of(self, linkage: str) -> str:
        row = stage()
        row.snap = Snapshot(workers=[worker(linkage)])
        collect._co_level_tags([row])
        return row.snap.workers[0].level_tag

    def test_native_linkage_is_sub(self) -> None:
        self.assertEqual(self.tag_of('native'), 'SUB')

    def test_other_linkages_are_worker(self) -> None:
        for linkage in ('unknown', 'process', 'run-record', 'env', 'conflict', ''):
            with self.subTest(linkage=linkage):
                self.assertEqual(self.tag_of(linkage), 'WORKER')

    def test_each_worker_of_a_row_is_tagged(self) -> None:
        row = stage(parent='s1')
        row.snap = Snapshot(workers=[worker('native'), worker('env'), worker('native')])
        collect._co_level_tags([row])
        self.assertEqual([w.level_tag for w in row.snap.workers], ['SUB', 'WORKER', 'SUB'])
        self.assertEqual(row.level_tag, 'SPAWN')

    def test_empty_row_has_no_workers_to_tag(self) -> None:
        row = stage()
        collect._co_level_tags([row])
        self.assertEqual((row.level_tag, row.snap.workers), ('LEAD', []))


class NeverRaises(unittest.TestCase):
    def test_missing_snapshot_keeps_row_tag(self) -> None:
        row = stage(parent='s1')
        row.snap = None
        collect._co_level_tags([row])
        self.assertEqual(row.level_tag, 'SPAWN')

    def test_no_stages_is_a_no_op(self) -> None:
        self.assertIsNone(collect._co_level_tags([]))


if __name__ == '__main__':
    unittest.main()
