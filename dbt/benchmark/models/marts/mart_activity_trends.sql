{{ config(materialized='table') }}

-- Dashboard Tile 2: Daily CT.gov Activity Trends (temporal distribution).
-- One row per (therapeutic_area, activity_date).
-- Looker Studio uses activity_date as the X-axis for the line chart,
-- with one line per therapeutic_area showing daily update volume over time.

select
    therapeutic_area,
    activity_date,
    sum(update_count)                                     as daily_updates,
    sum(industry_sponsored)                               as industry_updates,
    avg(avg_enrollment)                                   as avg_enrollment,
    safe_divide(sum(industry_sponsored), sum(update_count)) as industry_share,
    sum(completed_count)                                  as completed_count

from {{ ref('stg_daily_activity') }}

group by therapeutic_area, activity_date
order by activity_date desc, daily_updates desc
