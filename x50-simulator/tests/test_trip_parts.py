import gzip
import json
import unittest
import hashlib
from unittest.mock import patch
from pathlib import Path
from test_archive_import import ArchiveImportTest, START, archive
from journal_trajectory import journal_inertial_points


def part(index, *, end=False, conflicting=False):
    rows=[json.loads(line) for line in gzip.decompress(archive(start=START+index*4000)).splitlines()]
    identity=f"20261007-12000{index}-0123abcd"
    rows[0]['data'].update(archive_id=identity,part_index=index,trip_started_ms=START,
                          previous_archive_id=f"20261007-12000{index-1}-0123abcd" if index else "")
    if conflicting: rows[0]['data']['trip_started_ms']+=1
    rows[-1]['data'].update(continuation=not end,reason='manual_stop_by_user' if end else 'part_rotation')
    return gzip.compress(('\n'.join(json.dumps(r) for r in rows)+'\n').encode())


class TripPartsTest(ArchiveImportTest):
    def test_join_reverse_order_restart_and_idempotence(self):
        second,status=self.engine.import_trip_archive(part(1,end=True))
        self.assertEqual(status,200,second)
        self.assertFalse(self.engine.trip_store.detail(second['trip_id'])[0]['summary']['archive_complete'])
        first,status=self.engine.import_trip_archive(part(0))
        self.assertEqual(status,200,first)
        self.assertEqual(first['trip_id'],second['trip_id'])
        detail,_=self.engine.trip_detail(first['trip_id'])
        self.assertEqual(len(detail['samples']),8)
        self.assertTrue(detail['summary']['archive_complete'])
        self.assertEqual(len(detail['summary']['archive_parts']),2)
        repeated,status=self.engine.import_trip_archive(part(0))
        self.assertEqual(status,200,repeated)
        self.assertTrue(repeated['already_imported'])
        detail,_=self.engine.trip_detail(first['trip_id'])
        self.assertEqual(len(detail['samples']),8)
        self.assertEqual(len(self.engine.trip_store.list()['trips']),1)

    def test_ha_sync_joins_parts_and_skips_unchanged_archives(self):
        import server
        blobs={f'20261007-12000{i}-0123abcd':part(i,end=i==1) for i in range(2)}
        listing={'journals':[dict(id=key,sha256=hashlib.sha256(value).hexdigest()) for key,value in blobs.items()]}
        self.engine.journal_points_cache={}
        with patch.object(server,'ha_request',return_value=(listing,200)), \
             patch.object(server,'ha_journal_download',side_effect=lambda key,*args:blobs[key]):
            self.engine._sync_ha_journals('unused','unused')
        trips=self.engine.trip_store.list()['trips']
        self.assertEqual(len(trips),1)
        self.assertTrue(trips[0]['archive_complete'])
        self.assertEqual(len(trips[0]['archive_parts']),2)
        with patch.object(server,'ha_request',return_value=(listing,200)), \
             patch.object(self.engine,'import_trip_archive',side_effect=AssertionError('unchanged part reimported')):
            self.engine._sync_ha_journals('unused','unused')

    def test_conflicting_part_preserves_existing_trip(self):
        result,status=self.engine.import_trip_archive(part(0))
        self.assertEqual(status,200)
        before=self.engine.trip_store.detail(result['trip_id'])[0]
        rejected,status=self.engine.import_trip_archive(part(1,end=True,conflicting=True))
        self.assertEqual(status,400,rejected)
        self.assertEqual(self.engine.trip_store.detail(result['trip_id'])[0],before)

    def test_column_deltas_keep_previous_poses_and_update_old_point(self):
        path=Path(self.temp.name)/'columns.gz'
        rows=[dict(type='inertial_fusion_revision',time_ms=START+3000,elapsed_ms=3000,
            data=dict(segment_id=1,point_columns=['elapsed_ms','lat','lon'],points=[[1000,1,2],[2000,1.001,2]],point_encoding='checkpoint_columns_v1')),
              dict(type='inertial_fusion_revision',time_ms=START+4000,elapsed_ms=4000,
            data=dict(segment_id=1,point_columns=['elapsed_ms','lat','lon'],points=[[1000,1.0001,2]],point_encoding='upsert_columns_v1'))]
        path.write_bytes(gzip.compress(('\n'.join(json.dumps(r) for r in rows)+'\n').encode()))
        points=journal_inertial_points(path)
        self.assertEqual(len(points),2)
        self.assertEqual(points[0]['lat'],1.0001)
        self.assertEqual(points[1]['lat'],1.001)

if __name__=='__main__':unittest.main()
