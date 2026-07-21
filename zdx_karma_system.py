"""
ZDX Karma System: Node Reputation, Experience Decay, and Cluster Leadership.

Implements a self-governing network where:
- Nodes earn karma through trustworthy behavior and active compute participation
- Experience DECAYS if inactive (prevents coasting at high levels)
- Multiple independent 100-node clusters can operate simultaneously
- Nodes can work across multiple clusters for workload balancing
- High-level nodes compete for master node election within their cluster
- Karmic system enforces continuous participation to maintain status

Karma categories:
- POSITIVE: valid contributions, uptime, peer validation, active compute cycles
- NEGATIVE: failed auth, replay attacks, revocation, DoS attempts
- DECAY: continuous experience loss if node is inactive

Levels: TRUSTEE (0-100 XP) → CONTRIBUTOR (100-1K) → VALIDATOR (1K-10K)
        → SENTINEL (10K-100K) → MASTER (100K+)

Each level increases gravitational mass multiplier. Decay prevents
nodes from staying at high levels without active participation.

Architecture:
- Multiple clusters (100 nodes each) operate independently
- Nodes can join multiple clusters simultaneously
- Master node elected from highest-mass nodes in each cluster
- Workload distribution across clusters reduces training wait times
- Decay forces continuous network participation
"""

from __future__ import annotations

import json
import time
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from enum import Enum
from typing import Optional, Dict, Tuple, List, Set
from collections import defaultdict


class KarmaEvent(Enum):
    """Types of events that affect karma."""
    # Positive events (active participation)
    VALID_CONTRIBUTION = "valid_contribution"  # +10 karma, +15 XP
    CORRECT_COMPUTATION = "correct_computation"  # +5 karma, +10 XP
    COMPUTE_CYCLE_COMPLETED = "compute_cycle_completed"  # +8 karma, +20 XP (high value)
    UPTIME_HEARTBEAT = "uptime_heartbeat"  # +1 karma, +1 XP
    PEER_VALIDATION = "peer_validation"  # +3 karma, +5 XP
    FRAME_SYNC_SUCCESS = "frame_sync_success"  # +2 karma, +3 XP

    # Negative events
    AUTH_FAILURE = "auth_failure"  # -5 karma, 0 XP
    REPLAY_ATTACK_ATTEMPT = "replay_attack"  # -20 karma, -10 XP
    SIGNATURE_FORGERY = "signature_forgery"  # -50 karma, -25 XP
    DOS_ATTEMPT = "dos_attempt"  # -30 karma, -15 XP
    REVOCATION_VIOLATION = "revocation_violation"  # -100 karma, eviction
    MALFORMED_PACKET = "malformed_packet"  # -3 karma, 0 XP
    TIMEOUT_VIOLATION = "timeout_violation"  # -2 karma, 0 XP
    ENROLLMENT_ABUSE = "enrollment_abuse"  # -25 karma, -10 XP
    COMPUTATION_FRAUD = "computation_fraud"  # -40 karma, -20 XP
    STATE_CORRUPTION = "state_corruption"  # -60 karma, -30 XP

    # Neutral/decay events
    HEARTBEAT = "heartbeat"  # 0 karma, 0 XP
    ROUTINE_OPERATION = "routine_operation"  # 0 karma, 0 XP
    INACTIVITY_DECAY = "inactivity_decay"  # 0 karma, -XP (see decay calculation)


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
class ClusterMembership:
    """Node's membership in a cluster."""
    cluster_id: str
    joined_at: float
    last_active: float
    cycles_completed: int = 0
    cycles_in_progress: int = 0


@dataclass
class KarmaScore:
    """Current karma and experience state of a node."""
    node_id: str
    karma: int = 50  # Starting neutral (0-100 scale)
    total_experience: int = 0
    level: str = "TRUSTEE"
    last_event_time: float = 0.0
    last_activity_time: float = 0.0  # Tracks inactivity for decay
    suspension_status: bool = False
    suspension_time: Optional[float] = None
    eviction_pending: bool = False
    consecutive_violations: int = 0
    cluster_memberships: Dict[str, ClusterMembership] = field(default_factory=dict)

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
        data = asdict(self)
        # Convert cluster memberships to serializable format
        data["cluster_memberships"] = {
            cid: asdict(cm) for cid, cm in self.cluster_memberships.items()
        }
        return data


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


