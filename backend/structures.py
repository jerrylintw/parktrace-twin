"""Data structures intentionally implemented for the course presentation."""
from collections import deque


class VisitNode:
    def __init__(self, plate, spot):
        self.plate = plate
        self.spot = spot
        self.prev = None
        self.next = None


class ActiveDoublyLinkedList:
    """A doubly linked list of parked cars, ordered by entry time."""
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0

    def append(self, plate, spot):
        node = VisitNode(plate, spot)
        if self.tail:
            node.prev = self.tail
            self.tail.next = node
        else:
            self.head = node
        self.tail = node
        self.size += 1

    def remove(self, plate):
        current = self.head
        while current:
            if current.plate == plate:
                if current.prev:
                    current.prev.next = current.next
                else:
                    self.head = current.next
                if current.next:
                    current.next.prev = current.prev
                else:
                    self.tail = current.prev
                self.size -= 1
                return True
            current = current.next
        return False

    def nodes(self):
        result = []
        current = self.head
        while current:
            result.append({
                'plate': current.plate,
                'spot': current.spot,
                'prev': current.prev.plate if current.prev else None,
                'next': current.next.plate if current.next else None,
            })
            current = current.next
        return result


def fifo_queue(wait_items):
    """Queue.enqueue/dequeue: O(1) at both ends, using deque."""
    queue = deque((item.plate for item in wait_items))
    return list(queue)


def lifo_stack(events, limit=8):
    """Stack.push/pop: most recent event appears first; history is not deleted."""
    stack = []
    for event in events[-limit:]:
        stack.append({'action': event.action, 'plate': event.plate, 'spot': event.spot_id})
    ordered = []
    while stack:
        ordered.append(stack.pop())
    return ordered


def parking_grid(spots, assignments, observations):
    """2-D array A[row][column]: random access via row and column."""
    result = []
    for zone in ('A', 'B', 'C'):
        row = []
        for i in range(1, 7):
            spot_id = f'{zone}{i:02}'
            spot = spots[spot_id]
            assigned = assignments.get(spot_id)
            seen = observations.get(spot_id)
            row.append({
                'id': spot_id, 'zone': zone, 'kind': spot.kind,
                'distance': spot.distance, 'plate': assigned,
                'observed': seen, 'conflict': bool(seen and seen != assigned),
            })
        result.append(row)
    return result
