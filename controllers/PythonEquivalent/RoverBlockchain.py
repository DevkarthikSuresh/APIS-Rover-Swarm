import hashlib
import json
import time
from typing import Dict, Any


class Block:
    """
    RAFT-aware blockchain block.
    Includes proposer + term for auditability and correctness.
    """

    def __init__(
        self,
        index: int,
        timestamp: float,
        data: Dict[str, Any],
        prev_hash: str,
        proposer: str,
        term: int
    ):
        self.index = index
        self.timestamp = timestamp
        self.data = data
        self.prev_hash = prev_hash
        self.proposer = proposer
        self.term = term

        self.hash = self.compute_hash()

    def compute_hash(self) -> str:
        # Sort keys ensures consistent hashing regardless of dictionary order
        payload = json.dumps({
            "index": self.index,
            "timestamp": self.timestamp,
            "data": self.data,
            "prev_hash": self.prev_hash,
            "proposer": self.proposer,
            "term": self.term
        }, sort_keys=True).encode("utf-8")

        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "data": self.data,
            "prev_hash": self.prev_hash,
            "proposer": self.proposer,
            "term": self.term,
            "hash": self.hash
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]):
        b = Block(
            d["index"],
            d["timestamp"],
            d["data"],
            d["prev_hash"],
            d.get("proposer", "unknown"),
            d.get("term", -1)
        )
        b.hash = d.get("hash", b.hash)
        return b


class Blockchain:
    def __init__(self):
        self.chain = []
        self._create_genesis()

    def _create_genesis(self):
        # FIX: Timestamp must be identical for all robots!
        # If we use time.time(), every robot has a different genesis hash.
        genesis = Block(
            index=0,
            timestamp=0.0,  # <--- FIXED: Hardcoded time
            data={"genesis": True},
            prev_hash="0",
            proposer="genesis",
            term=0
        )
        self.chain.append(genesis)

    def add_block(self, block: Block) -> bool:
        last = self.chain[-1]

        if block.prev_hash != last.hash:
            # Added debug info to see WHO is rejecting WHAT
            # print(f"❌ Block rejected: prev_hash mismatch. Got {block.prev_hash[:6]}..., expected {last.hash[:6]}...")
            return False

        if block.hash != block.compute_hash():
            print("❌ Block rejected: invalid hash")
            return False

        self.chain.append(block)
        return True

    def make_new_block(self, data: Dict[str, Any], proposer: str, term: int, timestamp: float = None) -> Block:
        last = self.chain[-1]
        return Block(
            index=last.index + 1,
            timestamp=time.time() if timestamp is None else timestamp,
            data=data,
            prev_hash=last.hash,
            proposer=proposer,
            term=term
        )

    def is_valid_chain(self) -> bool:
        if not self.chain:
            return False
        for i, block in enumerate(self.chain):
            if block.hash != block.compute_hash():
                return False
            if i == 0:
                continue
            if block.prev_hash != self.chain[i - 1].hash:
                return False
        return True

    def get_chain_summary(self) -> Dict[str, Any]:
        latest = self.get_latest_block()
        return {
            "length": len(self.chain),
            "latest_index": latest.index,
            "latest_hash": latest.hash,
            "valid": self.is_valid_chain()
        }

    def get_latest_block(self) -> Block:
        return self.chain[-1]

    def __len__(self):
        return len(self.chain)

    def to_list(self):
        return [b.to_dict() for b in self.chain]

    def save_to_file(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_list(), f, indent=2)

    def load_from_file(self, filepath: str) -> bool:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            chain = [Block.from_dict(d) for d in data]
            if not chain:
                return False

            # Verify integrity
            for i in range(len(chain)):
                if chain[i].hash != chain[i].compute_hash():
                    return False
                if i > 0 and chain[i].prev_hash != chain[i - 1].hash:
                    return False

            self.chain = chain
            return True
        except Exception:
            return False