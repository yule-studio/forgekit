"""Product-intake gate — raw user ask → structured ProductIntentPacket.

The product-manager's pre-engineering shaping: feature-family gap audit, ≤3
decision questions, auto-filled defaults, acceptance criteria, readiness verdict.
Pure/stdlib; the gateway/discussion layer consumes the packet.
"""
from .models import ProductIntentPacket, ProductReadinessVerdict, DecisionQuestion, FeatureGap  # noqa: F401
from .shaping import shape_product_intent  # noqa: F401
