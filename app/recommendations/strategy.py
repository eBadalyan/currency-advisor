from __future__ import annotations

from abc import ABC, abstractmethod

from app.recommendations.models import Recommendation, RecommendationContext


class RecommendationStrategy(ABC):
    """Turns an already-assembled RecommendationContext into a Recommendation.

    Synchronous and side-effect free by design: all I/O (fetching rates,
    computing indicators) happens in RecommendationService before a
    strategy is ever called. A future ML-based strategy is just a second
    implementation of this same contract — a model doing inference over
    already-extracted features needs no I/O at evaluate() time either.
    """

    @abstractmethod
    def evaluate(self, context: RecommendationContext) -> Recommendation:
        raise NotImplementedError