# Karma adjustments per event type
KARMA_ADJUSTMENTS = {
    KarmaEvent.VALID_CONTRIBUTION: {"karma": 10, "xp": 15},
    KarmaEvent.CORRECT_COMPUTATION: {"karma": 5, "xp": 10},
    KarmaEvent.COMPUTE_CYCLE_COMPLETED: {"karma": 8, "xp": 20},  # Most valuable
    KarmaEvent.UPTIME_HEARTBEAT: {"karma": 1, "xp": 1},
    KarmaEvent.PEER_VALIDATION: {"karma": 3, "xp": 5},
    KarmaEvent.FRAME_SYNC_SUCCESS: {"karma": 2, "xp": 3},
    KarmaEvent.AUTH_FAILURE: {"karma": -5, "xp": 0},
    KarmaEvent.REPLAY_ATTACK_ATTEMPT: {"karma": -20, "xp": -10},
    KarmaEvent.SIGNATURE_FORGERY: {"karma": -50, "xp": -25},
    KarmaEvent.DOS_ATTEMPT: {"karma": -30, "xp": -15},
    KarmaEvent.REVOCATION_VIOLATION: {"karma": -100, "xp": 0},
    KarmaEvent.MALFORMED_PACKET: {"karma": -3, "xp": 0},
    KarmaEvent.TIMEOUT_VIOLATION: {"karma": -2, "xp": 0},
    KarmaEvent.ENROLLMENT_ABUSE: {"karma": -25, "xp": -10},
    KarmaEvent.COMPUTATION_FRAUD: {"karma": -40, "xp": -20},
    KarmaEvent.STATE_CORRUPTION: {"karma": -60, "xp": -30},
}

# Decay parameters
INACTIVITY_DECAY_PERIOD = 604800  # 7 days - triggers decay check
INACTIVITY_DECAY_RATE = 0.15  # 15% XP loss per decay period if inactive


