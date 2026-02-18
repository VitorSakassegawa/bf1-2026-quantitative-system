"""ORM models – import all models so Alembic can detect them."""

from app.models.user import User
from app.models.circuit import Circuit
from app.models.team import Team
from app.models.driver import Driver
from app.models.race import Race
from app.models.qualifying_result import QualifyingResult
from app.models.race_result import RaceResult
from app.models.sprint_result import SprintResult
from app.models.weather import Weather
from app.models.elo_history import ELOHistory
from app.models.bet import Bet
from app.models.simulation import Simulation
from app.models.prediction import Prediction

__all__ = [
    "User",
    "Circuit",
    "Team",
    "Driver",
    "Race",
    "QualifyingResult",
    "RaceResult",
    "SprintResult",
    "Weather",
    "ELOHistory",
    "Bet",
    "Simulation",
    "Prediction",
]
