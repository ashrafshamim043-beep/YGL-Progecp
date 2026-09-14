"""
Builds the locked, approved CommercialAllocationPlanVersion.
Locked business decision:
 Product Cost 40% + Marketing 4.5% + Courier/Logistics 3.5% +
 Payment/System 2% + Company Profit 5% + Phase7 Compensation 35% +
 Rank Reward Pool 10% = 100%
"""
from decimal import Decimal
from .approved_plan import build_approved_plan_v1
from .approved_rank_reward_plan import build_approved_rank_reward_plan_v1
from .commercial_allocation import (
 CommercialAllocationPlanVersion,
 validate_and_publish_commercial_allocation,
)
def build_approved_commercial_allocation_v1() -> CommercialAllocationPlanVersion:
 phase7_plan = build_approved_plan_v1()
 reward_plan = build_approved_rank_reward_plan_v1()
 draft = CommercialAllocationPlanVersion(
 commercial_plan_version_id="COMMERCIAL_ALLOCATION_V1_2026",
 is_published=False,
 product_cost_rate=Decimal("40.00"),
 marketing_rate=Decimal("4.50"),
 courier_logistics_rate=Decimal("3.50"),
 payment_system_other_rate=Decimal("2.00"),
 company_profit_rate=Decimal("5.00"),
 phase7_plan_version=phase7_plan,
 rank_reward_pool_plan_version=reward_plan,
 )
 return validate_and_publish_commercial_allocation(draft)