class ZDXKarmaSystem:
    """
    Network-wide karma and reputation management with experience decay.

    Tracks node behavior, issues suspensions and evictions, manages
    multi-cluster membership, and enforces experience decay for inactive nodes.
    """

    SUSPENSION_DURATION = 86400  # 24 hours
    CLUSTER_SIZE = 100  # Standard cluster size

    def __init__(self, persistence_path: str = ".zdx/karma.json"):
        """
        Initialize karma system.

        Args:
            persistence_path: Path to save karma state
        """
        self.persistence_path = Path(persistence_path)
        self.scores: Dict[str, KarmaScore] = {}
        self.event_log: Dict[str, list] = defaultdict(list)
        self.suspension_queue: Dict[str, float] = {}
        self.eviction_queue: set = set()
        self.clusters: Dict[str, Set[str]] = defaultdict(set)  # cluster_id -> set of node_ids
        self.load()

    def report_event(
        self,
        node_id: str,
        event_type: KarmaEvent,
        reason: str,
        severity: str = "minor",
    ) -> KarmaScore:
        """
        Report an event and update karma.

        Args:
            node_id: Node that generated the event
            event_type: Type of event (KarmaEvent enum)
            reason: Human-readable reason
            severity: "minor", "moderate", "severe"

        Returns:
            Updated KarmaScore
        """
        if node_id not in self.scores:
            self.scores[node_id] = KarmaScore(node_id=node_id)

        score = self.scores[node_id]

        # Get karma adjustment
        adjustment = KARMA_ADJUSTMENTS.get(event_type, {"karma": 0, "xp": 0})
        karma_delta = adjustment["karma"]
        xp_delta = adjustment["xp"]

        # Apply adjustment
        score.karma = max(0, min(100, score.karma + karma_delta))
        score.total_experience = max(0, score.total_experience + xp_delta)
        score.last_event_time = time.time()
        score.last_activity_time = time.time()  # Reset inactivity timer

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

        if score.karma <= -50 or event_type == KarmaEvent.REVOCATION_VIOLATION:
            self._mark_eviction(node_id)

        # Log event
        event_record = KarmaEventRecord(
            node_id=node_id,
            event_type=event_type.value,
            karma_delta=karma_delta,
            xp_delta=xp_delta,
            timestamp=time.time(),
            reason=reason,
            severity=severity,
        )
        self.event_log[node_id].append(event_record.to_dict())

        self.save()
        return score

    def apply_inactivity_decay(self, node_id: str) -> int:
        """
        Apply experience decay for inactive nodes.

        Nodes lose XP if they haven't participated in compute cycles
        within the decay period. This prevents coasting at high levels.

        Args:
            node_id: Node to decay

        Returns:
            XP lost (0 if node is active)
        """
        if node_id not in self.scores:
            return 0

        score = self.scores[node_id]
        now = time.time()
        time_since_activity = now - score.last_activity_time

        # No decay if recently active
        if time_since_activity < INACTIVITY_DECAY_PERIOD:
            return 0

        # Calculate decay periods elapsed
        decay_periods = int(time_since_activity / INACTIVITY_DECAY_PERIOD)

        # Apply exponential decay
        xp_before = score.total_experience
        for _ in range(decay_periods):
            xp_lost = int(score.total_experience * INACTIVITY_DECAY_RATE)
            score.total_experience = max(0, score.total_experience - xp_lost)

        actual_loss = xp_before - score.total_experience

        # Update level
        score.level = self._compute_level(score.total_experience)

        if actual_loss > 0:
            # Log decay event
            event_record = KarmaEventRecord(
                node_id=node_id,
                event_type=KarmaEvent.INACTIVITY_DECAY.value,
                karma_delta=0,
                xp_delta=-actual_loss,
                timestamp=now,
                reason=f"Inactivity decay ({decay_periods} period(s), {INACTIVITY_DECAY_RATE*100:.0f}% loss)",
                severity="moderate",
            )
            self.event_log[node_id].append(event_record.to_dict())

            print(
                f"[DECAY] Node {node_id} lost {actual_loss} XP due to inactivity "
                f"({score.level})"
            )

            self.save()

        return actual_loss

    def register_cluster_activity(
        self,
        node_id: str,
        cluster_id: str,
        cycle_completed: bool = False,
    ) -> None:
        """
        Register a node's activity in a cluster.

        Args:
            node_id: Node ID
            cluster_id: Cluster ID
            cycle_completed: Whether a compute cycle was completed
        """
        if node_id not in self.scores:
            self.scores[node_id] = KarmaScore(node_id=node_id)

        score = self.scores[node_id]

        # Add to cluster if not already member
        if cluster_id not in score.cluster_memberships:
            score.cluster_memberships[cluster_id] = ClusterMembership(
                cluster_id=cluster_id,
                joined_at=time.time(),
                last_active=time.time(),
            )
            self.clusters[cluster_id].add(node_id)

        # Update cluster membership
        membership = score.cluster_memberships[cluster_id]
        membership.last_active = time.time()

        if cycle_completed:
            membership.cycles_completed += 1
            # Report compute cycle completion
            self.report_event(
                node_id,
                KarmaEvent.COMPUTE_CYCLE_COMPLETED,
                f"Completed compute cycle in cluster {cluster_id}",
                severity="minor",
            )

        # Reset activity timer
        score.last_activity_time = time.time()

    def join_cluster(self, node_id: str, cluster_id: str) -> bool:
        """
        Add node to a cluster.

        Args:
            node_id: Node to add
            cluster_id: Cluster to join

        Returns:
            True if joined, False if cluster is full or node is banned
        """
        if node_id not in self.scores:
            self.scores[node_id] = KarmaScore(node_id=node_id)

        score = self.scores[node_id]

        # Check if node can participate
        can_participate, _ = self.can_participate(node_id)
        if not can_participate:
            return False

        # Check cluster capacity
        if len(self.clusters[cluster_id]) >= self.CLUSTER_SIZE:
            return False

        # Add to cluster
        if cluster_id not in score.cluster_memberships:
            score.cluster_memberships[cluster_id] = ClusterMembership(
                cluster_id=cluster_id,
                joined_at=time.time(),
                last_active=time.time(),
            )
            self.clusters[cluster_id].add(node_id)
            self.save()
            return True

        return True  # Already a member

    def leave_cluster(self, node_id: str, cluster_id: str) -> bool:
        """
        Remove node from a cluster.

        Args:
            node_id: Node to remove
            cluster_id: Cluster to leave

        Returns:
            True if left, False if not a member
        """
        if node_id not in self.scores:
            return False

        score = self.scores[node_id]

        if cluster_id in score.cluster_memberships:
            del score.cluster_memberships[cluster_id]
            self.clusters[cluster_id].discard(node_id)
            self.save()
            return True

        return False

    def get_cluster_info(self, cluster_id: str) -> dict:
        """Get information about a cluster."""
        nodes = self.clusters.get(cluster_id, set())

        stats = {
            "cluster_id": cluster_id,
            "node_count": len(nodes),
            "capacity": self.CLUSTER_SIZE,
            "utilization": len(nodes) / self.CLUSTER_SIZE,
            "active_nodes": 0,
            "total_mass": 0.0,
            "average_karma": 0,
        }

        karma_sum = 0
        for node_id in nodes:
            score = self.get_score(node_id)
            can_participate, _ = self.can_participate(node_id)
            if can_participate:
                stats["active_nodes"] += 1
                stats["total_mass"] += score.mass
                karma_sum += score.karma

        if stats["active_nodes"] > 0:
            stats["average_karma"] = karma_sum // stats["active_nodes"]

        return stats

    def _compute_level(self, experience: int) -> str:
        """Compute node level from experience."""
        for level in [
            NodeLevel.MASTER,
            NodeLevel.SENTINEL,
            NodeLevel.VALIDATOR,
            NodeLevel.CONTRIBUTOR,
            NodeLevel.TRUSTEE,
        ]:
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

        # Remove from all clusters
        for cluster_id in list(score.cluster_memberships.keys()):
            self.leave_cluster(node_id, cluster_id)

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
            return False, "EVICTED"

        if score.is_suspended:
            remaining = 86400 - (time.time() - (score.suspension_time or time.time()))
            return False, f"SUSPENDED ({remaining:.0f}s remaining)"

        if score.karma <= 0:
            return False, f"CRITICAL KARMA (karma={score.karma})"

        return True, "ACTIVE"

    def get_master_node_candidates(
        self, cluster_id: str, top_n: int = 10
    ) -> list:
        """
        Get eligible master node candidates for a cluster sorted by mass.

        Only MASTER level nodes with positive karma are eligible.

        Args:
            cluster_id: Cluster ID
            top_n: Number of top candidates to return

        Returns:
            List of (node_id, mass, level, karma) tuples, sorted by mass descending
        """
        candidates = []
        nodes = self.clusters.get(cluster_id, set())

        for node_id in nodes:
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

            mass = score.mass
            candidates.append((node_id, mass, score.level, score.karma))

        # Sort by mass descending
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_n]

    def elect_master_node(self, cluster_id: str) -> Optional[str]:
        """
        Elect master node for a cluster from eligible candidates.

        Args:
            cluster_id: Cluster ID

        Returns:
            node_id of elected master, or None if no eligible candidates
        """
        candidates = self.get_master_node_candidates(cluster_id, top_n=100)

        if not candidates:
            return None

        # Return highest mass node
        return candidates[0][0]

    def save(self) -> None:
        """Persist karma state to disk."""
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "scores": {node_id: score.to_dict() for node_id, score in self.scores.items()},
            "event_log": dict(self.event_log),
            "suspension_queue": self.suspension_queue,
            "eviction_queue": list(self.eviction_queue),
            "clusters": {cid: list(nids) for cid, nids in self.clusters.items()},
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
                memberships = score_dict.pop("cluster_memberships", {})
                score = KarmaScore(**score_dict)
                # Restore cluster memberships
                for cid, cm_dict in memberships.items():
                    score.cluster_memberships[cid] = ClusterMembership(**cm_dict)
                self.scores[node_id] = score

            # Restore event log
            self.event_log = defaultdict(list, data.get("event_log", {}))

            # Restore queues
            self.suspension_queue = data.get("suspension_queue", {})
            self.eviction_queue = set(data.get("eviction_queue", []))

            # Restore clusters
            for cid, nids in data.get("clusters", {}).items():
                self.clusters[cid] = set(nids)

        except Exception as e:
            print(f"[KARMA] Failed to load karma state: {e}")

    def print_leaderboard(self, top_n: int = 20) -> None:
        """Print top nodes by mass."""
        ranked = sorted(
            [
                (node_id, score.mass, score.level, score.karma, score.total_experience)
                for node_id, score in self.scores.items()
            ],
            key=lambda x: x[1],
            reverse=True,
        )

        print("\n=== KARMA LEADERBOARD ===")
        print(
            f"{'Rank':<6} {'Node ID':<12} {'Mass':<10} {'Level':<12} {'Karma':<7} {'XP':<6}"
        )
        print("-" * 60)

        for i, (node_id, mass, level, karma, xp) in enumerate(ranked[:top_n], 1):
            print(f"{i:<6} {node_id:<12} {mass:>9.1f} {level:<12} {karma:>6} {xp:>5}")

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
            remaining = 86400 - (time.time() - score.suspension_time)
            print(f"Suspension Time: {max(0, remaining):.0f}s remaining")

        print(f"\nCluster Memberships:")
        for cid, membership in score.cluster_memberships.items():
            print(
                f"  {cid}: cycles_done={membership.cycles_completed}, "
                f"last_active={membership.last_active}"
            )

        print(f"\nRecent Events:")
        for event in self.event_log.get(node_id, [])[-5:]:
            print(f"  {event['event_type']:20} {event['reason']}")

    def print_cluster_leaderboard(self, cluster_id: str, top_n: int = 20) -> None:
        """Print top nodes in a specific cluster."""
        nodes = self.clusters.get(cluster_id, set())

        ranked = sorted(
            [
                (node_id, self.get_score(node_id).mass, self.get_score(node_id).level)
                for node_id in nodes
            ],
            key=lambda x: x[1],
            reverse=True,
        )

        print(f"\n=== CLUSTER {cluster_id} LEADERBOARD ===")
        print(f"{'Rank':<6} {'Node ID':<12} {'Mass':<10} {'Level':<12}")
        print("-" * 45)

        for i, (node_id, mass, level) in enumerate(ranked[:top_n], 1):
            print(f"{i:<6} {node_id:<12} {mass:>9.1f} {level:<12}")
