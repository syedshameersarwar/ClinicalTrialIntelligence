{{ config(materialized='view') }}

with source as (

    select * from {{ source('raw', 'trials') }}

),

-- Normalize raw AACT phase codes (uppercase e.g. PHASE1, PHASE1/PHASE2, EARLY_PHASE1)
-- to readable labels. NULL / 'NA' / '' / unknown → 'Not Applicable'.
phase_clean as (

    select
        *,
        case
            when phase = 'EARLY_PHASE1'  then 'Early Phase 1'
            when phase = 'PHASE1'        then 'Phase 1'
            when phase = 'PHASE2'        then 'Phase 2'
            when phase = 'PHASE3'        then 'Phase 3'
            when phase = 'PHASE4'        then 'Phase 4'
            when phase = 'PHASE1/PHASE2' then 'Phase 1/2'
            when phase = 'PHASE2/PHASE3' then 'Phase 2/3'
            when phase = 'PHASE3/PHASE4' then 'Phase 3/4'
            else 'Not Applicable'
        end as phase_label
    from source

),

renamed as (

    select
        -- identifiers
        nct_id,
        brief_title,

        -- status
        overall_status,
        case
            when overall_status in (
                'Recruiting', 'Not yet recruiting',
                'Active, not recruiting', 'Enrolling by invitation'
            ) then 'Active'
            when overall_status = 'Completed'                   then 'Completed'
            when overall_status in ('Terminated', 'Withdrawn')  then 'Discontinued'
            else 'Other'
        end                                     as status_category,

        -- study design
        phase_label                             as phase,
        case
            when phase_label like '%Phase 1%' then 1
            when phase_label like '%Phase 2%' then 2
            when phase_label like '%Phase 3%' then 3
            when phase_label like '%Phase 4%' then 4
            else null
        end                                     as phase_numeric,
        study_type,
        is_interventional,

        -- sponsorship
        sponsor_class,
        lead_sponsor_name,

        -- enrollment
        enrollment,

        -- dates
        start_date,
        completion_date,
        first_posted_date,
        start_year,

        -- classification
        therapeutic_area,

        -- derived
        is_completed,
        trial_duration_days

    from phase_clean
    where nct_id is not null

)

select * from renamed
