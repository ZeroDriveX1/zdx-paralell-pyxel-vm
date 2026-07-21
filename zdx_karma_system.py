"""
ZDX Karma System: Node Reputation and Cluster Leadership.

Implements a self-governing network where:
- Every node has karma (reputation) and experience level
- Trustworthy behavior earns positive karma and experience
- Attack attempts, security violations, and protocol breaches reduce karma
- Low karma eventually results in suspension and eviction
- High-level nodes have more "gravitational mass" for cluster elections
- Master node (leader of 100-node cluster) is elected by highest mass
- Karma is persistent, affecting node status across sessions

Karma categories:
- POSITIVE: valid frame contributions, correct computation, uptime
- NEGATIVE: failed authentication, replay attacks, revocation attempts, DoS attempts
- NEUTRAL: routine heartbeats, standard operations

Levels: Trustee (0-100 XP) → Contributor (100-1000) → Validator (1000-10000)
        → Sentinel (10000-100000) → Master (100000+)

Master nodes eligible for cluster leadership receive voting weight proportional
to their total mass (level * experience multiplier).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from enum import Enum
from typing import Optional, Dict, Tuple, List
from collections import defaultdict


class KarmaEventType(Enum):
    """Types of events that affect karma."""
    # Positive events
    VALID_CONTRIBUTION = ("valid_contribution", 10, 15)  # (name, karma, xp)
    CORRECT_COMPUTATION = ("correct_computation", 5, 10)
    UPTIME_HEARTBEAT = ("uptime_heartbeat", 1, 1)
    PEER_VALIDATION = ("peer_validation", 3, 5)
    FRAME_SYNC_SUCCESS = ("frame_sync_success", 2, 3)

    # Negative events
    AUTH_FAILURE = ("auth_failure", -5, 0)
    REPLAY_ATTACK_ATTEMPT = ("replay_attack", -20, -10)
    SIGNATURE_FORGERY = ("signature_forgery", -50, -25)
    DOS_ATTEMPT = ("dos_attempt", -30, -15)
    REVOCATION_VIOLATION = ("revocation_violation", -100, 0)  # Leads to eviction
    MALFORMED_PACKET = ("malformed_packet", -3, 0)
    TIMEOUT_VIOLATION = ("timeout_violation", -2, 0)
    ENROLLMENT_ABUSE = ("enrollment_abuse", -25, -10)
    COMPUTATION_FRAUD = ("computation_fraud", -40, -20)
    STATE_CORRUPTION = ("state_corruption", -60, -30)

    # Neutral events
    HEARTBEAT = ("heartbeat", 0, 0)
    ROUTINE_OPERATION = ("routine_operation", 0, 0)

    def karma_delta(self) -> int:
        return self.value[1]

    def xp_delta(self) -> int:
        return self.value[2]


class NodeLevel(Enum):
    """Node experience levels and their properties."""
    TRUSTEE = {"min_xp": 0, "max_xp": 100, "rank": 1, "mass_multiplier": 1.0}
    CONTRIBUTOR = {"min_xp": 100, "max_xp": 1000, "rank": 2, "mass_multiplier": 1.5}
    VALIDATOR = {"min_xp": 1000, "max_xp": 10000, "rank": 3, "mass_multiplier": 2.5}
    SENTINEL = {"min_xp": 10000, "max_xp": 100000, "rank": 4, "mass_multiplier": 4.0}
    MASTER = {"min_xp": 100000, "max_xp": float("inf"), "rank": 5, "mass_multiplier": 6.0}

    def is_master(self) -> bool:
        return self == NodeLevel.MASTER


@dataclass
class KarmaScore:
    """Current karma and experience state of a node."""
    node_id: str
    karma: int = 50  # Starting neutral (0-100 scale)
    total_experience: int = 0
    level: str = "TRUSTEE"
    last_event_time: float = 0.0
    suspension_status: bool = False
    suspension_time: Optional[float] = None
    eviction_pending: bool = False
    consecutive_violations: int = 0

    @property
    def is_suspended(self) -> bool:
        """Check if node is currently suspended."""
        if not self.suspension_status:
            return False
        if self.suspension_time is None:
            return True
        # Suspension lasts 24 hours, then auto-lifts
        elapsed = time.time() - self.suspension_time
        if elapsed > 86400:  # 24 hours
            self.suspension_status = False
            self.consecutive_violations = 0
            return False
        return True

    @property
    def mass(self) -> float:
        """Gravitational mass for cluster election (level * XP * karma factor)."""
        if self.suspension_status or self.is_suspended:
            return 0.0

        level_info = NodeLevel[self.level]
        karma_factor = max(0.1, self.karma / 100.0)  # Karma scales 0.1x to 1.0x
        return float(level_info.value["mass_multiplier"] * self.total_experience * karma_factor)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KarmaEventRecord:
    """Record of a single karma-affecting event."""
    node_id: str
    event_type: str
    karma_delta: int
    xp_delta: int
    timestamp: float
    reason: str
    severity: str  # "minor", "moderate", "severe"

    def to_dict(self) -> dict:
        return asdict(self)


class ZDXKarmaSystem:
    """
    Network-wide karma and reputation management.

    Tracks node behavior, issues suspensions and evictions, and computes
    election weight based on karma and experience level.
    """

    # Suspension lasts 24 hours, then auto-lifts
    SUSPENSION_DURATION = 86400  # seconds

    def __init__(self, persistence_path: str = ".zdx/karma.json"):
        """
        Initialize karma system.

        Args:
            persistence_path: Path to save karma state
        """
        self.persistence_path = Path(persistence_path)
        self.scores: Dict[str, KarmaScore] = {}
        self.event_log: Dict[str, List[dict]] = defaultdict(list)
        self.suspension_queue: Dict[str, float] = {}  # node_id -> suspension_time
        self.eviction_queue: set = set()  # node_ids pending eviction
        self.load()

    def report_event(
        self,
        node_id: str,
        event_type: KarmaEventType,
        reason: str,
        severity: str = "minor",
    ) -> KarmaScore:
        """
        Report an event and update karma.

        Args:
            node_id: Node that generated the event
            event_type: Type of event (KarmaEventType enum)
            reason: Human-readable reason
            severity: "minor", "moderate", "severe"

        Returns:
            Updated KarmaScore
        """
        if node_id not in self.scores:
            self.scores[node_id] = KarmaScore(node_id=node_id)

        score = self.scores[node_id]

        # Get karma and XP adjustments
        karma_delta = event_type.karma_delta()
        xp_delta = event_type.xp_delta()

        # Apply adjustment
        score.karma = max(0, min(100, score.karma + karma_delta))
        score.total_experience = max(0, score.total_experience + xp_delta)
        score.last_event_time = time.time()

        # Update level based on experience
        score.level = self._compute_level(score.total_experience)

        # Track violations for suspension
        if karma_delta < 0:
            score.consecutive_violations += 1

        # Check suspension/eviction thresholds
        if score.karma <= 0:
            self._suspend_node(node_id, reason)
        elif score.consecutive_violations >= 3 and severity == "severe":
            self._suspend_node(node_id, reason)

        if score.karma <= -50 or event_type == KarmaEventType.REVOCATION_VIOLATION:
            self._mark_eviction(node_id)

        # Log event
        event_record = KarmaEventRecord(
            node_id=node_id,
            event_type=event_type.value[0],
            karma_delta=karma_delta,
            xp_delta=xp_delta,
            timestamp=time.time(),
            reason=reason,
            severity=severity,
        )
        self.event_log[node_id].append(event_record.to_dict())

        self.save()
        return score

    def _compute_level(self, experience: int) -> str:
        """Compute node level from experience."""
        for level in [NodeLevel.MASTER, NodeLevel.SENTINEL, NodeLevel.VALIDATOR,
                      NodeLevel.CONTRIBUTOR, NodeLevel.TRUSTEE]:
            if experience >= level.value["min_xp"]:
                return level.name
        return NodeLevel.TRUSTEE.name

    def _suspend_node(self, node_id: str, reason: str) -> None:
        """Suspend a node for 24 hours."""
        if node_id not in self.scores:
            return

        score = self.scores[node_id]
        score.suspension_status = True
        score.suspension_time = time.time()

        self.suspension_queue[node_id] = score.suspension_time

        print(f"[KARMA] Node {node_id} SUSPENDED for 24 hours: {reason}")

    def _mark_eviction(self, node_id: str) -> None:
        """Mark node for permanent eviction."""
        if node_id not in self.scores:
            return

        score = self.scores[node_id]
        score.eviction_pending = True
        self.eviction_queue.add(node_id)

        print(f"[KARMA] Node {node_id} marked for EVICTION (karma: {score.karma})")

    def get_score(self, node_id: str) -> KarmaScore:
        """Get current karma score for a node."""
        if node_id not in self.scores:
            self.scores[node_id] = KarmaScore(node_id=node_id)
        return self.scores[node_id]

    def can_participate(self, node_id: str) -> Tuple[bool, str]:
        """
        Check if a node can participate in network operations.

        Returns:
            (can_participate, status_message)
        """
        score = self.get_score(node_id)

        if node_id in self.eviction_queue:
            return False, f"EVICTED"

        if score.is_suspended:
            remaining = self.SUSPENSION_DURATION - (time.time() - (score.suspension_time or time.time()))
            return False, f"SUSPENDED ({remaining:.0f}s remaining)"

        if score.karma <= 0:
            return False, f"CRITICAL KARMA (karma={score.karma})"

        return True, "ACTIVE"

    def get_master_node_candidates(self, cluster_size: int = 100) -> List[Tuple[str, float, str, int]]:
        """
        Get eligible master node candidates sorted by mass.

        Only MASTER level nodes with positive karma are eligible.
        Returns top candidate for a cluster of given size.

        Args:
            cluster_size: Cluster size (unused for now, extensible)

        Returns:
            List of (node_id, mass, level, karma) tuples, sorted by mass descending
        """
        candidates = []

        for node_id, score in self.scores.items():
            # Must be MASTER level
            if score.level != NodeLevel.MASTER.name:
                continue

            # Must not be suspended/evicted
            if score.is_suspended or score.eviction_pending:
                continue

            # Must have positive karma
            if score.karma <= 0:
                continue

            mass = score.mass
            candidates.append((node_id, mass, score.level, score.karma))

        # Sort by mass descending
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates

    def elect_master_node(self, cluster_nodes: List[str]) -> Optional[str]:
        """
        Elect master node for a cluster from eligible candidates.

        Uses weighted random selection based on mass, ensuring majority
        of voting weight can override a single dominant node (prevents autocracy).

        Args:
            cluster_nodes: List of node_ids in the cluster

        Returns:
            node_id of elected master, or None if no eligible candidates
        """
        candidates = []

        for node_id in cluster_nodes:
            score = self.get_score(node_id)

            # Must be MASTER level
            if score.level != NodeLevel.MASTER.name:
                continue

            # Must not be suspended/evicted
            if score.is_suspended or score.eviction_pending:
                continue

            # Must have positive karma
            if score.karma <= 0:
                continue

            candidates.append((node_id, score.mass))

        if not candidates:
            return None

        # Sort by mass
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Return highest mass node
        # (In production, could use weighted random or multi-round voting)
        return candidates[0][0]

    def get_cluster_stats(self, cluster_nodes: List[str]) -> dict:
        """Get aggregate statistics for a cluster."""
        stats = {
            "total_nodes": len(cluster_nodes),
            "active_nodes": 0,
            "suspended_nodes": 0,
            "evicted_nodes": 0,
            "total_mass": 0.0,
            "average_karma": 0.0,
            "average_level_rank": 0.0,
            "master_eligible": 0,
        }

        karma_sum = 0
        level_rank_sum = 0
        active_count = 0

        for node_id in cluster_nodes:
            score = self.get_score(node_id)
            can_participate, status = self.can_participate(node_id)

            if can_participate:
                stats["active_nodes"] += 1
                active_count += 1
                karma_sum += score.karma
                level_rank_sum += NodeLevel[score.level].value["rank"]
                stats["total_mass"] += score.mass

                if score.level == NodeLevel.MASTER.name:
                    stats["master_eligible"] += 1
            elif score.is_suspended:
                stats["suspended_nodes"] += 1
            elif node_id in self.eviction_queue:
                stats["evicted_nodes"] += 1

        if active_count > 0:
            stats["average_karma"] = karma_sum / active_count
            stats["average_level_rank"] = level_rank_sum / active_count

        return stats

    def save(self) -> None:
        """Persist karma state to disk."""
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "scores": {node_id: score.to_dict() for node_id, score in self.scores.items()},
            "event_log": dict(self.event_log),
            "suspension_queue": self.suspension_queue,
            "eviction_queue": list(self.eviction_queue),
            "saved_at": time.time(),
        }

        self.persistence_path.write_text(json.dumps(data, indent=2))

    def load(self) -> None:
        """Load karma state from disk."""
        if not self.persistence_path.exists():
            return

        try:
            data = json.loads(self.persistence_path.read_text())

            # Restore scores
            for node_id, score_dict in data.get("scores", {}).items():
                self.scores[node_id] = KarmaScore(**score_dict)

            # Restore event log
            self.event_log = defaultdict(list, data.get("event_log", {}))

            # Restore queues
            self.suspension_queue = data.get("suspension_queue", {})
            self.eviction_queue = set(data.get("eviction_queue", []))

        except Exception as e:
            print(f"[KARMA] Failed to load karma state: {e}")

    def print_leaderboard(self, top_n: int = 20) -> None:
        """Print top nodes by mass (for monitoring/debugging)."""
        ranked = sorted(
            [(node_id, score.mass, score.level, score.karma, score.total_experience)
             for node_id, score in self.scores.items()],
            key=lambda x: x[1],
            reverse=True,
        )

        print("\n=== KARMA LEADERBOARD ===")
        print(f"{'Rank':<6} {'Node ID':<12} {'Mass':<10} {'Level':<12} {'Karma':<7} {'XP':<6}")
        print("-" * 60)

        for i, (node_id, mass, level, karma, xp) in enumerate(ranked[:top_n], 1):
            print(
                f"{i:<6} {node_id:<12} {mass:>9.1f} {level:<12} {karma:>6} {xp:>5}"
            )

    def print_node_status(self, node_id: str) -> None:
        """Print detailed status for a node."""
        score = self.get_score(node_id)
        can_participate, status = self.can_participate(node_id)

        print(f"\n=== NODE STATUS: {node_id} ===")
        print(f"Status:          {status}")
        print(f"Karma:           {score.karma}/100")
        print(f"Experience:      {score.total_experience} XP")
        print(f"Level:           {score.level}")
        print(f"Mass:            {score.mass:.1f}")
        print(f"Violations:      {score.consecutive_violations}")
        print(f"Suspended:       {score.suspension_status}")

        if score.suspension_time:
            remaining = self.SUSPENSION_DURATION - (time.time() - score.suspension_time)
            print(f"Suspension Time: {max(0, remaining):.0f}s remaining")

        print(f"\nRecent Events:")
        for event in self.event_log.get(node_id, [])[-5:]:
            print(f"  {event['event_type']:20} {event['reason']}")
