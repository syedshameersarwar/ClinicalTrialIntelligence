{{
    config(
        materialized='table',
        partition_by={
            'field': 'start_year',
            'data_type': 'int64',
            'range': {
                'start': 2000,
                'end': 2030,
                'interval': 1
            }
        },
        cluster_by=['therapeutic_area', 'phase']
    )
}}

-- Dashboard Tile 1: Trial Landscape by Therapeutic Area (categorical distribution).
-- Aggregated at (therapeutic_area, start_year, phase, sponsor_class, status_category).
-- Partitioned by start_year + clustered by therapeutic_area + phase for query performance.

select
    therapeutic_area,
    start_year,
    phase,
    phase_numeric,
    sponsor_class,
    status_category,
    count(*)                                         as trial_count,
    safe_divide(countif(is_completed), count(*))     as completion_rate,
    avg(enrollment)                                  as avg_enrollment,
    avg(trial_duration_days)                         as avg_duration_days,
    sum(enrollment)                                  as total_planned_enrollment

from {{ ref('stg_trials') }}
where start_year >= 2010
  and therapeutic_area is not null

group by
    therapeutic_area,
    start_year,
    phase,
    phase_numeric,
    sponsor_class,
    status_category
