"""Parking core: deterministic business rules and durable event snapshots."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from threading import RLock

from sqlalchemy import delete, func, select
from .models import Event, Observation, Setting, Spot, Visit, WaitItem, make_session_factory
from .structures import ActiveDoublyLinkedList, fifo_queue, lifo_stack, parking_grid

PLATE_RE = re.compile(r'^[A-Z0-9]{2,4}-[A-Z0-9]{3,4}$')
VALID_KINDS = ('normal', 'ev', 'accessible')

class ParkingError(ValueError):
    pass


def normalize_plate(value):
    plate = re.sub(r'\s+', '', str(value or '').upper()).replace('－', '-').replace('—', '-')
    if not PLATE_RE.fullmatch(plate):
        raise ParkingError('車牌格式不正確，例：ABC-1234 或 AB-1234')
    return plate


def calculate_fee(entered_at, exited_at):
    """10-minute grace; TWD20 per started 30min thereafter; TWD240 per 24h cap."""
    seconds = max(0, (exited_at - entered_at).total_seconds())
    minutes = math.ceil(seconds / 60)
    days, remaining = divmod(minutes, 1440)
    if remaining <= 10:
        partial = 0
    else:
        partial = min(240, math.ceil((remaining - 10) / 30) * 20)
    return days * 240 + partial


class ParkingEngine:
    def __init__(self, database_url=None):
        self.Session = make_session_factory(database_url)
        self.lock = RLock()
        with self.Session.begin() as db:
            if db.scalar(select(func.count()).select_from(Spot)) == 0:
                for zone in ('A', 'B', 'C'):
                    for i in range(1, 7):
                        kind = 'accessible' if zone == 'A' and i == 1 else ('ev' if zone == 'C' and i <= 3 else 'normal')
                        db.add(Spot(id=f'{zone}{i:02}', zone=zone, kind=kind,
                                    distance={'A': 10, 'B': 25, 'C': 40}[zone] + i * 3))

    def _now(self, db):
        record = db.get(Setting, 'offset_minutes')
        offset = int(record.value) if record else 0
        return datetime.now(timezone.utc) + timedelta(minutes=offset)

    def _spots(self, db):
        return {spot.id: spot for spot in db.scalars(select(Spot)).all()}

    def _active(self, db):
        return db.scalars(select(Visit).where(Visit.exited_at.is_(None)).order_by(Visit.id)).all()

    def _waiting(self, db):
        return db.scalars(select(WaitItem).order_by(WaitItem.id)).all()

    def _observed(self, db):
        return {o.spot_id: o.plate for o in db.scalars(select(Observation)).all()}

    def _choose_spot(self, db, vehicle_type):
        active_spots = {v.spot_id for v in self._active(db)}
        free = [s for s in self._spots(db).values() if s.id not in active_spots]
        if vehicle_type == 'normal':
            free = [s for s in free if s.kind == 'normal']
        else:
            preferred = [s for s in free if s.kind == vehicle_type]
            free = preferred or [s for s in free if s.kind == 'normal']
        return min(free, key=lambda s: (s.distance, s.id)) if free else None

    def _snapshot(self, db):
        active = self._active(db)
        assignments = {v.spot_id: v.plate for v in active}
        grid = parking_grid(self._spots(db), assignments, self._observed(db))
        waiting = self._waiting(db)
        return {'at': self._now(db).isoformat(), 'grid': grid,
                'waiting': fifo_queue(waiting), 'occupied': len(active)}

    def _log(self, db, action, plate=None, spot=None, note=''):
        event = Event(action=action, plate=plate, spot_id=spot, note=note,
                      created_at=self._now(db).isoformat(),
                      snapshot_json=json.dumps(self._snapshot(db), ensure_ascii=False))
        db.add(event)

    def _promote(self, db):
        first = db.scalars(select(WaitItem).order_by(WaitItem.id).limit(1)).first()
        if first is None:
            return None
        spot = self._choose_spot(db, first.vehicle_type)
        if not spot:
            return None
        now = self._now(db).isoformat()
        db.add(Visit(plate=first.plate, vehicle_type=first.vehicle_type,
                     spot_id=spot.id, entered_at=now))
        db.delete(first)
        db.flush()
        self._log(db, 'PROMOTE', first.plate, spot.id, '候補 FIFO 自動補位；此時才開始計費')
        return {'plate': first.plate, 'spot': spot.id}

    def entry(self, plate, vehicle_type='normal'):
        plate = normalize_plate(plate)
        if vehicle_type not in VALID_KINDS:
            raise ParkingError('車型必須為一般、電動或無障礙')
        with self.lock, self.Session.begin() as db:
            if db.scalar(select(Visit.id).where(Visit.plate == plate, Visit.exited_at.is_(None)).limit(1)) is not None:
                raise ParkingError('這輛車已在停車場內，不可重複進場')
            if db.scalar(select(WaitItem.id).where(WaitItem.plate == plate).limit(1)) is not None:
                raise ParkingError('這輛車已在候補佇列中')
            waiting = self._waiting(db)
            spot = None if waiting else self._choose_spot(db, vehicle_type)
            if spot is None:
                db.add(WaitItem(plate=plate, vehicle_type=vehicle_type, queued_at=self._now(db).isoformat()))
                db.flush()
                self._log(db, 'QUEUE', plate, note='車位額滿或已有候補；FIFO 排隊')
                return {'status': 'queued', 'plate': plate}
            db.add(Visit(plate=plate, vehicle_type=vehicle_type,
                         spot_id=spot.id, entered_at=self._now(db).isoformat()))
            db.flush()
            self._log(db, 'ENTRY', plate, spot.id, '自動選擇可用車位')
            return {'status': 'parked', 'plate': plate, 'spot': spot.id}

    def exit(self, plate):
        plate = normalize_plate(plate)
        with self.lock, self.Session.begin() as db:
            visit = db.scalars(select(Visit).where(Visit.plate == plate, Visit.exited_at.is_(None)).limit(1)).first()
            if not visit:
                raise ParkingError('找不到此車牌的有效停車紀錄（候補車不可直接出場）')
            now = self._now(db)
            fee = calculate_fee(datetime.fromisoformat(visit.entered_at), now)
            visit.exited_at = now.isoformat()
            visit.fee = fee
            # A car that left is no longer observed at any old stall.
            for observation in db.scalars(select(Observation).where(Observation.plate == plate)).all():
                db.delete(observation)
            db.flush()
            self._log(db, 'EXIT', plate, visit.spot_id, f'應付 NT$ {fee}')
            promoted = self._promote(db)
            return {'status': 'exited', 'plate': plate, 'spot': visit.spot_id,
                    'fee': fee, 'entered_at': visit.entered_at, 'exited_at': visit.exited_at,
                    'promoted': promoted}

    def cancel_wait(self, plate):
        plate = normalize_plate(plate)
        with self.lock, self.Session.begin() as db:
            item = db.scalars(select(WaitItem).where(WaitItem.plate == plate).limit(1)).first()
            if not item:
                raise ParkingError('候補清單中找不到這輛車')
            db.delete(item)
            db.flush()
            self._log(db, 'CANCEL', plate, note='取消候補')
            self._promote(db)
            return {'status': 'cancelled', 'plate': plate}

    def observe(self, spot_id, plate):
        spot_id = str(spot_id or '').upper().strip()
        if plate:
            plate = normalize_plate(plate)
        with self.lock, self.Session.begin() as db:
            if db.get(Spot, spot_id) is None:
                raise ParkingError('車位不存在')
            record = db.get(Observation, spot_id)
            if record:
                db.delete(record)
                db.flush()
            if plate:
                db.add(Observation(spot_id=spot_id, plate=plate, seen_at=self._now(db).isoformat()))
            db.flush()
            self._log(db, 'OBSERVE', plate or None, spot_id,
                      '模擬攝影機觀測' if plate else '清除觀測結果')
            return {'status': 'observed', 'spot': spot_id, 'plate': plate}

    def advance(self, minutes):
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ParkingError('請輸入正確的分鐘數')
        if minutes not in (15, 30, 60, 180, 1440):
            raise ParkingError('只能快轉 15、30、60、180 或 1440 分鐘')
        with self.lock, self.Session.begin() as db:
            record = db.get(Setting, 'offset_minutes')
            if record is None:
                record = Setting(key='offset_minutes', value='0')
                db.add(record)
            record.value = str(int(record.value) + minutes)
            db.flush()
            self._log(db, 'CLOCK', note=f'模擬時間快轉 {minutes} 分鐘')
            return {'minutes_advanced': minutes, 'now': self._now(db).isoformat()}

    def reset(self):
        with self.lock, self.Session.begin() as db:
            for table in (Observation, WaitItem, Visit, Event, Setting):
                db.execute(delete(table))
            db.flush()
            self._log(db, 'RESET', note='清空資料並將模擬時間重設為現在')
            return {'status': 'reset'}

    def state(self):
        with self.lock, self.Session() as db:
            active = self._active(db)
            waiting = self._waiting(db)
            spots = self._spots(db)
            observed = self._observed(db)
            assignments = {v.spot_id: v.plate for v in active}
            grid = parking_grid(spots, assignments, observed)
            all_events = db.scalars(select(Event).order_by(Event.id.desc()).limit(35)).all()
            all_events.reverse()
            linked = ActiveDoublyLinkedList()
            for visit in active:
                linked.append(visit.plate, visit.spot_id)
            issues = [{'spot': s['id'], 'assigned': s['plate'], 'observed': s['observed']}
                      for row in grid for s in row if s['conflict']]
            receipts = db.scalars(select(Visit).where(Visit.exited_at.is_not(None)).order_by(Visit.id.desc()).limit(12)).all()
            total = db.scalar(select(func.coalesce(func.sum(Visit.fee), 0))) or 0
            return {
                'now': self._now(db).isoformat(), 'grid': grid,
                'metrics': {'total': 18, 'occupied': len(active), 'free': 18-len(active),
                            'waiting': len(waiting), 'revenue': int(total), 'alerts': len(issues)},
                'active': [{'plate': v.plate, 'spot': v.spot_id, 'type': v.vehicle_type,
                            'entered_at': v.entered_at, 'current_fee': calculate_fee(
                                datetime.fromisoformat(v.entered_at), self._now(db))} for v in active],
                'waiting': [{'plate': v.plate, 'type': v.vehicle_type, 'queued_at': v.queued_at} for v in waiting],
                'alerts': issues,
                'structures': {'array': [[s['id'] for s in row] for row in grid],
                               'queue': fifo_queue(waiting), 'stack': lifo_stack(all_events),
                               'linked': linked.nodes()},
                'events': [{'id': e.id, 'at': e.created_at, 'action': e.action,
                            'plate': e.plate, 'spot': e.spot_id, 'note': e.note,
                            'snapshot': json.loads(e.snapshot_json)} for e in reversed(all_events)],
                'receipts': [{'plate': v.plate, 'spot': v.spot_id, 'fee': v.fee,
                              'entered_at': v.entered_at, 'exited_at': v.exited_at} for v in receipts],
                'rules': {'grace_minutes': 10, 'unit_minutes': 30, 'unit_twd': 20,
                          'daily_cap_twd': 240, 'note': '超過免費10分鐘後，每開始30分鐘20元；每24小時最多240元'}
            }
