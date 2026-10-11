"""Component 00 — Foundation: the epistemic model.

No I/O, no credentials, no domain logic. Everything else depends on this; it
depends on nothing. That inversion is deliberate: in v1 the provenance scheme
lived in a markdown file and the code was free to ignore it.
"""

from .bundle import FactBundle, ParityError, assemble, assert_parity
from .claims import (
    Claim,
    ClaimCategory,
    ClaimError,
    ClaimsRegister,
    lint_paths,
    lint_public_text,
)
from .contradiction import (
    AuthorityPrecedence,
    Contradiction,
    ContradictionRef,
    Resolution,
)
from .fact import Fact, FactReference, ReviewState
from .manifest import RunManifest, design_hash
from .outcome import (
    RuleOutcome,
    RuleResult,
    Severity,
    Timings,
    Verdict,
    assert_exhaustive,
)
from .stamp import (
    Confidence,
    ProvenanceStamp,
    ResolutionMethod,
    Tier,
    asserted,
    derived,
    ingested,
    runtime,
)
from .temporal import StalenessPolicy, ValidityInterval, days
from .trust import (
    EligibilityVerdict,
    IneligibilityReason,
    TrustPolicy,
    WithheldFact,
    is_eligible_rule_input,
)

__all__ = [
    # stamp
    "Tier", "ResolutionMethod", "Confidence", "ProvenanceStamp",
    "asserted", "ingested", "derived", "runtime",
    # temporal
    "ValidityInterval", "StalenessPolicy", "days",
    # contradiction
    "Contradiction", "ContradictionRef", "Resolution", "AuthorityPrecedence",
    # fact
    "Fact", "FactReference", "ReviewState",
    # trust
    "TrustPolicy", "IneligibilityReason", "EligibilityVerdict", "WithheldFact",
    "is_eligible_rule_input",
    # bundle
    "FactBundle", "assemble", "assert_parity", "ParityError",
    # outcome
    "RuleOutcome", "RuleResult", "Severity", "Verdict", "Timings", "assert_exhaustive",
    # claims
    "Claim", "ClaimCategory", "ClaimsRegister", "ClaimError",
    "lint_public_text", "lint_paths",
    # manifest
    "RunManifest", "design_hash",
]
