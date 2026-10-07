import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from test_archive_import import archive
import server
from reference_trajectory import create_reference, ReferenceStore


class ReferenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.engine = server.SimulationEngine.__new__(server.SimulationEngine)
        self.engine.trip_store = server.TripLogRegistry(root/'trips')
        self.engine.trajectory_store = server.TrajectoryStore(root/'traces')
        self.engine.journal_dir = root/'journals'
        self.engine.journal_dir.mkdir()
        result, status = self.engine.import_trip_archive(archive())
        self.assertEqual(status,200)
        self.trip_id=result['trip_id']

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_save_restart_and_evidence_immutable(self):
        original,_ = self.engine.trip_detail(self.trip_id)
        ref,status = self.engine.trip_reference(self.trip_id)
        self.assertEqual(status,200)
        store = ReferenceStore(self.engine.trip_store.stores['head_unit'].root)
        self.assertIsNone(store.load(self.trip_id))
        edit=copy.deepcopy(ref)
        edit['nodes'][1]['lat']+=.001
        edit['nodes'][1]['t_ms']=1
        edit['nodes'][1]['evidence']={}
        saved,status=self.engine.trip_reference(self.trip_id,edit)
        self.assertEqual(status,200)
        self.assertEqual(saved['revision'],1)
        self.assertEqual(saved['nodes'][1]['t_ms'],ref['nodes'][1]['t_ms'])
        self.assertEqual(saved['nodes'][1]['evidence'],ref['nodes'][1]['evidence'])
        self.assertEqual(store.load(self.trip_id),saved)
        self.assertEqual(self.engine.trip_detail(self.trip_id)[0],original)
        self.assertEqual(self.engine.trip_reference(self.trip_id,edit)[1],409)

    def test_invalid_payload_and_traversal(self):
        ref,_=self.engine.trip_reference(self.trip_id)
        bad=copy.deepcopy(ref);bad['nodes'][0]['lon']=float('nan')
        self.assertEqual(self.engine.trip_reference(self.trip_id,bad)[1],400)
        self.assertEqual(self.engine.trip_reference(self.trip_id,[])[1],400)
        self.assertEqual(self.engine.trip_reference('../secret')[1],400)

    def test_flags_persist_without_changing_original_evidence(self):
        ref=create_reference({'summary':{'id':'flags'},'trajectories':[{'inertial':{'points':[
            {'t_ms':1000+i*1000,'lat':1+i*.0001,'lon':2} for i in range(4)]}}]})
        store=ReferenceStore(Path(self.temp.name))
        edit=copy.deepcopy(ref)
        edit['nodes'][0]['locked']=True
        edit['nodes'][-1]['excluded']=True
        edit['nodes'][1]['cut_before']=True
        saved=store.save(ref,edit)
        loaded=store.load('flags')
        self.assertEqual(loaded,saved)
        self.assertTrue(loaded['nodes'][0]['locked'])
        self.assertTrue(loaded['nodes'][-1]['excluded'])
        self.assertTrue(loaded['nodes'][1]['cut_before'])
        self.assertEqual(loaded['source_sha256'],ref['source_sha256'])
        for before,after in zip(ref['nodes'],loaded['nodes']):
            for key in ('id','t_ms','evidence','rest_m','break_before','original_lat','original_lon'):
                self.assertEqual(before[key],after[key])
        # Older coordinate-only clients preserve the flags.
        patch=dict(saved,nodes=[{key:n[key] for key in ('id','lat','lon')} for n in saved['nodes']])
        again=store.save(ref,patch)
        self.assertTrue(again['nodes'][0]['locked'])

    def test_flags_validation_and_saved_only_does_not_generate_draft(self):
        self.assertEqual(self.engine.trip_reference(self.trip_id,saved_only=True),({'saved':False,'nodes':[]},200))
        ref,_=self.engine.trip_reference(self.trip_id)
        for flag in ('locked','excluded','cut_before'):
            bad=copy.deepcopy(ref);bad['nodes'][0][flag]='yes'
            self.assertEqual(self.engine.trip_reference(self.trip_id,bad)[1],400)
        bad=copy.deepcopy(ref)
        for n in bad['nodes']:n['excluded']=True
        self.assertEqual(self.engine.trip_reference(self.trip_id,bad)[1],400)
        bad=copy.deepcopy(ref);bad['nodes'][0].update(locked=True,excluded=True)
        self.assertEqual(self.engine.trip_reference(self.trip_id,bad)[1],400)

    def test_jump_is_not_physical_length(self):
        trip={'summary':{'id':'test'},'samples':[], 'trajectories':[{'inertial':{'points':[
            {'t_ms':1000,'lat':1,'lon':2,'segment_id':0},
            {'t_ms':2000,'lat':1.0001,'lon':2,'segment_id':0},
            {'t_ms':3000,'lat':1.1,'lon':2,'segment_id':0},
            {'t_ms':4000,'lat':1.1001,'lon':2,'segment_id':1}]}}]}
        nodes=create_reference(trip)['nodes']
        self.assertFalse(nodes[1]['break_before'])
        for n in nodes[2:]:
            self.assertTrue(n['break_before']);self.assertEqual(n['rest_m'],0)

    def test_actual_handler_round_trip(self):
        engine=self.engine
        class Handler(server.Handler):
            def log_message(self,*_): pass
        Handler.engine=engine
        http=server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{http.server_port}/api/controller/trips/{self.trip_id}/reference'
        try:
            with urlopen(url+'?saved_only=1') as response:
                self.assertEqual(json.load(response),{'saved':False,'nodes':[]})
            with urlopen(url) as response:ref=json.load(response)
            with urlopen(Request(url,data=json.dumps(ref).encode(),headers={'Content-Type':'application/json'})) as response:
                saved=json.load(response)
            self.assertEqual(saved['revision'],1)
            with urlopen(url+'?saved_only=1') as response:
                self.assertEqual(json.load(response),saved)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url,data=json.dumps(ref).encode()))
            self.assertEqual(error.exception.code,409)
            error.exception.close()
        finally:
            http.shutdown();http.server_close();thread.join()


if __name__=='__main__':unittest.main()
