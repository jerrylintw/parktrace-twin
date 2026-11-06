"""Run with: python -m unittest discover -s tests -v"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from backend.engine import ParkingEngine, ParkingError, calculate_fee
from backend.structures import ActiveDoublyLinkedList


class FeeTest(unittest.TestCase):
    def test_fee_boundaries(self):
        start = datetime(2026, 11, 6, tzinfo=timezone.utc)
        check = lambda minutes: calculate_fee(start, start + timedelta(minutes=minutes))
        self.assertEqual(check(0), 0)
        self.assertEqual(check(10), 0)
        self.assertEqual(check(11), 20)
        self.assertEqual(check(40), 20)
        self.assertEqual(check(41), 40)
        self.assertEqual(check(1440), 240)
        self.assertEqual(check(1440 + 11), 260)
        self.assertEqual(check(2 * 1440), 480)


class StructureTest(unittest.TestCase):
    def test_doubly_linked_remove_head_middle_tail(self):
        chain = ActiveDoublyLinkedList()
        for x in 'ABCD':
            chain.append(x, x)
        self.assertTrue(chain.remove('A'))
        self.assertTrue(chain.remove('C'))
        self.assertTrue(chain.remove('D'))
        self.assertEqual(chain.nodes(), [{'plate':'B','spot':'B','prev':None,'next':None}])
        self.assertFalse(chain.remove('X'))
        self.assertTrue(chain.remove('B'))
        self.assertEqual(chain.nodes(), [])


class ParkingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.url = 'sqlite:///' + os.path.join(self.tmp.name, 'db.sqlite')
        self.engine = ParkingEngine(self.url)

    def tearDown(self):
        self.engine.Session.kw["bind"].dispose()
        self.tmp.cleanup()

    def test_entry_exit_and_receipt(self):
        entered = self.engine.entry('abc-1234')
        self.assertEqual(entered['status'], 'parked')
        self.assertNotEqual(entered['spot'], 'A01')  # reserved accessible
        with self.assertRaises(ParkingError):
            self.engine.entry('ABC-1234')
        self.engine.advance(60)
        left = self.engine.exit('ABC-1234')
        self.assertEqual(left['fee'], 40)
        self.assertEqual(self.engine.state()['metrics']['occupied'], 0)
        self.assertEqual(self.engine.state()['metrics']['revenue'], 40)
        self.assertEqual(self.engine.state()['receipts'][0]['plate'], 'ABC-1234')

    def test_special_spots(self):
        self.assertEqual(self.engine.entry('EV-1234', 'ev')['spot'], 'C01')
        self.assertEqual(self.engine.entry('AA-1234', 'accessible')['spot'], 'A01')

    def test_fifo_waitlist_promotion(self):
        for i in range(14):
            result = self.engine.entry(f'XY-{i:04d}')
            self.assertEqual(result['status'], 'parked')
        self.assertEqual(self.engine.entry('ZZ-9000')['status'], 'queued')
        self.assertEqual(self.engine.entry('ZZ-9001')['status'], 'queued')
        self.assertEqual(self.engine.state()['structures']['queue'], ['ZZ-9000','ZZ-9001'])
        exit1 = self.engine.exit('XY-0000')
        self.assertEqual(exit1['promoted']['plate'], 'ZZ-9000')
        self.assertEqual(self.engine.state()['waiting'][0]['plate'], 'ZZ-9001')
        self.engine.exit('XY-0001')
        self.assertEqual(self.engine.state()['metrics']['waiting'], 0)
        self.assertEqual(self.engine.state()['metrics']['occupied'], 14)

    def test_conflict_and_event_replay(self):
        self.engine.entry('AB-1001')
        original = self.engine.state()
        spot = original['active'][0]['spot']
        self.engine.observe(spot, 'XX-2002')
        state = self.engine.state()
        self.assertEqual(state['metrics']['alerts'], 1)
        self.assertEqual(state['events'][0]['snapshot']['grid'][0][0]['id'], 'A01')
        entry_event = next(e for e in state['events'] if e['action'] == 'ENTRY')
        self.assertFalse(any(s['conflict'] for row in entry_event['snapshot']['grid'] for s in row))
        self.engine.observe(spot, '')
        self.assertEqual(self.engine.state()['metrics']['alerts'], 0)

    def test_reset_and_bad_inputs(self):
        with self.assertRaises(ParkingError):
            self.engine.entry('bad input!')
        self.engine.entry('AA-1234')
        self.engine.reset()
        s = self.engine.state()
        self.assertEqual(s['metrics']['occupied'], 0)
        self.assertEqual(s['events'][0]['action'], 'RESET')

    def test_restart_persistence(self):
        self.engine.entry('AB-4444')
        # Simulate server restart by creating a new engine to same SQLite DB.
        second = ParkingEngine(self.url)
        self.assertEqual(second.state()['active'][0]['plate'], 'AB-4444')
        second.Session.kw["bind"].dispose()


if __name__ == '__main__':
    unittest.main()
