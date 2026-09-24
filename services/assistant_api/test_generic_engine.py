"""Include the generic runner acceptance suite in backend coverage."""

from services.forecast_engine.test_engine import GenericEngineTests

__all__ = ["GenericEngineTests"]
