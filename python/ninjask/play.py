"""Play one game between two policies in the reference simulator."""

from __future__ import annotations

from dataclasses import dataclass, field

from ninjask.policy import Policy
from ninjask.refsim import Battle, Observation, RefSim

TURN_LIMIT = 150  # same tie rule as PKMN Arena
MAX_RETRIES = 25


@dataclass
class GameResult:
    seed: str
    winner: str | None  # "p1", "p2" or None for a tie
    turns: int
    inputlog: list[str]
    spectator_log: list[str] = field(default_factory=list)
    rejected_choices: dict[str, int] = field(default_factory=lambda: {"p1": 0, "p2": 0})
    rejections: list[tuple[str, str, str]] = field(default_factory=list)  # (side, choice, error)


def play_game(
    sim: RefSim,
    format_id: str,
    seed: str,
    team1: str,
    team2: str,
    policy1: Policy,
    policy2: Policy,
    ots: bool = False,
) -> GameResult:
    policies = {"p1": policy1, "p2": policy2}
    names = {"p1": "Player 1", "p2": "Player 2"}
    battle = Battle(sim, format_id, seed, (names["p1"], team1), (names["p2"], team2), ots=ots)
    pending: dict[str, list[str]] = {"p1": [], "p2": []}
    spectator: list[str] = []
    rejected = {"p1": 0, "p2": 0}
    rejections: list[tuple[str, str, str]] = []
    # The latest unanswered request per side (the simulator sends each once).
    requests: dict[str, dict | None] = {"p1": None, "p2": None}

    def absorb(o: Observation, take_requests: bool = True) -> None:
        for s in ("p1", "p2"):
            pending[s].extend(o.logs[s])
            if take_requests and o.requests[s] is not None:
                requests[s] = o.requests[s]
        spectator.extend(o.logs["spectator"])

    obs = battle.start()
    absorb(obs)
    while not obs.ended and obs.turn < TURN_LIMIT:
        acted = False
        for side in ("p1", "p2"):
            req = requests[side]
            if req is None or req.get("wait"):
                continue
            given = 0  # lines already passed to the policy (a retry gets only newer ones)
            for attempt in range(MAX_RETRIES + 1):
                if attempt < MAX_RETRIES:
                    choice = policies[side].choose(req, pending[side][given:])
                    given = len(pending[side])
                else:
                    choice = "default"
                obs = battle.choose(side, choice)
                if obs.accepted or obs.errors[side] is None:
                    break
                rejected[side] += 1
                rejections.append((side, choice, obs.errors[side] or ""))
                # An "Unavailable choice" error comes with an updated request.
                if obs.requests[side] is not None:
                    req = obs.requests[side]
                absorb(obs, take_requests=False)
            pending[side] = []
            requests[side] = None
            absorb(obs)
            acted = True
            if obs.ended:
                break
        if not acted:
            raise RuntimeError("no side can act but the battle has not ended")
    inputlog = battle.inputlog()
    battle.close()
    winner = None
    if obs.ended and obs.winner:
        winner = "p1" if obs.winner == names["p1"] else "p2"
    return GameResult(seed, winner, obs.turn, inputlog, spectator, rejected, rejections)
