--- segment
name: [CAMPAIGN NAME] CC_00. Content Total (Visit)
rsid: sscompany_name4mstglobal

visit(
  hit(
    @s200000000_YOUR_SEGMENT_ID
    AND
    (
      '[CAMPAIGN NAME] CC_00. Content Total (Visit)'!hit(
        hit(
          'evar OR group'!hit(
            'v25'!hit(
              evar25 exists
              AND
              event25 event-exists
            )
            OR
            'v26'!hit(
              evar26 exists
              AND
              event26 event-exists
            )
            OR
            'v35'!hit(
              evar35 exists
              AND
              event35 event-exists
            )
          )
          AND
          not 'site'!hit(
            evar80 equals 'WidgetView'
          )
        )
      )
    )
  )
)

===

--- segment
name: [CAMPAIGN NAME] CC_00. Content Total
rsid: sscompany_name4mstglobal

hit(
  '[CAMPAIGN NAME] CC_00. Content Total'!hit(
    hit(
      'evar OR group'!hit(
        'v25'!hit(
          evar25 exists
          AND
          event25 event-exists
        )
        OR
        'v26'!hit(
          evar26 exists
          AND
          event26 event-exists
        )
        OR
        'v35'!hit(
          evar35 exists
          AND
          event35 event-exists
        )
      )
      AND
      not 'site'!hit(
        evar80 equals 'WidgetView'
      )
    )
  )
)

===

--- segment
name: [CAMPAIGN NAME] CC_00. Content Total (Delayed Purchase)
rsid: sscompany_name4mstglobal

hit(
  visit(
    '[CAMPAIGN NAME] CC_00. Content Total'!hit(
      @s200000000_YOUR_SEGMENT_ID
      AND
      'evar OR group'!hit(
        'v25'!hit(
          evar25 exists
          AND
          event25 event-exists
        )
        OR
        'v26'!hit(
          evar26 exists
          AND
          event26 event-exists
        )
        OR
        'v35'!hit(
          evar35 exists
          AND
          event35 event-exists
        )
      )
      AND
      not 'site'!hit(
        evar80 equals 'WidgetView'
      )
    )
    THEN
    '[Global] Add to Cart Visit'!hit(
      @YOUR_SEGMENT2_ID
    )
    AND
    hit(
      NOT orders event-exists
    )
  )
  THEN
  visit(
    orders event-exists
  )
)
