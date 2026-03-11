"""
Schemas package — re-exports all schema classes for backward compatibility.
"""

from anchor.extract.schemas.v5 import (
    ExtractedFact,
    ExtractedAssumption,
    ExtractedImplicitCondition,
    ExtractedConclusion,
    ExtractedPrediction,
    ExtractedSolution,
    ExtractedTheory,
    ExtractedRelationship,
    ExtractionResult,
    RawClaim,
    RawEdge,
    Step1Result,
    MergeGroup,
    Step2Result,
    ClassifiedEntity,
    Step3Result,
    ImplicitConditionItem,
    Step4Result,
)

from anchor.extract.schemas.policy import (
    PolicyItem,
    PolicyThemeItem,
    Step1PolicyResult,
    PolicyChangeAnnotation,
    PolicyComparisonResult,
    PolicyMeasureSchema,
    PolicySchema,
    PolicyExtractionResult,
)

from anchor.extract.schemas.v6 import (
    CoreConclusion,
    KeyTheory,
    TopDownAnchorsResult,
    SupportingFact,
    SubConclusion,
    SupportingAssumption,
    SupportingPrediction,
    SupportingSolution,
    SupportingScanResult,
    TypedEntity,
    AbstractedResult,
    MergeDecision,
    MergedResult,
    TypedEdge,
    RelationshipResult,
)

from anchor.extract.schemas.industry import (
    ExtractedPlayer,
    ExtractedSupplyNode,
    IndustryContextResult,
    ExtractedIssue,
    ExtractedTechRoute,
    ExtractedMetric,
    IndustryEntitiesResult,
    IndustryEdge,
    IndustryRelationshipResult,
)

__all__ = [
    # v5
    "ExtractedFact", "ExtractedAssumption", "ExtractedImplicitCondition",
    "ExtractedConclusion", "ExtractedPrediction", "ExtractedSolution",
    "ExtractedTheory", "ExtractedRelationship", "ExtractionResult",
    "RawClaim", "RawEdge", "Step1Result", "MergeGroup", "Step2Result",
    "ClassifiedEntity", "Step3Result", "ImplicitConditionItem", "Step4Result",
    # policy
    "PolicyItem", "PolicyThemeItem", "Step1PolicyResult",
    "PolicyChangeAnnotation", "PolicyComparisonResult",
    "PolicyMeasureSchema", "PolicySchema", "PolicyExtractionResult",
    # v6
    "CoreConclusion", "KeyTheory", "TopDownAnchorsResult",
    "SupportingFact", "SubConclusion", "SupportingAssumption",
    "SupportingPrediction", "SupportingSolution", "SupportingScanResult",
    "TypedEntity", "AbstractedResult", "MergeDecision", "MergedResult",
    "TypedEdge", "RelationshipResult",
    # industry
    "ExtractedPlayer", "ExtractedSupplyNode", "IndustryContextResult",
    "ExtractedIssue", "ExtractedTechRoute", "ExtractedMetric",
    "IndustryEntitiesResult", "IndustryEdge", "IndustryRelationshipResult",
]
