"""Immutable trip evidence plus explicitly saved, manually edited coordinates."""
import bisect
import hashlib
import json
import math
import re
import threading
import time


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def distance(a, b):
    return math.hypot((b['lat']-a['lat'])*111132,
                      (b['lon']-a['lon'])*111320*math.cos(math.radians((a['lat']+b['lat'])/2)))


def position(sample, prefix):
    lat, lon = sample.get(prefix+'_lat'), sample.get(prefix+'_lon')
    if number(lat) and number(lon) and abs(lat) <= 90 and abs(lon) <= 180:
        return {'lat': lat, 'lon': lon}


def create_reference(trip):
    samples = sorted(trip.get('samples', []), key=lambda p: p['time_ms'])
    times = [p['time_ms'] for p in samples]
    # Prefer the complete journal; duplicate native overlays must not interleave.
    traces = [t for t in trip.get('trajectories', [])+trip.get('trajectory_event_overlays', [])
              if t.get('inertial', {}).get('points')]
    if not traces:
        raise ValueError('В поездке нет инерциальных узлов. Добавьте архив Navigation.')
    trace = max(traces, key=lambda t: len(t['inertial']['points']))
    nodes, previous = [], None
    for source in sorted(trace['inertial']['points'], key=lambda p: p.get('t_ms', 0)):
        stamp, lat, lon = source.get('t_ms'), source.get('lat'), source.get('lon')
        if not all(number(v) for v in (stamp, lat, lon)) or abs(lat)>90 or abs(lon)>180:
            continue
        if previous and stamp <= previous['t_ms']:
            continue
        index = bisect.bisect_left(times, stamp)
        candidates = samples[max(0,index-1):index+1]
        sample = min(candidates, key=lambda p: abs(p['time_ms']-stamp)) if candidates else {}
        if abs(sample.get('time_ms', stamp)-stamp)>2500:
            sample = {}
        step = sample.get('inertial_step') or {}
        compass = sample.get('compass') or {}
        node = dict(id=len(nodes), t_ms=stamp, lat=lat, lon=lon,
                    original_lat=lat, original_lon=lon, segment_id=source.get('segment_id', 0),
                    evidence={'sample_time_ms': sample.get('time_ms'), 'step': step, 'compass': compass,
                              'speed_kmh': sample.get('vehicle_speed_kmh'),
                              'odometer_km': sample.get('odometer_km'),
                              'gps': position(sample,'carlinkit'), 'fake': position(sample,'fake'),
                              'gps_accuracy_m': sample.get('carlinkit_accuracy_m'),
                              'gps_age_ms': sample.get('real_gps_age_ms',sample.get('carlinkit_fix_age_ms')),
                              'gps_good': sample.get('gps_good',sample.get('real_gps_quality_good'))})
        length, origin = 0, 'fragment_start'
        broken = previous is None or previous['segment_id'] != node['segment_id']
        if previous:
            geometric = distance(previous,node)
            dt = (stamp-previous['t_ms'])/1000
            length, origin = geometric, 'original_geometry'
            lo, hi = bisect.bisect_right(times, previous['t_ms']), bisect.bisect_right(times, stamp)
            # Integrate the recorded vehicle speed, clipping endpoints. Coarse
            # 100 m dashboard odometer ticks cannot constrain individual links.
            span = samples[max(0,lo-1):hi+1]
            total, covered = 0, 0
            for a,b in zip(span,span[1:]):
                seconds = max(0,(min(stamp,b['time_ms'])-max(previous['t_ms'],a['time_ms']))/1000)
                speed = a.get('vehicle_speed_kmh')
                if number(speed) and 0<=speed<=250 and b['time_ms']-a['time_ms']<=2500:
                    total += speed/3.6*seconds
                    covered += seconds
            if covered >= dt*.95:
                length, origin = total, 'vehicle_speed_integral'
            broken = broken or dt>15 or geometric>max(30,length*3+15) or geometric>dt*70+30
        node.update(break_before=broken, rest_m=0 if broken else length, length_source=origin,
                    original_edge_m=0 if previous is None else distance(previous,node))
        nodes.append(node)
        previous = node
    if len(nodes)<2:
        raise ValueError('Недостаточно инерциальных узлов для редактора')
    digest = hashlib.sha256(b'[')
    for i, node in enumerate(nodes):
        if i:
            digest.update(b',')
        digest.update(json.dumps(node,sort_keys=True,separators=(',',':')).encode())
    digest.update(b']')
    evidence_hash = digest.hexdigest()
    return dict(schema='x50.reference-trajectory.v1', trip_id=trip['summary']['id'],
                source_trajectory_id=trace.get('trajectory_id'), source_sha256=evidence_hash,
                revision=0, saved=False, nodes=nodes)


class ReferenceStore:
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()

    def path(self, trip_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', trip_id):
            raise ValueError('Недопустимый идентификатор поездки')
        return self.root / (trip_id+'.reference.json')

    def load(self, trip_id):
        path = self.path(trip_id)
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def save(self, base, patch):
        with self.lock:
            if not isinstance(patch,dict):
                raise ValueError('Ожидается JSON-объект')
            current = self.load(base['trip_id']) or base
            if patch.get('revision') != current['revision'] or patch.get('source_sha256') != current['source_sha256']:
                raise FileExistsError('Референс изменился. Скачайте свои правки и заново откройте редактор.')
            edits = patch.get('nodes')
            if not isinstance(edits,list) or len(edits)!=len(current['nodes']):
                raise ValueError('Нельзя менять число узлов и исходные измерения')
            updated = dict(current, nodes=[])
            for original, edit in zip(current['nodes'],edits):
                if not isinstance(edit,dict) or edit.get('id')!=original['id']:
                    raise ValueError('Порядок узлов изменён')
                lat,lon = edit.get('lat'),edit.get('lon')
                if not number(lat) or not number(lon) or abs(lat)>89 or abs(lon)>180:
                    raise ValueError('Некорректные координаты')
                flags = {key: edit.get(key, original.get(key, False))
                         for key in ('locked', 'excluded', 'cut_before')}
                if any(type(value) is not bool for value in flags.values()):
                    raise ValueError('Замок, исключение и разрыв должны быть true/false')
                if flags['locked'] and flags['excluded']:
                    raise ValueError('Снимите замки перед удалением диапазона')
                # Original virtual breaks are immutable evidence boundaries.
                updated['nodes'].append(dict(original,lat=lat,lon=lon,**flags))
            if sum(not n['excluded'] for n in updated['nodes']) < 2:
                raise ValueError('Оставьте не менее двух узлов референса')
            updated.update(revision=current['revision']+1,saved=True,updated_ms=int(time.time()*1000))
            path = self.path(base['trip_id'])
            temporary = path.with_suffix('.json.tmp')
            with temporary.open('w',encoding='utf-8') as stream:
                json.dump(updated,stream,ensure_ascii=False,separators=(',',':'),allow_nan=False)
            temporary.replace(path)
            return updated
