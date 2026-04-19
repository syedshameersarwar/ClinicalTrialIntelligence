{{ config(materialized='view') }}

with source as (

    select * from {{ source('streaming', 'daily_activity') }}

),

cleaned as (

    select
        window_start,
        window_end,
        therapeutic_area,
        update_count,
        industry_sponsored,
        avg_enrollment,
        completed_count,

        -- derived
        safe_divide(industry_sponsored, update_count) as industry_share,
        date(window_start)                            as activity_date

    from source
    where therapeutic_area is not null
      and update_count > 0

)

select * from cleaned
