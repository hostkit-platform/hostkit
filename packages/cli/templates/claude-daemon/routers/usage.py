"""Usage router - track API usage and quotas."""

from datetime import date, timedelta
from fastapi import APIRouter
from sqlalchemy import select, func

from dependencies import DB, CurrentProject
from models.usage import UsageTracking
from schemas.usage import UsageStats, DailyUsage, UsageLimits

router = APIRouter(prefix="/v1", tags=["Usage"])


@router.get("/usage")
async def get_usage(
    project: CurrentProject,
    db: DB,
):
    """Get usage statistics for the current project.

    Returns today's usage, this month's total, and current limits.
    """
    today = date.today()
    month_start = today.replace(day=1)

    # Get today's usage
    today_result = await db.execute(
        select(UsageTracking)
        .where(
            UsageTracking.project_name == project.project_name,
            UsageTracking.date == today,
        )
    )
    today_usage = today_result.scalar_one_or_none()

    # Get this month's totals
    month_result = await db.execute(
        select(
            func.sum(UsageTracking.requests).label("requests"),
            func.sum(UsageTracking.input_tokens).label("input_tokens"),
            func.sum(UsageTracking.output_tokens).label("output_tokens"),
            func.sum(UsageTracking.tool_calls).label("tool_calls"),
        )
        .where(
            UsageTracking.project_name == project.project_name,
            UsageTracking.date >= month_start,
        )
    )
    month_row = month_result.fetchone()

    # Calculate remaining tokens for today
    today_total = (today_usage.input_tokens + today_usage.output_tokens) if today_usage else 0
    remaining = max(0, project.daily_token_limit - today_total)

    return {
        "success": True,
        "data": UsageStats(
            today=DailyUsage(
                requests=today_usage.requests if today_usage else 0,
                input_tokens=today_usage.input_tokens if today_usage else 0,
                output_tokens=today_usage.output_tokens if today_usage else 0,
                tool_calls=today_usage.tool_calls if today_usage else 0,
            ),
            this_month=DailyUsage(
                requests=month_row.requests or 0,
                input_tokens=month_row.input_tokens or 0,
                output_tokens=month_row.output_tokens or 0,
                tool_calls=month_row.tool_calls or 0,
            ),
            limits=UsageLimits(
                daily_token_limit=project.daily_token_limit,
                remaining_tokens=remaining,
                rate_limit_rpm=project.rate_limit_rpm,
            ),
        ).model_dump()
    }
