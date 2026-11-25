import time
import json
import random
import csv
import os
from RoverBlockchain import Block

# ---------------- CONFIG ----------------
ELECTION_TIMEOUT_RANGE = (3.0, 5.0)  # Slightly faster elections
HEARTBEAT_INTERVAL = 0.5




class RAFTConsensus:
    """
    FINAL controller-compatible RAFT implementation.
    Fixes:
    - Leader Crash Detection (Removes 'leader_id is None' check).
    - Vote Stealing (Checks 'to' field).
    - Blockchain Replication.
    """

    def __init__(
        self,
        blockchain,
        robot_name,
        emitter,
        receiver,
        peer_names,
        priority,
        time_fn=None,
        on_discovery_request=None
    ):
        # Core wiring
        self.blockchain = blockchain
        self.robot_name = robot_name
        self.emitter = emitter
        self.receiver = receiver

        # Priority is a static election bias. Time source is injected separately.
        self.priority = priority() if callable(priority) else priority
        self._time_fn = time_fn if callable(time_fn) else time.time
        self.on_discovery_request = on_discovery_request

        # Cluster info
        self.peers = [p for p in peer_names if p != robot_name]
        self.cluster_size = len(peer_names)
        self.majority = self.cluster_size // 2 + 1

        # ---------- Persistent RAFT state ----------
        self.current_term = 0
        self.voted_for = None

        # ---------- Volatile RAFT state ----------
        self.state = "follower"   # follower | candidate | leader
        self.leader_id = None

        self.last_heartbeat = self._time_fn()
        self.election_deadline = self._new_deadline()
        self.votes_received = set()

        self._last_debug = 0

        # ---- Election Metrics Logging ----
        self.metrics_file = "election_metrics.csv"

        if not os.path.exists(self.metrics_file):
            with open(self.metrics_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "event",
                    "time",
                    "term",
                    "rover"
                ])

    # ------------------------------------------------
    # Compatibility shim
    # ------------------------------------------------
    def tick(self):
        self.step()

    # ------------------------------------------------
    # Timing
    # ------------------------------------------------
    def _new_deadline(self):
        base = random.uniform(*ELECTION_TIMEOUT_RANGE)
        # Higher priority → slightly faster election
        bias = max(0.0, (100 - int(self.priority))) * 0.01
        return self._time_fn() + base + bias

    # ------------------------------------------------
    # Messaging
    # ------------------------------------------------
    def _send(self, msg):
        self.emitter.send(json.dumps(msg))

    def _receive(self):
        while self.receiver.getQueueLength() > 0:
            try:
                msg_str = self.receiver.getString()
                msg = json.loads(msg_str)
            except Exception as e:
                self.receiver.nextPacket()
                continue
                
            self.receiver.nextPacket()

            term = msg.get("term", 0)

            # If we see a higher term, immediately step down
            if term > self.current_term:
                self.current_term = term
                self.state = "follower"
                self.voted_for = None
                self.leader_id = None
                self.election_deadline = self._new_deadline() # Reset timer

            mtype = msg.get("type")

            if mtype == "heartbeat":
                self._on_heartbeat(msg)
            elif mtype == "request_vote":
                self._on_request_vote(msg)
            elif mtype == "vote":
                self._on_vote(msg)
            elif mtype == "new_block":
                self._on_new_block(msg)
            elif mtype == "discovery_request":
                self._on_discovery_request(msg)
            elif mtype == "sync_request":
                self._on_sync_request(msg)
            elif mtype == "full_chain":
                self._on_full_chain(msg)

    # ------------------------------------------------
    # Message handlers
    # ------------------------------------------------
    def _on_heartbeat(self, msg):
        if msg["term"] < self.current_term:
            return

        # Valid heartbeat received -> Reset deadline
        self.state = "follower"
        self.leader_id = msg["leader"]
        self.last_heartbeat = self._time_fn()
        self.election_deadline = self._new_deadline()

    def _on_request_vote(self, msg):
        grant = False
        if (
            msg["term"] >= self.current_term
            and (self.voted_for is None or self.voted_for == msg["candidate"])
        ):
            grant = True
            self.voted_for = msg["candidate"]
            self.current_term = msg["term"]
            self.election_deadline = self._new_deadline()

        self._send({
            "type": "vote",
            "term": self.current_term,
            "from": self.robot_name,
            "to": msg["candidate"], # Targeted vote
            "granted": grant
        })

    def _on_vote(self, msg):
        if self.state != "candidate":
            return
        if msg["term"] != self.current_term:
            return

        # Ignore votes intended for other candidates
        if msg.get("to") != self.robot_name:
            return

        if msg["granted"]:
            self.votes_received.add(msg["from"])
            if len(self.votes_received) >= self.majority:
                self._become_leader()

    def _on_new_block(self, msg):
        try:
            block_data = msg["block"]
            block = Block.from_dict(block_data)

            if self.blockchain.add_block(block):
                print(f"[{self.robot_name}] 🔗 Replicated block #{block.index} from {msg.get('sender', 'unknown')}")
                self.blockchain.save_to_file(f"blockchain_{self.robot_name}.json")
                print(f"[{self.robot_name}] 💾 Saved replicated blockchain to blockchain_{self.robot_name}.json")
                self.election_deadline = self._new_deadline()
            else:
                print(f"[{self.robot_name}] ⚠️ Out of sync while applying block #{block.index}. Requesting full chain from leader.")
                if self.leader_id:
                    self._send({
                        "type": "sync_request",
                        "from": self.robot_name,
                        "to": self.leader_id,
                        "term": self.current_term
                    })
        except Exception as e:
            print(f"[{self.robot_name}] ⚠️ Error processing new block: {e}")

    def _on_discovery_request(self, msg):
        if self.state != "leader":
            return
        if msg.get("to") != self.robot_name:
            return
        payload = msg.get("payload")
        sender = msg.get("from", "unknown")
        if payload is None:
            return

        if callable(self.on_discovery_request):
            self.on_discovery_request(payload, sender)

    def _on_sync_request(self, msg):
        if self.state != "leader":
            return
        if msg.get("to") != self.robot_name:
            return

        requester = msg.get("from")
        if not requester:
            return

        print(f"[{self.robot_name}] 📤 Sending full chain to {requester}")
        self._send({
            "type": "full_chain",
            "from": self.robot_name,
            "to": requester,
            "term": self.current_term,
            "chain": self.blockchain.to_list()
        })

    def _on_full_chain(self, msg):
        if msg.get("to") != self.robot_name:
            return

        chain_data = msg.get("chain")
        if not chain_data:
            return

        try:
            new_chain = [Block.from_dict(b) for b in chain_data]

            for i, block in enumerate(new_chain):
                if block.hash != block.compute_hash():
                    raise ValueError("Received chain has invalid block hash")
                if i > 0 and block.prev_hash != new_chain[i - 1].hash:
                    raise ValueError("Received chain has broken prev_hash links")

            self.blockchain.chain = new_chain
            self.blockchain.save_to_file(f"blockchain_{self.robot_name}.json")
            print(f"[{self.robot_name}] 🔄 Chain synchronized with leader {msg.get('from', 'unknown')}")
            self.election_deadline = self._new_deadline()
        except Exception as e:
            print(f"[{self.robot_name}] ⚠️ Sync failed: {e}")

    # ------------------------------------------------
    # State transitions
    # ------------------------------------------------
    def _start_election(self):
        self.state = "candidate"
        self.current_term += 1
        self.voted_for = self.robot_name
        self.votes_received = {self.robot_name}
        self.leader_id = None # Clear old leader
        self.election_deadline = self._new_deadline()

        print(f"[{self.robot_name}] ⚠️ Leader heartbeat timeout. Initiating election (Term {self.current_term})")

        with open(self.metrics_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ELECTION_STARTED",
                round(self._time_fn(), 3),
                self.current_term,
                self.robot_name
            ])

        self._send({
            "type": "request_vote",
            "term": self.current_term,
            "candidate": self.robot_name
        })

    def _become_leader(self):
        self.state = "leader"
        self.leader_id = self.robot_name
        self.last_heartbeat = self._time_fn()
        print(f"🔵 LEADER ELECTED: {self.robot_name} | term={self.current_term}")

        with open(self.metrics_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "LEADER_ELECTED",
                round(self._time_fn(), 3),
                self.current_term,
                self.robot_name
            ])
        
        # Send immediate heartbeat to establish authority
        self._send({
            "type": "heartbeat",
            "term": self.current_term,
            "leader": self.robot_name
        })

    # ------------------------------------------------
    # Public API for Controller
    # ------------------------------------------------
    def propose_block(self, block):
        if self.state != "leader":
            print(f"[{self.robot_name}] ⚠️ Rejecting block proposal because this rover is not the leader.")
            return False

        # Stamp the proposal with the active RAFT term before hashing/replication.
        block.term = self.current_term
        block.hash = block.compute_hash()

        if self.blockchain.add_block(block):
            print(f"[{self.robot_name}] 💾 Block #{block.index} generated and appended to local ledger.")
            self.blockchain.save_to_file(f"blockchain_{self.robot_name}.json")
            print(f"[{self.robot_name}] 💾 Saved leader blockchain to blockchain_{self.robot_name}.json")
        else:
            print(f"[{self.robot_name}] ⚠️ Local append failed for block #{block.index}.")
            return False

        msg = {
            "type": "new_block",
            "term": self.current_term,
            "sender": self.robot_name,
            "block": block.to_dict()
        }
        self._send(msg)
        return True

    def step(self):
        self._receive()

        if self.state == "leader":
            if self._time_fn() - self.last_heartbeat > HEARTBEAT_INTERVAL:
                self.last_heartbeat = self._time_fn()
                self._send({
                    "type": "heartbeat",
                    "term": self.current_term,
                    "leader": self.robot_name
                })
        else:
            # --- CRITICAL FIX ---
            # If deadline passed, start election regardless of whether we *thought* we had a leader.
            if self._time_fn() > self.election_deadline:
                # print(f"[{self.robot_name}] Timeout! {self._time_fn()} > {self.election_deadline}")
                self._start_election()

        self._debug()

    # ------------------------------------------------
    # Debug
    # ------------------------------------------------
    def _debug(self):
        if self._time_fn() - self._last_debug > 2.0:
            self._last_debug = self._time_fn()